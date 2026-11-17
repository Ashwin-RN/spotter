"""The Gemini client of the evals: python -m evals.run --client gemini [--model M] [--effort E] [--gemini-key N]

Here this process is the MCP host. The same local server, fake issuer, throwaway person, recorder and judge as the
Claude runs (evals/run.py); only the client differs. Built from the trial of 2026-11-17 (contract function-list.md,
G1, "--client gemini"):

- mcp's own Client over streamable HTTP, the bearer token in a header, its own handshake (server/discover, else
  initialize). A run with no instructions in the handshake is a setup fault, not a model's miss.
- generateContent on v1beta: the server's instructions as systemInstruction, the case's say as the one user message,
  each tool's input schema as a functionDeclaration's parametersJsonSchema, unchanged. generationConfig carries
  only the pinned thinking level (thinkingConfig.thinkingLevel); everything else is the API's default.
- Each functionCall runs through the MCP session, one after another, in the order sent; every functionResponse goes
  back in one user content, each with its call's id. Each model content goes back exactly as received, so thought
  signatures stay on their parts. A run ends at a text answer or at MAX_TURNS.
- One key a run, from .env.evals at the repo root (git-ignored): GOOGLE_API_KEY_<n>, n from --gemini-key, never
  switched during a run. It is read in this process when the run starts, sent only in the x-goog-api-key header,
  and taken out of every text this module returns or keeps. Never printed.
- Requests are paced: at most PER_MINUTE a minute, the free tier's limit, so each waits until 60 / PER_MINUTE
  seconds have passed since the one before. A 429 or 503 still backs off once, for the body's retryDelay (at most
  MAX_BACKOFF seconds). A 429 that persists stops that model (QuotaStop). A 503 that persists, a transport error, MISSING_THOUGHT_SIGNATURE or a handshake with no
  instructions is a setup fault: retried at most MAX_SETUP_RETRIES times, each with a fresh person and token. A
  first-turn 400 that names the schema switches that model to the parameters subset once; one that names the
  thinking level stops the model (ConfigStop), since sending the schema another way would not mend it.
- Each run is cut off at RUN_TIMEOUT. A cut-off run keeps its usage, finish reasons and back-off log: the caller owns
  that state and the conversation only adds to it (the trial lost them).

The REST call and the MCP connection are seams (post, connect): the tests replace both, so no test reaches Google
or opens a port.
"""

import contextlib
import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import anyio
import anyio.to_thread

from evals import judge

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / ".env.evals"
BASE = "https://generativelanguage.googleapis.com/v1beta"
KEY_HIDDEN = "[key hidden]"
MAX_BACKOFF = 90
PER_MINUTE = 5  # the free tier's requests a minute for a model, as its 429 named it in the trial
MAX_TURNS = 20
MAX_SETUP_RETRIES = 2
REQUEST_SECONDS = 240


class SetupFault(Exception):
    """Not the model's doing: retried with a fresh person."""


class QuotaStop(Exception):
    """A 429 that persisted after one back-off: this model stops, and the run says so."""


class ConfigStop(Exception):
    """A request this runner built that the API refuses whatever the model does, such as a thinking level the model
    does not take: this model stops."""


class SchemaRefused(SetupFault):
    """A first-turn 400 that names the schema: this model switches to the parameters subset once."""


def load_key(n: int = 1, path: Path = ENV_FILE) -> str:
    """GOOGLE_API_KEY_<n> from the env file. The key's value is never in a message: a missing one names the line."""
    name = f"GOOGLE_API_KEY_{n}"
    path = Path(path)
    if not path.is_file():
        raise KeyError(f"{path.name} is not there: the Gemini runs read their key from it")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(name + "="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise KeyError(f"{name} is not in {path.name}, or it is empty")


def retry_delay(parsed: dict) -> float:
    """Seconds from a google.rpc.RetryInfo retryDelay ("12s" or "1.5s"), plus one, at most MAX_BACKOFF; 20 when
    there is none."""
    for detail in ((parsed or {}).get("error") or {}).get("details") or []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        match = re.match(r"^([0-9.]+)s$", delay) if isinstance(delay, str) else None
        if match:
            return min(float(match.group(1)) + 1, MAX_BACKOFF)
    return 20.0


def _urllib_post(url: str, headers: dict[str, str], body: dict, timeout: float) -> tuple[int, bytes, dict]:
    """The real POST, by urllib. A transport error is status 0 with its name in the body."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        return 0, json.dumps({"transport_error": f"{type(e).__name__}: {e}"}).encode("utf-8"), {}


class GeminiAPI:
    """generateContent with the key in a header and out of every text returned, paced to per_minute requests a
    minute (0: not paced). post, sleep and clock are seams."""

    def __init__(self, key: str, post: Callable = _urllib_post, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, per_minute: int = PER_MINUTE) -> None:
        self._key = key
        self._post = post
        self._sleep = sleep
        self._clock = clock
        self._gap = 60.0 / per_minute if per_minute else 0.0
        self._last: float | None = None
        self.paced: list[float] = []  # each wait the pacing made, in seconds

    def redact(self, text: Any) -> str:
        text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        return text.replace(self._key, KEY_HIDDEN) if self._key else text

    def _pace(self) -> None:
        """Waits until the gap since the last request has passed, then marks this one."""
        if self._gap and self._last is not None:
            wait = self._last + self._gap - self._clock()
            if wait > 0:
                self.paced.append(round(wait, 3))
                self._sleep(wait)
        self._last = self._clock()

    def _once(self, model: str, body: dict) -> tuple[int, dict, dict]:
        self._pace()
        url = f"{BASE}/models/{model}:generateContent"
        status, raw, headers = self._post(url, {"x-goog-api-key": self._key, "Content-Type": "application/json"},
                                          body, REQUEST_SECONDS)
        text = self.redact((raw or b"").decode("utf-8", "replace"))
        try:
            parsed = json.loads(text) if text.strip() else {}
        except ValueError:
            parsed = {"raw": text[:2000]}
        return status, parsed if isinstance(parsed, dict) else {"raw": text[:2000]}, \
            {k: self.redact(v) for k, v in (headers or {}).items()}

    def generate(self, model: str, body: dict, backoffs: list) -> tuple[int, dict, dict, int]:
        """(status, json, headers, attempts). One back-off on a 429 or 503; each one is added to backoffs as it
        happens, so a run cut off during the wait still has it."""
        status, parsed, headers = self._once(model, body)
        if status not in (429, 503):
            return status, parsed, headers, 1
        wait = retry_delay(parsed)
        backoffs.append({"status": status, "waited": wait, "body": parsed})
        self._sleep(wait)
        status, parsed, headers = self._once(model, body)
        if status in (429, 503):
            backoffs.append({"status": status, "waited": 0, "body": parsed})
        return status, parsed, headers, 2


# ---------------------------------------------------------------- schemas

UNSUPPORTED = {"$schema", "$id", "additionalProperties", "unevaluatedProperties", "exclusiveMinimum",
               "exclusiveMaximum", "multipleOf", "patternProperties", "dependentRequired", "examples", "const",
               "oneOf", "allOf", "not", "if", "then", "else", "$comment", "readOnly", "writeOnly", "deprecated",
               "contentEncoding", "contentMediaType", "uniqueItems"}


def to_openapi_subset(schema: Any, defs: dict | None = None, changes: list | None = None, path: str = "$") -> Any:
    """The fallback: a JSON Schema cut down to the Schema subset `parameters` takes, every change named in changes.
    Used only after a model refuses parametersJsonSchema."""
    changes = [] if changes is None else changes
    if not isinstance(schema, dict):
        return schema
    defs = defs if defs is not None else {**schema.get("$defs", {}), **schema.get("definitions", {})}
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        changes.append(f"{path}: $ref {name} inlined")
        merged = {**defs.get(name, {}), **{k: v for k, v in schema.items() if k != "$ref"}}
        return to_openapi_subset(merged, defs, changes, path)
    out: dict = {}
    for key, value in schema.items():
        if key in ("$defs", "definitions"):
            continue
        if key == "const":
            out["enum"] = [value]
            changes.append(f"{path}: const -> enum")
        elif key == "oneOf":
            out["anyOf"] = [to_openapi_subset(v, defs, changes, f"{path}.oneOf[{i}]") for i, v in enumerate(value)]
            changes.append(f"{path}: oneOf -> anyOf")
        elif key in UNSUPPORTED:
            changes.append(f"{path}: {key} dropped")
        elif key == "type" and isinstance(value, list):
            kinds = [t for t in value if t != "null"]
            out["type"] = kinds[0] if kinds else "null"
            if "null" in value:
                out["nullable"] = True
            changes.append(f"{path}: type {value} -> {out['type']}{' nullable' if 'null' in value else ''}")
        elif key == "properties":
            out["properties"] = {k: to_openapi_subset(v, defs, changes, f"{path}.{k}") for k, v in value.items()}
        elif key == "items":
            out["items"] = to_openapi_subset(value, defs, changes, f"{path}[]")
        elif key == "anyOf":
            branches = [b for b in value if not (isinstance(b, dict) and b.get("type") == "null")]
            if len(branches) < len(value):
                out["nullable"] = True
                changes.append(f"{path}: anyOf null branch -> nullable")
            if len(branches) == 1:
                out.update(to_openapi_subset(branches[0], defs, changes, path))
            else:
                out["anyOf"] = [to_openapi_subset(b, defs, changes, f"{path}.anyOf[{i}]")
                                for i, b in enumerate(branches)]
        else:
            out[key] = value
    return out


def declarations(tools: list[dict], mode: str) -> list[dict]:
    """Gemini functionDeclarations from the MCP tool list. json: each inputSchema as parametersJsonSchema, as it
    is. openapi: the subset as parameters, left out for a tool with no arguments."""
    out = []
    for tool in tools:
        declaration = {"name": tool["name"], "description": tool.get("description") or ""}
        schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
        if mode == "json":
            declaration["parametersJsonSchema"] = schema
        else:
            reduced = to_openapi_subset(schema)
            if reduced.get("properties"):
                declaration["parameters"] = reduced
        out.append(declaration)
    return out


def request_body(instructions: str, say: str, tools: list[dict], mode: str, effort: str | None) -> dict:
    """The first turn's body. The thinking level is the one generationConfig field set: the pin."""
    body = {"systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": say}]}],
            "tools": [{"functionDeclarations": declarations(tools, mode)}]}
    if effort:
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": effort.upper()}}
    return body


# ---------------------------------------------------------------- the MCP side

@contextlib.asynccontextmanager
async def mcp_session(port: int, token: str):
    """The mcp library's own Client on the local server, the token in a header: yields the client."""
    import httpx2  # mcp's own HTTP client, in requirements.txt
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                  timeout=httpx2.Timeout(30.0, read=300.0)) as http:
        async with Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http)) as client:
            yield client


async def list_all_tools(session) -> list[dict]:
    tools, cursor = [], None
    for _ in range(50):
        page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
        tools += [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in page.tools]
        cursor = page.next_cursor
        if not cursor:
            break
    return tools


async def run_call(session, name: str, arguments: dict) -> tuple[dict, str | None]:
    """(what Gemini gets as functionResponse.response, a client-side note). The response is the answer's
    structuredContent, the same object the text carries as JSON; a JSON-RPC error or the client's own check comes
    back as {"error": its words}."""
    from mcp.shared.exceptions import MCPError

    try:
        result = await session.call_tool(name, arguments)
    except MCPError as e:  # an unknown function, arguments that break the schema
        return {"error": str(e)}, "json-rpc error"
    except RuntimeError as e:  # the client's own output check
        return {"error": str(e)}, f"client check: {e}"
    dumped = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    structured = dumped.get("structuredContent")
    if isinstance(structured, dict):
        return structured, None
    text = "".join(c.get("text", "") for c in dumped.get("content") or [] if c.get("type") == "text")
    try:
        parsed = json.loads(text)
        return (parsed if isinstance(parsed, dict) else {"output": parsed}), None
    except ValueError:
        return ({"error": text} if dumped.get("isError") else {"output": text}), None


# ---------------------------------------------------------------- the conversation

def new_state() -> dict:
    """What one run's conversation adds to as it goes. The caller holds it, so a run cut off keeps it."""
    return {"reply": "", "turns": 0, "calls_per_turn": [], "finish_reasons": [], "usage": {}, "usage_per_turn": [],
            "model_versions": [], "backoffs": [], "notes": [], "ended": None, "transcript": []}


def _usage_add(total: dict, usage: dict) -> None:
    for key, value in (usage or {}).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value


def _refusal_words(parsed: dict) -> str:
    error = (parsed or {}).get("error") or {}
    return str(error.get("message") or "") if isinstance(error, dict) else str(error)


class GeminiHost:
    """One model's conversations: its API, its pinned thinking level and its schema mode (json until refused)."""

    def __init__(self, api: GeminiAPI, model: str, effort: str | None, schema_mode: str = "json") -> None:
        self.api = api
        self.model = model
        self.effort = effort
        self.schema_mode = schema_mode
        self.schema_refusals: list[str] = []

    async def converse(self, session, instructions: str, tools: list[dict], say: str, state: dict) -> None:
        body = request_body(instructions, say, tools, self.schema_mode, self.effort)
        contents = body["contents"]
        for _ in range(MAX_TURNS):
            status, parsed, _headers, attempts = await anyio.to_thread.run_sync(
                lambda: self.api.generate(self.model, body, state["backoffs"]), abandon_on_cancel=True)
            state["turns"] += 1
            if status == 429 and attempts == 2:
                raise QuotaStop(self.api.redact(json.dumps(parsed)[:3000]))
            if status == 400 and state["turns"] == 1:
                words = _refusal_words(parsed)
                if re.search(r"thinking", words, re.IGNORECASE):
                    raise ConfigStop(self.api.redact(words)[:2000])
                if self.schema_mode == "json" and re.search(r"schema|parameters|function_?declaration", words,
                                                            re.IGNORECASE):
                    raise SchemaRefused(self.api.redact(words)[:2000])
            if status != 200:
                raise SetupFault(f"HTTP {status}: {self.api.redact(json.dumps(parsed)[:3000])}")
            usage = parsed.get("usageMetadata") or {}
            _usage_add(state["usage"], usage)
            state["usage_per_turn"].append(usage)
            version = parsed.get("modelVersion")
            if version and version not in state["model_versions"]:
                state["model_versions"].append(version)
            candidates = parsed.get("candidates") or []
            if not candidates:
                state["ended"] = f"no candidates: {json.dumps(parsed.get('promptFeedback'))}"
                return
            candidate = candidates[0]
            finish = candidate.get("finishReason")
            state["finish_reasons"].append(finish)
            if finish == "MISSING_THOUGHT_SIGNATURE":
                raise SetupFault("MISSING_THOUGHT_SIGNATURE")
            content = candidate.get("content") or {"role": "model", "parts": []}
            contents.append(content)  # as received: thought signatures ride on their parts
            state["transcript"].append({"model_turn": content, "finishReason": finish})
            parts = content.get("parts") or []
            asked = [p["functionCall"] for p in parts if isinstance(p.get("functionCall"), dict)]
            if not asked:
                state["reply"] = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
                state["ended"] = "text answer" if state["reply"].strip() else f"empty reply ({finish})"
                return
            state["calls_per_turn"].append([a.get("name") for a in asked])
            responses = []
            for call in asked:  # one after another, in the order sent
                response, note = await run_call(session, call.get("name", ""), call.get("args") or {})
                if note:
                    state["notes"].append(f"{call.get('name')}: {note}")
                answer = {"name": call.get("name", ""), "response": response}
                if call.get("id"):
                    answer["id"] = call["id"]
                responses.append({"functionResponse": answer})
            contents.append({"role": "user", "parts": responses})
            state["transcript"].append({"function_responses": responses})
        state["ended"] = f"{MAX_TURNS} turns reached"


# ---------------------------------------------------------------- one run

async def _attempt(server, case: dict, zone: str, host: GeminiHost, connect, timeout: float) -> dict:
    """One attempt with a fresh person. A stop or fault it raises carries no copy of that person's token."""
    user_id, token, seeded = server.new_person(zone, case.get("seed") or [], case.get("switch"))
    try:
        return await _attempt_as(server, case, zone, host, connect, timeout, user_id, token, seeded)
    except (SetupFault, QuotaStop, ConfigStop) as e:
        raise type(e)(judge.masked(str(e), token)) from None


async def _attempt_as(server, case: dict, zone: str, host: GeminiHost, connect, timeout: float, user_id: int,
                      token: str, seeded: dict) -> dict:
    from evals.run import blank_outcome, dates_for, person_rows

    resolved = judge.resolve(case, {**dates_for(zone), **{f"seed.{k}": v for k, v in seeded.items()}})
    state = new_state()
    outcome = {**blank_outcome("gemini", host.model, host.effort), "timed_out": False}
    started = time.monotonic()
    try:
        with anyio.fail_after(timeout):
            async with connect(server.port, token) as session:
                instructions = session.instructions
                if not instructions:
                    raise SetupFault("the handshake carried no instructions")
                tools = await list_all_tools(session)
                outcome["tool_count"] = len(tools)
                await host.converse(session, instructions, tools, case["say"], state)
    except TimeoutError:
        outcome["timed_out"] = True
    except BaseExceptionGroup as group:  # anyio's task groups wrap what the conversation raised
        found = _first(group)
        if isinstance(found, TimeoutError):
            outcome["timed_out"] = True
        elif found is group and _transport_error(group):
            raise SetupFault(f"the MCP connection failed: {_transport_error(group)}") from None
        else:
            raise found from None
    except Exception as e:  # noqa: BLE001 - only a transport error is a setup fault; any other is raised
        if not _transport_error(e):
            raise
        raise SetupFault(f"the MCP connection failed: {_transport_error(e)}") from None
    outcome["seconds"] = round(time.monotonic() - started, 1)
    if not outcome["timed_out"] and server.recorder.got_instructions(token) is not True:
        raise SetupFault("the server's record shows no handshake that carried instructions")
    outcome["calls"] = server.recorder.calls(token)
    outcome["methods"] = server.recorder.methods(token)
    outcome["instructions"] = server.recorder.got_instructions(token)
    outcome.update(_kept(state, host.api))
    reasons = [f"timed out after {timeout:g} s"] if outcome["timed_out"] else []
    reasons += judge.judge(resolved, outcome["calls"], state["reply"], person_rows(user_id))
    outcome["reasons"] = [host.api.redact(judge.masked(r, token)) for r in reasons]
    outcome["reply"] = host.api.redact(judge.masked(state["reply"], token))
    outcome["transcript"] = json.loads(host.api.redact(judge.masked(json.dumps(state["transcript"],
                                                                               ensure_ascii=False), token)))
    outcome["passed"] = not outcome["reasons"]
    return outcome


def _transport_error(error: BaseException) -> str | None:
    """Its words when the error, or one inside a group, is the MCP connection's own (a socket, an HTTP transport, a
    JSON-RPC error in the handshake), else None: not the model's doing."""
    import httpx2
    from mcp.shared.exceptions import MCPError

    if isinstance(error, BaseExceptionGroup):
        return next((w for w in map(_transport_error, error.exceptions) if w), None)
    if isinstance(error, (OSError, httpx2.TransportError, MCPError)):
        return f"{type(error).__name__}: {error}"
    return None


def _first(group: BaseExceptionGroup) -> BaseException:
    """The first of SchemaRefused, QuotaStop, ConfigStop, SetupFault or TimeoutError inside a group, else the
    group."""
    for kind in (SchemaRefused, QuotaStop, ConfigStop, SetupFault, TimeoutError):
        for inner in group.exceptions:
            found = _first(inner) if isinstance(inner, BaseExceptionGroup) else inner
            if isinstance(found, kind):
                return found
    return group


def _kept(state: dict, api: GeminiAPI) -> dict:
    """What an outcome keeps of the conversation's state, cut off or not: usage, finish reasons, back-offs."""
    usage = dict(state["usage"])
    return {"num_turns": state["turns"], "usage": usage, "usage_per_turn": list(state["usage_per_turn"]),
            "finish_reasons": list(state["finish_reasons"]), "calls_per_turn": list(state["calls_per_turn"]),
            "models": list(state["model_versions"]), "ended": state["ended"], "notes": list(state["notes"]),
            "backoffs": json.loads(api.redact(json.dumps(state["backoffs"], ensure_ascii=False))),
            **_tokens(usage)}


def _tokens(usage: dict) -> dict:
    """The split and its sum: the run's tokens, as the table adds them (totalTokenCount is not used)."""
    split = judge.token_split("gemini", usage)
    return {"tokens": sum(split.values()), **split}


def run_once_gemini(server, case: dict, zone: str, host: GeminiHost, connect=mcp_session,
                    timeout: float | None = None) -> dict:
    """One Gemini run of a case: the outcome evals/run.py's run_once gives, with the client's own fields beside it.
    A setup fault is retried with a fresh person, at most MAX_SETUP_RETRIES times; QuotaStop and ConfigStop are
    raised for the caller to stop this model."""
    from evals.run import RUN_TIMEOUT

    limit = RUN_TIMEOUT if timeout is None else timeout
    faults: list[str] = []
    tries = 0
    while tries <= MAX_SETUP_RETRIES:
        try:
            outcome = anyio.run(_attempt, server, case, zone, host, connect, limit)
        except SchemaRefused as e:  # once a model, and not a retry: the next attempt sends the subset
            host.schema_refusals.append(host.api.redact(str(e))[:2000])
            faults.append(f"parametersJsonSchema refused, switching to parameters: {host.api.redact(str(e))[:1500]}")
            host.schema_mode = "openapi"
            continue
        except SetupFault as e:
            faults.append(host.api.redact(str(e))[:3000])
            tries += 1
            continue
        outcome["setup_faults"] = faults
        outcome["schema_mode"] = host.schema_mode
        return outcome
    from evals.run import blank_outcome
    return {**blank_outcome("gemini", host.model, host.effort), "reasons": ["setup faults on every attempt"],
            "setup_faults": faults, "schema_mode": host.schema_mode}
