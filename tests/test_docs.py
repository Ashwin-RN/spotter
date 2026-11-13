"""T9: the README's facts match the code.

- Its MCP function count equals len(mcp_server.NEEDS).
- Its page list names every top-level page: each parameterless links._PATHS name but home and sign_in.
  Once privacy is a links._PATHS name, Privacy is one of them, and the README must then name it.
  Extra names in the list are allowed.
- Any eval case count it states equals the number of cases in evals/cases.json.
- It writes no test count by hand.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CASES = ROOT / "evals" / "cases.json"

WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
         "sixteen seventeen eighteen nineteen twenty twenty-one twenty-two twenty-three twenty-four "
         "twenty-five twenty-six twenty-seven twenty-eight twenty-nine thirty").split()
NUMBER = r"(\d[\d,]*|" + "|".join(sorted(WORDS, key=len, reverse=True)) + r")"

# A page name for each links._PATHS name, where the name alone does not give it.
PAGE_NAMES = {"today": "Today", "progress": "Progress", "goals": "Goals", "foods": "Foods",
              "settings": "Settings"}
NOT_PAGES = ("home", "sign_in")


def number(text: str) -> int:
    text = text.casefold()
    return WORDS.index(text) if text in WORDS else int(text.replace(",", ""))


def function_counts(readme: str) -> list[int]:
    """Every "N MCP functions" the README states."""
    return [number(m.group(1)) for m in re.finditer(NUMBER + r"\s+MCP\s+functions\b", readme, re.IGNORECASE)]


def case_counts(readme: str) -> list[int]:
    """Every "N cases" or "N eval cases" the README states."""
    return [number(m.group(1)) for m in re.finditer(NUMBER + r"\s+(?:eval\s+)?cases\b", readme, re.IGNORECASE)]


def test_counts(readme: str) -> list[str]:
    """Every test count written by hand: "1,598 tests", "968 test cases"."""
    return [m.group(0) for m in re.finditer(NUMBER + r"\s+(?:unit\s+|pytest\s+)?tests?(?:\s+cases)?\b", readme,
                                            re.IGNORECASE)]


test_counts.__test__ = False  # a helper, not a test


def page_list(readme: str) -> str | None:
    """The README's list of pages: the brackets after "a web app"."""
    m = re.search(r"\bweb app\b[^(\n]*\(([^)]*)\)", readme)
    return m.group(1) if m else None


def top_level_pages(paths: dict[str, str] | None = None) -> list[str]:
    """The page name of each parameterless links._PATHS name, home and sign_in left out."""
    if paths is None:
        from app import links
        paths = links._PATHS
    names = [n for n, path in paths.items() if "{" not in path and n not in NOT_PAGES]
    return [PAGE_NAMES.get(n, n.replace("_", " ").capitalize()) for n in names]


def missing_pages(listed: str | None, pages: list[str]) -> list[str]:
    if listed is None:
        return list(pages)
    return [p for p in pages if not re.search(rf"(?<!\w){re.escape(p)}(?!\w)", listed)]


def cases_in(path: Path) -> int:
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list) and cases, f"{path} must be a non-empty list of cases"
    return len(cases)


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


# ---------------------------------------------------------------- T9 on the README


def test_the_function_count_is_the_servers(readme):
    from app.mcp_server import NEEDS
    counts = function_counts(readme)
    assert counts, "README.md states no MCP function count"
    offered = len(NEEDS) - 1  # every function but owner_calls, the owner's alone (trace.md decision 16)
    assert set(counts) == {offered}, f"README.md says {counts}, the server offers {offered}"


def test_the_page_list_names_every_top_level_page(readme):
    listed = page_list(readme)
    assert listed is not None, "README.md has no page list after 'a web app'"
    missing = missing_pages(listed, top_level_pages())
    assert not missing, f"README.md's page list does not name {missing}"


def test_any_case_count_is_the_cases_file(readme):
    stated = case_counts(readme)
    real = cases_in(CASES)
    assert all(n == real for n in stated), f"README.md says {stated} cases, evals/cases.json has {real}"


def test_no_test_count_is_written_by_hand(readme):
    assert test_counts(readme) == [], f"README.md writes a test count by hand: {test_counts(readme)}"


# ---------------------------------------------------------------- T9's helpers, on hand-made text


def test_function_counts_read_digits_and_words():
    assert function_counts("18 MCP functions, and Sixteen MCP functions. Twenty-one mcp functions") == [18, 16, 21]
    assert function_counts("The MCP functions, 18 functions, 18 MCP tools") == []


def test_case_counts_read_every_statement():
    assert case_counts("17 cases, 3 runs each. Sixteen eval cases. 1,000 cases") == [17, 16, 1000]
    assert case_counts("48 of 48 runs passed, with one case fewer. Test cases run.") == []


def test_test_counts_find_a_count_written_by_hand():
    assert test_counts("1,598 tests, run plain. 968 test cases. One test. 12 unit tests") == [
        "1,598 tests", "968 test cases", "One test", "12 unit tests"]
    assert test_counts("Tests: run python -m pytest tests -q. The tests run before every push.") == []


def test_page_list_reads_the_brackets_after_web_app():
    text = "18 functions, a web app on the same account (Today, Progress with a page per lift, Goals), and more."
    assert page_list(text) == "Today, Progress with a page per lift, Goals"
    assert page_list("No list here (Today).") is None


def test_missing_pages_match_whole_names():
    assert missing_pages("Today, Progress, Goals", ["Today", "Goals", "Foods"]) == ["Foods"]
    assert missing_pages("Todays, Goalsetting", ["Today", "Goals"]) == ["Today", "Goals"]
    assert missing_pages(None, ["Today"]) == ["Today"]


def test_top_level_pages_come_from_links():
    pages = top_level_pages()
    assert {"Today", "Progress", "Goals", "Foods", "Settings"} <= set(pages)
    assert "Home" not in pages and "Sign in" not in pages and "Day" not in pages


def test_privacy_is_required_once_it_is_a_link():
    """When privacy becomes a links._PATHS name the mapping gives it Privacy, and the page-list test then asks
    the README for it."""
    from app import links
    with_privacy = {**links._PATHS, "privacy": "/privacy"}
    assert "Privacy" in top_level_pages(with_privacy)
    assert missing_pages("Today, Progress, Goals, Foods, Settings", top_level_pages(with_privacy)) == ["Privacy"]


def test_an_extra_name_in_the_list_is_not_a_fault():
    assert missing_pages("Today, Goals, Privacy", ["Today", "Goals"]) == []


def test_cases_in_counts_the_list(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")
    assert cases_in(path) == 2
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError):
        cases_in(path)
