"""Check a running Spotter server from outside, without a token.

Usage: python tools/probe.py <base-url>      for example  python tools/probe.py https://spotter.example.app

Checks /health, the 401 from /mcp and its WWW-Authenticate header, and the protected-resource metadata
the header points at. Prints PASS or FAIL per check and exits non-zero if any check fails.
Standard library only.
"""

import json
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

TIMEOUT = 15


def request(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    """(status, headers, text) for any status; raises only when the server cannot be reached."""
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "spotter-probe", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 - the address is the argument
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


class Probe:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
        self.failed += 0 if ok else 1
        return ok


def origin(url: str) -> tuple[str, str]:
    p = urlparse(url)
    return p.scheme, p.netloc.lower()


def main(base: str) -> int:
    base = base.rstrip("/")
    probe = Probe()

    try:
        status, _, text = request(f"{base}/health")
    except OSError as e:
        probe.check("server reachable", False, type(e).__name__)
        return 1
    probe.check("GET /health is 200 OK", status == 200 and text.strip() == "OK", f"{status} {text[:40]!r}")

    initialize = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}})
    status, headers, text = request(f"{base}/mcp", "POST", initialize.encode(), {
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    if status == 503:
        probe.check("POST /mcp without a token is 401", False, f"503: not configured: {text[:200]}")
        return 1
    probe.check("POST /mcp without a token is 401", status == 401, str(status))

    challenge = headers.get("WWW-Authenticate") or ""
    print(f"      WWW-Authenticate: {challenge}")
    probe.check("WWW-Authenticate is a Bearer challenge", challenge.startswith("Bearer "))
    match = re.search(r'resource_metadata="([^"]+)"', challenge)
    if not probe.check("WWW-Authenticate carries resource_metadata", match is not None):
        return 1
    metadata_url = match.group(1)
    probe.check("resource_metadata is on this server", origin(metadata_url) == origin(base), metadata_url)

    try:
        status, _, text = request(metadata_url)
    except OSError as e:
        probe.check("metadata reachable", False, type(e).__name__)
        return 1
    if not probe.check("metadata is 200", status == 200, f"{status} at {urlparse(metadata_url).path}"):
        return 1
    try:
        metadata = json.loads(text)
    except ValueError:
        probe.check("metadata is JSON", False)
        return 1
    print(f"      metadata: {json.dumps(metadata)}")

    resource = metadata.get("resource")
    probe.check("metadata has resource", isinstance(resource, str) and bool(resource), repr(resource))
    if isinstance(resource, str):
        probe.check("resource is on this server", origin(resource) == origin(base), resource)
        probe.check("resource is the /mcp address", urlparse(resource).path.rstrip("/") == "/mcp", resource)
        rp = urlparse(resource)
        expected = f"{rp.scheme}://{rp.netloc}/.well-known/oauth-protected-resource{rp.path if rp.path != '/' else ''}"
        probe.check("resource_metadata matches resource (RFC 9728)", metadata_url == expected, expected)

    servers = metadata.get("authorization_servers")
    ok = (isinstance(servers, list) and len(servers) == 1 and isinstance(servers[0], str)
          and urlparse(servers[0]).scheme == "https" and urlparse(servers[0]).path in ("", "/"))
    probe.check("authorization_servers is one https issuer", ok, repr(servers))

    print(f"{'FAIL' if probe.failed else 'PASS'}: {probe.failed} check(s) failed")
    return 1 if probe.failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip())
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
