"""python -m evals.need: whether a change needs an eval run (docs/evals.md, "When evals run").

The working tree's surface is read for real, in subprocesses, twice and once more from HEAD exported with
git archive. Every change is then made on a copy of that surface, or in temporary trees: copies of the
repository or of app/, edited there. The app itself is never edited.
"""

import copy
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app import mcp_server
from app.config import ROOT
from app.services import account
from evals import need

from .test_feedback import compact
from .test_mcp import CAN_AND_CANT, tool_list
from .test_trace import OWNER, sign_for


@pytest.fixture(scope="module")
def read():
    """(the working tree's surface, the same read again, HEAD's surface, HEAD's exported tree's files)."""
    tmp = Path(tempfile.mkdtemp(prefix="spotter-need-test-"))
    try:
        head = need.export("HEAD", ROOT, tmp)
        work, again, base = need.surfaces([ROOT, ROOT, head])
        yield work, again, base, sorted(p.name for p in head.iterdir())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def surface(read):
    return copy.deepcopy(read[0])


LEFT_OUT = shutil.ignore_patterns(".git", ".venv", "private", "__pycache__", ".pytest_cache", "*.db", "*.db-*")
NEW_NOTE = "No time zone is set yet, so today is the UTC date."


def edited(source: Path, path: str, old: str, new: str) -> None:
    """Replace `old`, found once in `source / path`, with `new`: only ever on a copy."""
    assert ROOT.resolve() not in [source.resolve(), *source.resolve().parents]
    file = source / path
    text = file.read_text(encoding="utf-8")
    assert text.count(old) == 1, f"{old!r} is not in {path} once"
    file.write_text(text.replace(old, new), encoding="utf-8")


@pytest.fixture(scope="module")
def changed_note():
    """The surface of a copy of the working tree whose TIME_ZONE_NOTE is reworded."""
    tmp = Path(tempfile.mkdtemp(prefix="spotter-need-test-"))
    try:
        tree = tmp / "work"
        shutil.copytree(ROOT, tree, ignore=LEFT_OUT)
        edited(tree, "app/services/account.py", account.TIME_ZONE_NOTE[:48], NEW_NOTE)
        [surface] = need.surfaces([tree])
        yield surface
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def app_copy(tmp_path):
    """A copy of app/ alone, for the static net (b), which reads nothing else."""
    shutil.copytree(ROOT / "app", tmp_path / "app", ignore=LEFT_OUT)
    return tmp_path


def tool(surface: dict, name: str, owner: bool = False) -> dict:
    return next(t for t in surface["owner_tools" if owner else "tools"] if t["name"] == name)


def test_the_surface_against_itself_is_no(read):
    work, again, _, _ = read
    assert need.differences(work, again) == []
    assert need.eval_file_lines(ROOT, ROOT) == []


def test_the_dump_is_the_tests_own_tools_list_instructions_and_resources(read, make_client):
    work, _, _, _ = read
    client = make_client(owner_user_id=OWNER)
    assert [compact(t) for t in work["owner_tools"]] == [compact(t) for t in tool_list(client, sign_for(OWNER))]
    assert [compact(t) for t in work["tools"]] == [compact(t) for t in tool_list(client, sign_for("user_01OTHER"))]
    assert need.compact(work["tools"]) == compact(work["tools"])
    assert {"owner_calls", "owner_feedback", "owner_delete_feedback"} <= {t["name"] for t in work["owner_tools"]}
    assert not {"owner_calls", "owner_feedback"} & {t["name"] for t in work["tools"]}
    assert work["instructions"] == mcp_server.INSTRUCTIONS
    assert set(work["resources"]) == {"spotter://can-and-cant", "spotter://exercises"}
    [text] = work["resources"]["spotter://can-and-cant"]["contents"]
    assert text["text"] == CAN_AND_CANT.read_text(encoding="utf-8")


def test_base_is_read_from_its_own_export_and_no_spotter_db_is_made(read):
    _, _, base, names = read
    assert {"app", "evals", "migrations", "alembic.ini"} <= set(names) and "spotter.db" not in names
    assert base["tools"] and base["instructions"]


def test_a_changed_tool_description_is_yes_with_its_line_once(read, surface):
    for listed in (tool(surface, "whoami"), tool(surface, "whoami", owner=True)):
        listed["description"] += " One more sentence."
    assert need.differences(read[0], surface) == ["tool whoami: description"]


def test_a_changed_input_or_output_is_named(read, surface):
    tool(surface, "log_food")["inputSchema"]["description"] = "Changed."
    tool(surface, "get_log")["outputSchema"]["description"] = "Changed."
    assert sorted(need.differences(read[0], surface)) == ["tool get_log: output", "tool log_food: input"]


def test_a_changed_instruction_is_yes(read, surface):
    surface["instructions"] = surface["instructions"].replace("Call whoami first", "Call whoami at once")
    assert need.differences(read[0], surface) == ["instructions"]


def test_a_new_function_and_a_removed_one_are_yes(read, surface):
    new = {"name": "plan_week", "description": "Plans a week.", "inputSchema": {"type": "object"}}
    surface["tools"].append(new)
    surface["owner_tools"].append(copy.deepcopy(new))
    assert need.differences(read[0], surface) == ["tool plan_week: added"]
    assert need.differences(surface, read[0]) == ["tool plan_week: removed"]


def test_a_new_owner_function_is_named_for_the_owner_alone(read, surface):
    surface["owner_tools"].append({"name": "owner_more", "description": "More.", "inputSchema": {}})
    assert need.differences(read[0], surface) == ["owner tool owner_more: added"]
    tool(surface, "owner_feedback", owner=True)["description"] = "Changed."
    assert need.differences(read[0], surface) == ["owner tool owner_feedback: description",
                                                  "owner tool owner_more: added"]


def test_a_changed_resource_is_yes(read, surface):
    surface["resources"]["spotter://can-and-cant"]["contents"][0]["text"] += "\nOne more line.\n"
    assert need.differences(read[0], surface) == ["resource spotter://can-and-cant"]
    surface["resources"]["spotter://routines"] = {"entry": {"uri": "spotter://routines"}, "contents": []}
    assert need.differences(read[0], surface)[-1] == "resource spotter://routines: added"


def test_whoami_is_read_for_a_fresh_account_and_the_owner_and_steady(read):
    work, again, _, _ = read
    assert need.compact(work["results"]) == need.compact(again["results"])
    other, owner = work["results"]["whoami"]["structured"], work["owner_results"]["whoami"]["structured"]
    assert other["time_zone_note"] == account.TIME_ZONE_NOTE
    assert other["new_account_note"] == account.NEW_ACCOUNT_NOTE
    assert other["setup"]["how"] == account.SETUP_HOW and other["setup"]["missing"][0] == "time_zone"
    assert other["user_id"] == need.OTHER and owner["user_id"] == need.OWNER
    assert other["today"] == "<date>" and other["sign_in"]["expires_at"] == "<varies>"
    assert account.TIME_ZONE_NOTE in work["results"]["whoami"]["text"][0]


def test_a_changed_time_zone_note_is_yes(read, changed_note):
    assert changed_note["results"]["whoami"]["structured"]["time_zone_note"].startswith(NEW_NOTE)
    lines = need.differences(read[0], changed_note)
    assert lines == ["result whoami", "owner result whoami", "result text app/services/account.py"]


def test_the_static_net_reads_strings_and_leaves_out_docstrings_and_pages(app_copy):
    net = need.result_text(ROOT)
    assert "app/web.py" not in net and not [n for n in net if n.startswith(("app/templates/", "app/static/"))]
    assert account.TIME_ZONE_NOTE in net["app/services/account.py"]
    assert account.whoami.__doc__ not in net["app/services/account.py"]
    assert need.result_text(app_copy) == net  # an unchanged tree: no


def test_a_changed_docstring_of_a_service_function_alone_is_no(read, app_copy, surface):
    edited(app_copy, "app/services/account.py", "Returns {account_id, user_id,", "Gives {account_id, user_id,")
    surface["result_text"] = need.result_text(app_copy)
    assert need.differences(read[0], surface) == []


def test_a_changed_page_string_in_web_is_no(read, app_copy, surface):
    edited(app_copy, "app/web.py", "Give the average heart rate", "Type the average heart rate")
    surface["result_text"] = need.result_text(app_copy)
    assert need.differences(read[0], surface) == []


def test_a_changed_chart_string_is_no_and_no_function_reaches_the_charts(read, app_copy, surface, tmp_path):
    """app/charts.py draws the pages' charts: no MCP function imports it, so its words are never an assistant's."""
    edited(app_copy, "app/charts.py", "Smoothed body weight over the last 4 weeks, from ",
           "Smoothed weight over the last 4 weeks, from ")
    surface["result_text"] = need.result_text(app_copy)
    assert need.differences(read[0], surface) == []
    loads = ("import sys; sys.path.insert(0, sys.argv[1]); import app.mcp_server, app.services; "
             "print('app.charts' in sys.modules)")
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{(tmp_path / 'unused.db').as_posix()}"}
    done = subprocess.run([sys.executable, "-I", "-c", loads, str(ROOT)], cwd=tmp_path, env=env, capture_output=True,
                          text=True, timeout=120)
    assert done.stdout.strip() == "False", done.stderr


def test_a_changed_fix_line_in_a_service_is_yes(read, app_copy, surface):
    edited(app_copy, "app/services/account.py", "Use an IANA name such as Asia/Kolkata",
           "Use an IANA zone name such as Asia/Kolkata")
    surface["result_text"] = need.result_text(app_copy)
    assert need.differences(read[0], surface) == ["result text app/services/account.py"]


def test_a_changed_case_file_is_yes_and_line_ends_alone_are_not(tmp_path):
    base, work = tmp_path / "base", tmp_path / "work"
    for tree in (base, work):
        (tree / "evals").mkdir(parents=True)
        (tree / "evals" / "judge.py").write_bytes(b"x = 1\r\n")
        (tree / "evals" / "run.py").write_bytes(b"y = 2\n")
        (tree / "evals" / "cases.json").write_bytes(b'{"cases": []}\n')
    (work / "evals" / "judge.py").write_bytes(b"x = 1\n")
    assert need.eval_file_lines(base, work) == []
    (work / "evals" / "cases.json").write_bytes(b'{"cases": [{"id": "new"}]}\n')
    assert need.eval_file_lines(base, work) == ["evals/cases.json"]
    (base / "evals" / "run.py").unlink()
    assert need.eval_file_lines(base, work) == ["evals/cases.json", "evals/run.py"]


def test_the_command_prints_the_answer_first_and_exits_0_1_or_2(monkeypatch, capsys):
    monkeypatch.setattr(need, "needed", lambda base: [])
    assert need.main([]) == 0 and capsys.readouterr().out == "evals needed: no\n"
    monkeypatch.setattr(need, "needed", lambda base: ["instructions", "evals/cases.json"])
    assert need.main(["ec48cdb"]) == 1
    assert capsys.readouterr().out == "evals needed: yes\ninstructions\nevals/cases.json\n"


def test_a_ref_that_is_no_commit_is_exit_2_and_says_so(capsys):
    assert need.main(["no-such-ref-for-need"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and "no-such-ref-for-need is not a commit" in out.err
