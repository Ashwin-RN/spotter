"""The Codex client of the evals: python -m evals.run --client codex [--model M] [--effort E]

The same local server, fake issuer, throwaway person, recorder and judge as the Claude runs (evals/run.py). Only the
client differs: `codex exec --json`, with the server and its bearer token given by -c overrides, and the token only
in the environment of that one process, under TOKEN_VARIABLE. The calls judged are the server's record, as with
Claude; the JSONL events give the reply, the usage, the turns and the client's own view of each call.

Built from the trial of 2026-11-17 on codex-cli 0.169.0 (contract function-list.md, G1, "--client codex"):
- the vendor codex.exe, not the npm shim, so a run that is cut off can be stopped with its whole tree;
- exec --json --ephemeral --skip-git-repo-check --ignore-user-config -s read-only: no saved session, none of the
  owner's own Codex settings or MCP servers (auth still works with it), nothing written;
- the features that are not Spotter's turned off, and web search off, so a run is Spotter's functions alone;
- mcp_servers.spotter.url, .bearer_token_env_var and .default_tools_approval_mode="approve": a write runs with no
  prompt (seen working on log_food in the trial);
- -m pinned, since --ignore-user-config drops the config's model line, and -c model_reasoning_effort pinned, the
  config key Codex 0.169.0 reads (its own config struct names it; the owner's config.toml sets it).

What Codex does not say: which model ran (no event names one), and what a run costs (signed in with ChatGPT, it is
plan usage, not a bill). So a Codex outcome carries the model asked for, no models, and cost_usd None.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from evals import judge

TOKEN_VARIABLE = "SPOTTER_EVAL_TOKEN"
# Codex tools that are not Spotter's, turned off so a run is the MCP functions alone (as --allowedTools
# mcp__spotter__* is for Claude). apps and plugins also keep any ChatGPT connector out of the run. The trial saw
# that multi_agent off still left the collaboration.* tools listed: a known limit.
DISABLED_FEATURES = ("apps", "plugins", "remote_plugin", "shell_tool", "unified_exec", "browser_use",
                     "browser_use_external", "computer_use", "image_generation", "multi_agent", "hooks", "goals")
APPROVAL = "approve"  # one of auto, prompt, writes, approve, by Codex's own parse error in the trial
# Where npm puts the vendor binary on Windows, under %APPDATA%.
VENDOR_EXE = Path("npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/"
                  "x86_64-pc-windows-msvc/bin/codex.exe")


def find_codex(environ: dict[str, str] | None = None) -> str | None:
    """The vendor codex.exe under %APPDATA% when it is there, else codex on the PATH, else None."""
    environ = os.environ if environ is None else environ
    appdata = environ.get("APPDATA")
    if appdata and (Path(appdata) / VENDOR_EXE).is_file():
        return str(Path(appdata) / VENDOR_EXE)
    return shutil.which("codex")


def codex_command(codex: str, say: str, port: int, cwd: Path, model: str | None, effort: str | None) -> list[str]:
    """The command for one run. The token is never in it: Codex reads it from TOKEN_VARIABLE."""
    command = [codex, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--ignore-user-config",
               "-s", "read-only", "-C", str(cwd)]
    if model:
        command += ["-m", model]
    for feature in DISABLED_FEATURES:
        command += ["--disable", feature]
    command += ["-c", 'web_search="disabled"']
    if effort:
        command += ["-c", f'model_reasoning_effort="{effort}"']
    command += ["-c", f'mcp_servers.spotter.url="http://127.0.0.1:{port}/mcp"',
                "-c", f'mcp_servers.spotter.bearer_token_env_var="{TOKEN_VARIABLE}"',
                "-c", f'mcp_servers.spotter.default_tools_approval_mode="{APPROVAL}"']
    return command + [say]


def parse_codex_jsonl(stdout: str) -> dict:
    """The parts of `codex exec --json` the report uses: the last agent message as the reply, the usage of every
    turn.completed summed field by field, the MCP call items as Codex saw them, and any error events.

    Returns {reply, num_turns, usage, tokens, input_tokens, cached_tokens, output_tokens, reasoning_tokens,
    item_types, tool_items, errors, completed}. num_turns counts turn.completed events: one an exec prompt, not
    Claude's count of model steps. tokens is input_tokens + output_tokens: the cached part is inside input_tokens,
    OpenAI's convention, so it is not added again (judge.token_split). Raises judge.CliOutputError when there is no
    JSON event at all."""
    events = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    if not events:
        raise judge.CliOutputError("codex printed no JSON events")
    items = [e["item"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)]
    messages = [i.get("text") or "" for i in items if i.get("type") == "agent_message"]
    usage: dict[str, int] = {}
    turns = 0
    for e in events:
        if e.get("type") == "turn.completed":
            turns += 1
            for key, value in (e.get("usage") or {}).items():
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
    tool_items = [{"server": i.get("server"), "tool": i.get("tool"), "arguments": i.get("arguments"),
                   "status": i.get("status"),
                   "is_error": bool((i.get("result") or {}).get("is_error") or i.get("error")
                                    or i.get("status") == "failed")}
                  for i in items if i.get("type") == "mcp_tool_call"]
    errors = [e for e in events if e.get("type") in ("error", "turn.failed")]
    return {
        "reply": messages[-1] if messages else "",
        "num_turns": turns,
        "usage": usage,
        "tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        **judge.token_split("codex", usage),
        "reasoning_tokens": usage.get("reasoning_output_tokens", 0),
        "item_types": [i.get("type") for i in items],
        "tool_items": tool_items,
        "errors": errors,
        "completed": turns > 0,
    }


def _stop_tree(process: subprocess.Popen) -> None:
    """Stops a run that is cut off, with every process it started."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, shell=False)
    else:
        process.kill()


def run_once_codex(server, case: dict, zone: str, codex: str, model: str | None, effort: str | None,
                   env: dict[str, str], workdir: Path, label: str, keep: bool, popen=subprocess.Popen) -> dict:
    """One Codex run of a case: the same outcome as evals/run.py's run_once, with the client's own fields beside it.
    popen is the seam the tests replace: nothing here starts Codex in a test."""
    from evals.run import RUN_TIMEOUT, blank_outcome, dates_for, person_rows

    user_id, token, seeded = server.new_person(zone, case.get("seed") or [], case.get("switch"))
    resolved = judge.resolve(case, {**dates_for(zone), **{f"seed.{k}": v for k, v in seeded.items()}})
    cwd = workdir / f"cwd-{label}"
    cwd.mkdir()
    child = {**env, TOKEN_VARIABLE: token}  # this one process only
    outcome = blank_outcome("codex", model, effort)
    started = time.monotonic()
    timed_out = False
    process = popen(codex_command(codex, case["say"], server.port, cwd, model, effort), cwd=cwd, env=child,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                    errors="replace", shell=False)
    try:
        stdout, stderr = process.communicate(timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_tree(process)
        stdout, stderr = process.communicate()
        stderr = (stderr or "") + f"\ntimed out after {RUN_TIMEOUT} s"
    finally:
        del child
    outcome["seconds"] = round(time.monotonic() - started, 1)
    outcome["exit_code"] = process.returncode
    outcome["timed_out"] = timed_out
    if keep:  # the JSONL holds every answer in full: the throwaway person's data, masked for the token
        (workdir / f"out-{label}.jsonl").write_text(judge.masked(stdout or "", token), encoding="utf-8")
        if stderr:
            (workdir / f"err-{label}.txt").write_text(judge.masked(stderr, token), encoding="utf-8")
    shutil.rmtree(cwd, ignore_errors=True)

    outcome["calls"] = server.recorder.calls(token)
    outcome["methods"] = server.recorder.methods(token)
    outcome["instructions"] = server.recorder.got_instructions(token)
    try:
        parsed = parse_codex_jsonl(stdout)
    except judge.CliOutputError as e:
        outcome["reasons"] = [f"codex: {e}; stderr: {judge.masked((stderr or '').strip()[-500:], token) or 'none'}"]
        if timed_out:
            outcome["reasons"].append(f"timed out after {RUN_TIMEOUT} s")
        return outcome
    outcome.update({k: parsed[k] for k in ("reply", "num_turns", "usage", "tokens", "input_tokens", "cached_tokens",
                                           "output_tokens", "reasoning_tokens", "item_types", "tool_items")})
    reasons = []
    if not parsed["completed"] or parsed["errors"]:
        reasons.append(f"codex did not complete the turn: {json.dumps(parsed['errors'])[:300]}")
    if timed_out:
        reasons.append(f"timed out after {RUN_TIMEOUT} s")
    reasons += judge.judge(resolved, outcome["calls"], parsed["reply"], person_rows(user_id))
    outcome["reasons"] = [judge.masked(r, token) for r in reasons]
    outcome["reply"] = judge.masked(outcome["reply"], token)
    outcome["passed"] = not outcome["reasons"]
    return outcome
