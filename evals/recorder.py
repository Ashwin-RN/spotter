"""Records what the MCP endpoint receives: the ground truth the evals judge.

CallRecorder wraps the whole app as ASGI middleware. For every POST to /mcp it reads the JSON-RPC body
before the app does, hands the app the same bytes, and reads the answer on the way out. It keeps, per
sign-in token:
- each tools/call: the function name, its arguments as sent, and whether the answer was isError
  (a JSON-RPC error, such as an unknown function or arguments that break the schema, counts as an error);
- the other JSON-RPC methods seen (server/discover or initialize, tools/list, ...), and whether the
  handshake's answer carried the server's instructions.

The token itself is never stored: records are keyed by its SHA-256. Lifespan and every other request
pass straight through.
"""

import hashlib
import json
import threading
from collections import defaultdict
from typing import Any


def token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer(scope: dict) -> str | None:
    for name, value in scope.get("headers") or []:
        if name.lower() == b"authorization":
            text = value.decode("latin-1")
            if text[:7].lower() == "bearer ":
                return text[7:].strip()
    return None


def parse_body(content_type: str, raw: bytes) -> Any:
    """A JSON-RPC message (or batch) from a plain JSON body or the data lines of an event stream."""
    text = raw.decode("utf-8", "replace")
    if content_type.startswith("text/event-stream"):
        found = [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:") and line[5:].strip()]
        return found[0] if len(found) == 1 else found
    return json.loads(text) if text.strip() else None


def _messages(body: Any) -> list[dict]:
    if isinstance(body, dict):
        return [body]
    if isinstance(body, list):
        return [m for m in body if isinstance(m, dict)]
    return []


class CallRecorder:
    def __init__(self, app, path: str = "/mcp") -> None:
        self.app = app
        self.path = path
        self._lock = threading.Lock()
        self._calls: dict[str, list[dict]] = defaultdict(list)
        self._methods: dict[str, list[str]] = defaultdict(list)
        self._instructions: dict[str, bool] = {}

    # ------------------------------------------------------------ what the runner reads

    def calls(self, token: str) -> list[dict]:
        """Every tools/call this token made, in order: {name, arguments, is_error}."""
        with self._lock:
            return [dict(c) for c in self._calls.get(token_key(token), [])]

    def methods(self, token: str) -> list[str]:
        with self._lock:
            return list(self._methods.get(token_key(token), []))

    def got_instructions(self, token: str) -> bool | None:
        """True when a handshake answer to this token carried instructions; None when there was no handshake."""
        with self._lock:
            return self._instructions.get(token_key(token))

    # ------------------------------------------------------------ ASGI

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path", "").rstrip("/") != self.path:
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        raw_request = b"".join(chunks)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": raw_request, "more_body": False}
            return await receive()

        response_type = ""
        response_chunks: list[bytes] = []

        async def capture(message):
            nonlocal response_type
            if message["type"] == "http.response.start":
                for name, value in message.get("headers") or []:
                    if name.lower() == b"content-type":
                        response_type = value.decode("latin-1")
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay, capture)
        finally:
            token = _bearer(scope)
            if token:
                self._record(token_key(token), raw_request, response_type, b"".join(response_chunks))

    def _record(self, key: str, raw_request: bytes, response_type: str, raw_response: bytes) -> None:
        try:
            requests = _messages(json.loads(raw_request.decode("utf-8", "replace")))
        except ValueError:
            return
        try:
            answers = {m.get("id"): m for m in _messages(parse_body(response_type, raw_response))}
        except ValueError:
            answers = {}
        with self._lock:
            for message in requests:
                method = message.get("method")
                if not isinstance(method, str):
                    continue
                self._methods[key].append(method)
                answer = answers.get(message.get("id"), {}) if "id" in message else {}
                result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
                # The handshake: initialize before protocol 2026-07-28, server/discover from it on.
                if method in ("initialize", "server/discover"):
                    self._instructions[key] = self._instructions.get(key, False) or bool(result.get("instructions"))
                if method == "tools/call":
                    params = message.get("params") if isinstance(message.get("params"), dict) else {}
                    arguments = params.get("arguments")
                    self._calls[key].append({
                        "name": params.get("name"),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                        # No answer at all (a crash mid-response) counts as an error too.
                        "is_error": bool(result.get("isError")) or "error" in answer or not answer,
                        "error": (result.get("structuredContent") or {}).get("error")
                        if isinstance(result.get("structuredContent"), dict) else None,
                    })
