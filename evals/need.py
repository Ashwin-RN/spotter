"""Whether a change needs an eval run: python -m evals.need [BASE]

BASE is a git ref, `main` when left out. The answer is yes when what an assistant reads differs between
BASE and the working tree, or when the eval files themselves differ. What an assistant reads is the
published surface: the server's instructions, tools/list as everyone sees it and as the owner sees it,
and each resource as resources/list gives it, with its text.

Each tree's surface is read in a subprocess with that tree's own code: BASE is exported with
`git archive` into a temporary folder; the working tree is read where it is. Each subprocess builds the
app in-process the way tests/conftest.py does: a throwaway SQLite file made by the migrations, the
exercise list synced, a fake sign-in provider and tokens signed for it, an in-process test client. No
port is opened and spotter.db is never used. tools/list is read and hashed the way tests/test_feedback.py
and tests/test_switch.py do it: OWNER_USER_ID set, the owner's token and another person's token, each
tool as compact JSON.

The first line printed is `evals needed: yes` or `evals needed: no`; each line after it names one thing
that differs. Exit code 0 for no, 1 for yes, 2 when the answer could not be worked out.

This file uses the standard library only at its top: it is also the script each subprocess runs, with
`--dump TREE OUT`, before that tree's own code is importable.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# The eval files: the cases, the judge, the runner, the recorder (what quotes_spotter reads) and the two clients
# (what each run sends). A change to any of them alone says yes (contract function-list.md, G1's fix round).
EVAL_FILES = ("evals/cases.json", "evals/judge.py", "evals/run.py", "evals/recorder.py", "evals/codex_client.py",
              "evals/gemini_client.py")
OWNER = "user_01OWNER"  # the owner's sign-in id, as tests/test_trace.py has it
OTHER = "user_01OTHER"  # anyone else, as tests/test_feedback.py has it
DOMAIN = "example.authkit.app"  # the fake provider of tests/conftest.py
ISSUER = f"https://{DOMAIN}"
RESOURCE = "https://spotter.example.app/mcp"
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
FIELDS = {"description": "description", "inputSchema": "input", "outputSchema": "output"}
DUMP_SECONDS = 180
MIN_STRING = 20  # (b): a shorter literal is a key, a code or a format, not text an assistant reads
NOT_ASSISTANT_TEXT = ("app/web.py", "app/charts.py", "app/templates/", "app/static/")  # pages, read by the person
# What varies run to run in an answer, and the fixed mark it is put in as.
VARYING = ((re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?"), "<date>"),
           (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<uuid>"))
VARYING_KEYS = {"expires_at", "exp", "iat", "request_id", "requestId", "account_id", "id"}
REPO = Path(__file__).resolve().parent.parent


class NeedError(Exception):
    """The answer could not be worked out. The message says what failed."""


def compact(value) -> str:
    """One tool as tests/test_feedback.py hashes it."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------- reading one tree (runs in the subprocess)

def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the module, class and function docstring nodes in `tree`."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                found.add(id(first.value))
    return found


def result_text(tree: Path) -> dict[str, list[str]]:
    """(b): by file under app/, its string literals of MIN_STRING or more characters, docstrings left out,
    sorted, so moving code alone is not a change. Pages are left out (NOT_ASSISTANT_TEXT)."""
    out = {}
    for path in sorted((tree / "app").rglob("*.py")):
        name = path.relative_to(tree).as_posix()
        if name in NOT_ASSISTANT_TEXT or name.startswith(NOT_ASSISTANT_TEXT):
            continue
        parsed = ast.parse(_text(path), filename=name)
        skip = _docstrings(parsed)
        out[name] = sorted(node.value for node in ast.walk(parsed)
                           if isinstance(node, ast.Constant) and isinstance(node.value, str)
                           and len(node.value) >= MIN_STRING and id(node) not in skip)
    return out


def steady(value):
    """An answer with what varies run to run put in as a fixed mark (VARYING, VARYING_KEYS)."""
    if isinstance(value, dict):
        return {k: "<varies>" if k in VARYING_KEYS and not isinstance(v, (dict, list)) else steady(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [steady(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return compact(steady(parsed))
        for pattern, mark in VARYING:
            value = pattern.sub(mark, value)
        return value
    return value


def answer(result: dict) -> dict:
    """What an assistant reads of one tools/call answer: its structured content and its text, made steady."""
    return steady({"structured": result.get("structuredContent"), "isError": result.get("isError", False),
                   "text": [c.get("text") for c in result.get("content", []) if c.get("type") == "text"]})

def dump(tree: Path, out: Path) -> None:
    """Write the surface of the code in `tree` to `out` as JSON. Run in a fresh interpreter."""
    sys.path.insert(0, str(tree))
    import shutil
    import time

    import jwt
    from alembic import command
    from alembic.config import Config
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from starlette.testclient import TestClient

    from app import config, db, exercises
    from app.main import create_app  # builds the module's own app from the environment, as the tests' import does

    if Path(config.__file__).resolve().parent.parent != tree.resolve():
        raise RuntimeError(f"imported app from {config.__file__}, not from {tree}")
    work = Path(tempfile.mkdtemp(prefix="spotter-need-"))
    url = f"sqlite:///{(work / 'surface.db').as_posix()}"
    values = {"authkit_domain": DOMAIN, "mcp_resource_url": RESOURCE, "auth_debug": False, "database_url": url}
    try:
        settings = config.Settings(**values, owner_user_id=OWNER)
    except TypeError:  # a tree from before the owner existed
        settings = config.Settings(**values)
    config.use_settings(settings)  # before anything reaches for a database

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
           "kid": "key-1", "use": "sig", "alg": "RS256"}

    def sign(subject: str) -> str:
        now = int(time.time())
        return jwt.encode({"iss": ISSUER, "aud": RESOURCE, "sub": subject, "exp": now + 600, "iat": now,
                           "client_id": "client_01NEED", "scope": "openid profile email"},
                          pem, algorithm="RS256", headers={"kid": "key-1"})

    async def fetch(address: str, headers: dict | None = None) -> dict:
        if address == f"{ISSUER}/oauth2/jwks":
            return {"keys": [jwk]}
        if address == f"{ISSUER}/.well-known/openid-configuration":
            return {"issuer": ISSUER, "userinfo_endpoint": f"{ISSUER}/oauth2/userinfo"}
        if address == f"{ISSUER}/.well-known/oauth-authorization-server":
            return {"issuer": ISSUER, "userinfo_endpoint": None}
        if address == f"{ISSUER}/oauth2/userinfo":
            token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
            subject = jwt.decode(token, options={"verify_signature": False})["sub"]
            return {"sub": subject, "email": "need@example.com", "name": "Need"}
        raise OSError(f"the surface dump fetched an address outside the fake provider: {address}")

    def rpc(client, method: str, subject: str, params: dict | None = None) -> dict:
        r = client.post("/mcp", headers={**HEADERS, "Authorization": f"Bearer {sign(subject)}"},
                        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        body = r.json()
        if r.status_code != 200 or "result" not in body:
            raise RuntimeError(f"{method} answered {r.status_code}: {str(body)[:300]}")
        return body["result"]

    try:
        alembic = Config(str(config.ROOT / "alembic.ini"))
        alembic.attributes["database_url"] = url
        alembic.attributes["skip_logging"] = True
        command.upgrade(alembic, "head")
        with db.session_scope() as session:
            exercises.sync(session)
        with TestClient(create_app(settings, fetch=fetch)) as client:
            init = rpc(client, "initialize", OTHER, {"protocolVersion": "2025-06-18", "capabilities": {},
                                                     "clientInfo": {"name": "need", "version": "0"}})
            surface = {"instructions": init.get("instructions"),
                       "tools": rpc(client, "tools/list", OTHER)["tools"],
                       "owner_tools": rpc(client, "tools/list", OWNER)["tools"],
                       "resources": {}}
            for entry in rpc(client, "resources/list", OTHER)["resources"]:
                contents = rpc(client, "resources/read", OTHER, {"uri": entry["uri"]})["contents"]
                surface["resources"][entry["uri"]] = {"entry": entry, "contents": contents}
            # (a): a fresh account, so setup and both notes show, and the owner. Nothing is logged first.
            call = {"name": "whoami", "arguments": {}}
            surface["results"] = {"whoami": answer(rpc(client, "tools/call", OTHER, call))}
            surface["owner_results"] = {"whoami": answer(rpc(client, "tools/call", OWNER, call))}
        surface["result_text"] = result_text(tree)  # (b)
        out.write_text(compact(surface), encoding="utf-8")
    finally:
        db.dispose()  # Windows keeps the file locked while a connection is open
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- the two trees

def export(base: str, repo: Path, into: Path) -> Path:
    """BASE's files, from `git archive`, in a new folder under `into`. Only reads the repository."""
    verified = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"],
                              capture_output=True, text=True)
    if verified.returncode != 0:
        raise NeedError(f"{base} is not a commit in this repository")
    archive = into / "base.tar"
    with archive.open("wb") as sink:
        made = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", verified.stdout.strip()],
                              stdout=sink, stderr=subprocess.PIPE)
    if made.returncode != 0:
        raise NeedError(f"git archive {base} failed: {made.stderr.decode(errors='replace').strip()}")
    tree = into / "base"
    with tarfile.open(archive) as tar:
        tar.extractall(tree, filter="data")
    archive.unlink()
    return tree


def surfaces(trees: list[Path]) -> list[dict]:
    """Each tree's surface, read in its own subprocess, all at once."""
    with tempfile.TemporaryDirectory(prefix="spotter-need-") as tmp:
        runs = []
        for i, tree in enumerate(trees):
            out = Path(tmp) / f"surface-{i}.json"
            # -I: no script folder or PYTHON* variable on the path, so `app` is that tree's alone.
            runs.append((tree, out, subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--dump", str(tree), str(out)],
                cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)))
        read = []
        for tree, out, run in runs:
            try:
                _, err = run.communicate(timeout=DUMP_SECONDS)
            except subprocess.TimeoutExpired:
                run.kill()
                run.communicate()
                raise NeedError(f"reading the surface of {tree} took over {DUMP_SECONDS} s") from None
            if run.returncode != 0 or not out.exists():
                tail = "\n".join(err.decode(errors="replace").strip().splitlines()[-5:])
                raise NeedError(f"reading the surface of {tree} failed:\n{tail}")
            read.append(json.loads(out.read_text(encoding="utf-8")))
        return read


# ---------------------------------------------------------------- what differs

def _tool_lines(prefix: str, before: list[dict], after: list[dict]) -> list[str]:
    old, new = {t["name"]: t for t in before}, {t["name"]: t for t in after}
    lines = []
    for name in [*new, *(n for n in old if n not in new)]:
        if name not in old:
            lines.append(f"{prefix}{name}: added")
        elif name not in new:
            lines.append(f"{prefix}{name}: removed")
        else:
            for key in sorted(old[name].keys() | new[name].keys(), key=lambda k: (k not in FIELDS, k)):
                if compact(old[name].get(key)) != compact(new[name].get(key)):
                    lines.append(f"{prefix}{name}: {FIELDS.get(key, key)}")
    if [n for n in old if n in new] != [n for n in new if n in old]:
        lines.append(f"{prefix.strip()} order")
    return lines


def differences(before: dict, after: dict) -> list[str]:
    """One line per thing an assistant reads that differs, in the order instructions, tools, owner, resources,
    answers (a), answer text by file (b)."""
    lines = ["instructions"] if before["instructions"] != after["instructions"] else []
    everyone = _tool_lines("tool ", before["tools"], after["tools"])
    lines += everyone
    # The owner's list holds everyone's functions too: a change already named for everyone is not named twice.
    lines += [line for line in _tool_lines("owner tool ", before["owner_tools"], after["owner_tools"])
              if line.removeprefix("owner ") not in everyone]
    old, new = before["resources"], after["resources"]
    for uri in [*new, *(u for u in old if u not in new)]:
        if uri not in old:
            lines.append(f"resource {uri}: added")
        elif uri not in new:
            lines.append(f"resource {uri}: removed")
        elif compact(old[uri]) != compact(new[uri]):
            lines.append(f"resource {uri}")
    for key, prefix in (("results", "result "), ("owner_results", "owner result ")):
        old, new = before.get(key, {}), after.get(key, {})
        lines += [f"{prefix}{name}" for name in sorted(old.keys() | new.keys())
                  if compact(old.get(name)) != compact(new.get(name))]
    old, new = before.get("result_text", {}), after.get("result_text", {})
    lines += [f"result text {name}" for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)]
    return lines


def _text(path: Path) -> bytes | None:
    """A file's bytes with Windows line ends undone: a checkout's line ends are not a change."""
    return path.read_bytes().replace(b"\r\n", b"\n") if path.is_file() else None


def eval_file_lines(base_tree: Path, work_tree: Path) -> list[str]:
    return [name for name in EVAL_FILES if _text(base_tree / name) != _text(work_tree / name)]


# ---------------------------------------------------------------- the command

def needed(base: str, repo: Path = REPO) -> list[str]:
    """Every line that differs between BASE and the working tree in `repo`. Empty: no eval run needed."""
    with tempfile.TemporaryDirectory(prefix="spotter-need-base-") as tmp:
        base_tree = export(base, repo, Path(tmp))
        before, after = surfaces([base_tree, repo])
        return differences(before, after) + eval_file_lines(base_tree, repo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.need",
                                     description="Say whether the working tree needs an eval run against BASE.")
    parser.add_argument("base", nargs="?", default="main", help="a git ref; default main")
    parser.add_argument("--dump", nargs=2, metavar=("TREE", "OUT"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.dump:
        dump(Path(args.dump[0]), Path(args.dump[1]))
        return 0
    try:
        lines = needed(args.base)
    except (NeedError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"evals need: could not tell: {exc}", file=sys.stderr)
        return 2
    print(f"evals needed: {'yes' if lines else 'no'}")
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
