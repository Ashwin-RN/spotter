"""Call one Spotter function on a running server, the way Claude would, for the live checks.

Usage: python tools/mcp_call.py <server address> <function> '<json arguments>'
       for example  python tools/mcp_call.py https://spotter.example.app/mcp whoami '{}'

The address is the /mcp address, or the site's address (then /mcp is added). It must be https, except
on localhost. The sign-in token comes from the environment variable SPOTTER_TOKEN, never from an
argument. It is never printed: every line the tool prints has the token masked, even when a server
sends it back. Redirects are refused: following one would carry the token to another address.
Prints the result's isError and its structured content, and exits 1 when the call failed.
Standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

TIMEOUT = 30
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TOKEN_VARIABLE = "SPOTTER_TOKEN"
HIDDEN = "[token hidden]"


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect is an error. urllib would otherwise send the Authorization header on to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


OPENER = urllib.request.build_opener(_RefuseRedirects)


def endpoint(address: str) -> str | None:
    """The /mcp address to call, or None when it is not allowed (not https, and not localhost)."""
    parsed = urlparse(address.strip())
    host = (parsed.hostname or "").lower()
    if not host or not (parsed.scheme == "https" or (parsed.scheme == "http" and host in LOCAL_HOSTS)):
        return None
    path = parsed.path.rstrip("/")
    return parsed._replace(path=path if path else "/mcp", query="", fragment="").geturl()


def _json_body(content_type: str, text: str) -> dict:
    """The JSON-RPC answer, from a plain JSON reply or the first data line of an event stream."""
    if content_type.startswith("text/event-stream"):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
        raise ValueError("the event stream held no data")
    return json.loads(text)


def masked(text: str, token: str) -> str:
    """The text with every copy of the token replaced, so a server that echoes it never gets it printed."""
    return text.replace(token, HIDDEN) if token else text


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip())
        return 2
    url = endpoint(argv[1])
    if url is None:
        print("The server address must be https (http only on localhost).")
        return 2
    function = argv[2]
    try:
        arguments = json.loads(argv[3])
    except ValueError:
        print("The arguments must be a JSON object, such as '{}' or '{\"kg\": 80}'.")
        return 2
    if not isinstance(arguments, dict):
        print("The arguments must be a JSON object.")
        return 2
    token = os.environ.get(TOKEN_VARIABLE, "").strip()

    def say(text: str) -> None:
        print(masked(text, token))

    if not token:
        say(f"Set {TOKEN_VARIABLE} to a sign-in token first. It is read from there and never printed.")
        return 2

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": function, "arguments": arguments}}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}", "User-Agent": "spotter-mcp-call"})
    try:
        with OPENER.open(request, timeout=TIMEOUT) as response:  # noqa: S310 - https or localhost, checked above
            status, content_type = response.status, response.headers.get("Content-Type", "")
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        say(f"HTTP {e.code}")
        if e.headers.get("WWW-Authenticate"):
            say(f"WWW-Authenticate: {e.headers['WWW-Authenticate']}")
        if 300 <= e.code < 400:
            say(f"Redirect refused (to {e.headers.get('Location', 'an unknown address')}).")
        e.close()
        return 1
    except OSError as e:
        say(f"Could not reach the server: {type(e).__name__}")
        return 1

    try:
        answer = _json_body(content_type, text)
    except ValueError:
        say(f"HTTP {status}: the reply was not JSON.")
        return 1
    if "error" in answer:  # a JSON-RPC error, such as an unknown function
        say(json.dumps({"rpc_error": answer["error"]}, indent=2, ensure_ascii=False))
        return 1
    result = answer.get("result", {})
    shown = result.get("structuredContent")
    if shown is None:
        shown = [c.get("text") for c in result.get("content", [])]
    say(json.dumps({"isError": result.get("isError", False), "result": shown}, indent=2, ensure_ascii=False))
    return 1 if result.get("isError") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
