"""The program card on Routines (docs/contracts/routines-and-programs.md: the page, decisions 14 and 19, and the note
of 2026-11-23): the program's name, its week, this week's days with today's row marked, the next routine day, and
Delete, which deletes at once and lands on the Undo strip.

Every today is the test's own clock (db.utcnow replaced, as tests/test_programs.py does), never the real date. The
person has no zone, so today is the clock's date. The card's weeks, dates and routines are get_routines' own
(programs.program_read); these tests read them off the page.
"""

import re
from datetime import date

import pytest
from sqlalchemy import select

from app import db, services, web
from app.models import Program

from . import text_rules as tr
from .test_calllog import rows
from .test_page_rules import Seen, built_findings, follow, frame_findings, page_rule_findings, titled_card_findings
from .test_pages_text import entered_notes, entered_text, names_of, page_findings, step_4_findings
from .test_programs import LEGS, PPL, PPL_DAYS, PULL, PUSH, at, clock  # noqa: F401 - clock is a fixture
from .test_web import client, location, only_user, post, seam, sign_in, web_client  # noqa: F401 - fixtures

NB = " "  # formatting's no-break space between a number and its unit
CARD = re.compile(r'<section class="card saved-foods program" aria-labelledby="program-(\d+)-title">(.*?)</section>',
                  re.S)


@pytest.fixture
def me(client, clock):
    """Signed in on Fri 20 Nov 2026 (the clock's), with the three routines PPL names."""
    at(clock, date(2026, 11, 20))
    sign_in(client)
    person = only_user().id
    for name, items in (("Push day", PUSH), ("Pull day", PULL), ("Legs day", LEGS)):
        assert "error" not in services.save_routine(person, {"name": name, "lifts": items}, "web")
    return person


def save(person: int, start: str = "2026-11-02", blocks=None) -> int:
    out = services.save_program(person, {**PPL, "start_on": start, **({"blocks": blocks} if blocks else {})}, "web")
    assert "error" not in out, out
    return out["id"]


def card(markup: str) -> dict | None:
    """The program card as a person reads it: its id, h2, the lines around its table, and the table's rows (the day
    with its tag, the routine)."""
    found = CARD.findall(markup)
    if not found:
        return None
    [(program_id, inner)] = found
    text = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip().replace("&#39;", "'")  # noqa: E731
    head, _, rest = inner.partition("</div>")
    before, _, after = rest.partition('<div class="table-wrap">')
    if not after:
        before, after = rest, ""
    table, _, tail = after.partition("</table>") if after else ("", "", "")
    rows_ = [(text(day), text(routine), today == ' aria-current="date"') for today, day, routine in re.findall(
        r'<tr( aria-current="date")?><td class="lead">(.*?)</td><td data-label="Routine">(.*?)</td></tr>', table)]
    lines = lambda s: [text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", s, re.S)]  # noqa: E731
    return {"id": int(program_id), "h2": text(re.search(r"<h2[^>]*>(.*?)</h2>", head).group(1)),
            "delete": re.findall(r'action="([^"]+)"', head), "lines": lines(before), "rows": rows_,
            "after": lines(tail if after else "")}


def page_of(client, mark: str = "") -> str:
    r = client.get("/routines" + mark)
    assert r.status_code == 200, r.text[:300]
    return r.text


def table() -> list[dict]:
    with db.new_session() as s:
        return [{"id": r.id, "user_id": r.user_id, "deleted_at": r.deleted_at}
                for r in s.scalars(select(Program).order_by(Program.id))]


def week(*routines: str) -> list[tuple]:
    """This week's rows, Mon 16 to Sun 22 Nov, each (day, routine, marked), today's marked by its tag."""
    days = ["Mon 16 Nov", "Tue 17 Nov", "Wed 18 Nov", "Thu 19 Nov", "Fri 20 Nov", "Sat 21 Nov", "Sun 22 Nov"]
    return list(zip(days, routines))


def marked(rows_: list[tuple], today: str) -> list[tuple]:
    return [(f"{d} Today" if d == today else d, r, d == today) for d, r in rows_]


PPL_WEEK = week("Push day", "Rest day", "Pull day", "Rest day", "Legs day", "Rest day", "Rest day")


# ---------------------------------------------------------------- the card, by its state

def test_with_no_program_there_is_no_card_and_the_page_is_as_today(client, me):
    page = page_of(client)
    assert card(page) is None and "program" not in page.lower()
    ids = [r["id"] for r in services.get_routines(me)["routines"]]
    assert re.findall(r'<form [^>]*action="([^"]+)"', page) == ["/sign-out"] + [f"/routines/{i}/delete" for i in
                                                                               ids] + ["/settings"]


def test_running_the_card_says_its_week_this_week_s_days_today_marked_and_the_next(client, me):
    made = save(me)
    page = page_of(client)
    assert card(page) == {"id": made, "h2": "PPL", "delete": [f"/routines/program/{made}/delete"],
                          "lines": ["Week 3 of 8, from Mon 2 Nov."], "rows": marked(PPL_WEEK, "Fri 20 Nov"),
                          "after": ["Next: Push day on Mon 23 Nov."]}  # the contract's two lines, word for word
    assert '<thead><tr><th scope="col">Day</th><th scope="col">Routine</th></tr></thead>' in page
    assert '<td class="lead">Fri 20 Nov <span class="tag">Today</span></td>' in page and page.count("aria-current=") \
        == page.count('aria-current="date"') + page.count('aria-current="page"')
    assert page.index('class="card saved-foods program"') < page.index('class="card saved-foods routine"')


def test_a_rest_day_is_marked_today_and_says_rest_day(client, me, clock):
    save(me)
    at(clock, date(2026, 11, 21))
    found = card(page_of(client))
    assert found["rows"] == marked(PPL_WEEK, "Sat 21 Nov") and ("Sat 21 Nov Today", "Rest day", True) in found["rows"]
    assert found["lines"] == ["Week 3 of 8, from Mon 2 Nov."] and found["after"] == ["Next: Push day on Mon 23 Nov."]


def test_the_week_s_days_start_on_start_on_s_weekday(client, me):
    save(me, "2026-11-04")  # a Wednesday: the program week runs Wed to Tue
    found = card(page_of(client))
    assert [d for d, _, _ in found["rows"]] == ["Wed 18 Nov", "Thu 19 Nov", "Fri 20 Nov Today", "Sat 21 Nov",
                                                "Sun 22 Nov", "Mon 23 Nov", "Tue 24 Nov"]
    assert found["lines"] == ["Week 3 of 8, from Wed 4 Nov."]


def test_not_started_the_card_says_get_routines_words_and_draws_no_table(client, me):
    save(me, "2026-11-23")
    found = card(page_of(client))
    assert found["lines"] == ["PPL starts on Mon 23 Nov. First: Push day."] and found["rows"] == []
    assert found["after"] == [] and found["h2"] == "PPL"
    assert services.get_routines(me)["program"]["words"] == found["lines"][0]  # the service's own words
    save(me, "2026-11-24")
    assert card(page_of(client))["lines"] == ["PPL starts on Tue 24 Nov. First: Pull day on Wed 25 Nov."]


def test_ended_the_card_says_get_routines_words_and_draws_no_table(client, me):
    save(me, "2026-11-02", [{"weeks": 1, "days": PPL_DAYS}])
    found = card(page_of(client))
    assert found["lines"] == ["PPL ended on Sun 8 Nov, after 1 week."] and found["rows"] == []
    assert found["after"] == [] and "<table" not in CARD.search(page_of(client)).group(2)


def test_the_last_routine_day_says_no_routine_day_is_left(client, me):
    save(me, "2026-11-16", [{"weeks": 1, "days": PPL_DAYS}])
    found = card(page_of(client))
    assert found["lines"] == ["Week 1 of 1, from Mon 16 Nov."] and found["rows"] == marked(PPL_WEEK, "Fri 20 Nov")
    assert found["after"] == ["No routine day is left after today."]


def test_a_day_whose_routine_is_not_live_keeps_its_name_and_says_it_is_not_saved_now(client, me, clock):
    save(me)
    [pull] = [r for r in services.get_routines(me)["routines"] if r["name"] == "Pull day"]
    services.delete_entry(me, "routine", pull["id"])
    at(clock, date(2026, 11, 16))
    found = card(page_of(client))
    assert found["rows"] == marked(PPL_WEEK, "Mon 16 Nov") and ("Wed 18 Nov", "Pull day", False) in found["rows"]
    assert found["after"] == ["Next: Pull day on Wed 18 Nov.", "Pull day is not saved now."]
    assert '<p class="small">Pull day is not saved now.</p>' in page_of(client)
    at(clock, date(2026, 11, 20))  # still this week's: said under the table though neither today nor the next
    assert card(page_of(client))["after"] == ["Next: Push day on Mon 23 Nov.", "Pull day is not saved now."]


def test_with_no_live_routine_the_card_still_shows_above_the_empty_card(client, me):
    made = save(me)
    for r in services.get_routines(me)["routines"]:
        services.delete_entry(me, "routine", r["id"])
    page = page_of(client)
    found = card(page)
    assert found["id"] == made and found["rows"] == marked(PPL_WEEK, "Fri 20 Nov")
    assert found["after"] == ["Next: Push day on Mon 23 Nov.", "Push day is not saved now.",
                              "Pull day is not saved now.", "Legs day is not saved now."]
    assert "No routines yet." in page and page.index("program-") < page.index("No routines yet.")


def test_the_card_s_day_carries_its_year_in_another_year(client, me, clock):
    save(me, "2026-12-21")
    at(clock, date(2027, 1, 1))
    found = card(page_of(client))
    assert found["lines"] == ["Week 2 of 8, from Mon 21 Dec 2026."]
    assert found["rows"][0] == ("Mon 28 Dec 2026", "Push day", False) and found["rows"][4][0] == "Fri 1 Jan Today"


# ---------------------------------------------------------------- Delete and Undo (decision 19)

def test_delete_deletes_at_once_and_the_strip_s_undo_brings_it_back(client, me):
    made = save(me)
    landed = location(post(client, f"/routines/program/{made}/delete"))
    assert landed.endswith(f"/routines?deleted=program:{made}") and table()[0]["deleted_at"] is not None
    page = client.get(landed).text
    assert "Program deleted." in page and card(page) is None
    token = re.search(rf'action="/routines/program/{made}/restore" class="inline">\s*'
                      r'<input type="hidden" name="csrf_token" value="([^"]+)">', page).group(1)
    back = client.post(f"/routines/program/{made}/restore", data={"csrf_token": token})
    assert location(back).endswith("/routines?restored=program") and table()[0]["deleted_at"] is None
    restored = client.get(location(back)).text
    assert '<p class="ok">Program restored.</p>' in restored and card(restored)["id"] == made
    assert [(r.function, r.caller, r.tried) for r in rows()[-2:]] == [
        ("delete_entry", "web", "entry_id kind=program"), ("restore_entry", "web", "entry_id kind=program")]


def test_with_deleting_off_the_person_s_own_delete_still_deletes_as_a_routine_s_does(client, me):
    assert "error" not in services.set_claude_can(me, "log")
    made = save(me)
    routine = services.get_routines(me)["routines"][0]["id"]
    assert location(post(client, f"/routines/program/{made}/delete")).endswith(f"deleted=program:{made}")
    assert location(post(client, f"/routines/{routine}/delete")).endswith(f"deleted=routine:{routine}")
    assert table()[0]["deleted_at"] is not None
    assert location(post(client, f"/routines/program/{made}/restore")).endswith("restored=program")


def test_an_undo_of_a_live_program_says_so_and_changes_nothing(client, me):
    made = save(me)
    before = table()
    r = post(client, f"/routines/program/{made}/restore")
    assert r.status_code == 409 and "Already back" in r.text
    assert "That program is not deleted, so there was nothing to undo." in r.text
    assert "Go back to Routines to see it." in r.text and table() == before


def test_another_person_s_id_and_a_missing_or_bad_one_are_refused_on_both_routes(client, me, make_user):
    mine = save(me)
    other = make_user().id
    assert "error" not in services.save_routine(other, {"name": "Secret day", "lifts": PUSH}, "web")
    theirs = services.save_program(other, {"name": "Secret plan", "start_on": "2026-11-02",
                                           "blocks": [{"weeks": 3, "days": [{"weekday": "monday",
                                                                             "routine": "Secret day"}]}]}, "web")["id"]
    before = table()
    for path in (f"/routines/program/{mine}/delete", f"/routines/program/{mine}/restore"):
        assert client.post(path).status_code == 403
        assert client.post(path, data={"csrf_token": "wrong"}).status_code == 403
    r = post(client, f"/routines/program/{theirs}/delete")  # theirs live: a delete of it is refused, nothing changes
    assert r.status_code == 404 and "Secret" not in r.text and table() == before
    assert all(row["deleted_at"] is None for row in table())
    services.delete_entry(other, "program", theirs)  # theirs deleted: an Undo of it is refused too
    before = table()
    for path in (f"/routines/program/{theirs}/restore", "/routines/program/999/delete",
                 "/routines/program/abc/delete", "/routines/program/abc/restore"):
        r = post(client, path)
        assert r.status_code == 404 and "Secret" not in r.text, path
    assert table() == before and "Secret" not in page_of(client)
    for path in (f"/routines/program/{mine}/delete", f"/routines/program/{mine}/restore"):  # a GET: as Foods'
        refused, foods = client.get(path), client.get(f"/foods/{mine}/" + path.rsplit("/", 1)[1])
        assert (refused.status_code, refused.text) == (foods.status_code, foods.text) and refused.status_code == 404
    assert table() == before


def test_signed_out_the_two_posts_are_refused_as_every_post(client):
    for path in ("/routines/program/1/delete", "/routines/program/1/restore"):
        assert client.post(path).status_code == 403


def test_the_strip_reads_only_its_own_marks(client, me):
    made = save(me)
    for mark in ("?deleted=program:abc", "?deleted=program:0", "?deleted=programme:5", "?restored=programs",
                 "?deleted=program:" + "9" * 11):
        page = page_of(client, mark)
        assert 'class="ok' not in page and "restore" not in page, mark
    page = page_of(client, f"?deleted=program:{made}")
    assert f'action="/routines/program/{made}/restore"' in page and "Program deleted." in page
    assert web.routine_strip({"deleted": "program:7"}) == {"words": "Program deleted.", "undo": "program/7"}
    assert web.routine_strip({"deleted": "routine:7"}) == {"words": "Routine deleted.", "undo": "7"}
    assert web.routine_strip({"restored": "program"}) == {"words": "Program restored.", "undo": None}


def test_the_two_routes_are_posts_beside_the_routine_s():
    assert ("/routines/program/{program_id}/delete", web.program_delete_page, ["POST"]) in web.PAGES
    assert ("/routines/program/{program_id}/restore", web.program_restore_page, ["POST"]) in web.PAGES


# ---------------------------------------------------------------- the page rules, every state

STATES = {
    "running": ("2026-11-02", None, date(2026, 11, 20)),
    "rest_day": ("2026-11-02", None, date(2026, 11, 21)),
    "not_started": ("2026-11-23", None, date(2026, 11, 20)),
    "ended": ("2026-11-02", [{"weeks": 1, "days": PPL_DAYS}], date(2026, 11, 20)),
    "not_saved_now": ("2026-11-02", None, date(2026, 11, 16)),
    "no_routine": ("2026-11-02", None, date(2026, 11, 20)),
}


@pytest.mark.parametrize("state", sorted(STATES))
def test_the_page_with_the_card_and_its_landings_keep_every_rule_the_routines_page_keeps(client, me, clock, state):
    start, blocks, today = STATES[state]
    made = save(me, start, blocks)
    if state == "not_saved_now":
        services.delete_entry(me, "routine", [r for r in services.get_routines(me)["routines"]
                                              if r["name"] == "Pull day"][0]["id"])
    if state == "no_routine":
        for r in services.get_routines(me)["routines"]:
            services.delete_entry(me, "routine", r["id"])
    at(clock, today)
    zone, iso = not only_user().time_zone, today.isoformat()
    seen = [Seen("/routines" + mark, page_of(client, mark), True, zone_missing=zone, today=iso)
            for mark in ("", "?restored=program", f"?deleted=program:{made}")]
    landed = [follow(client, post(client, f"/routines/program/{made}/delete")),
              follow(client, post(client, f"/routines/program/{made}/restore"))]
    seen += [Seen(address, markup, True, zone_missing=zone, today=iso) for address, markup in landed]
    refused = post(client, f"/routines/program/{made}/restore")
    assert refused.status_code == 409
    seen.append(Seen(f"/routines/program/{made}/restore", refused.text, True, zone_missing=zone, message=True,
                     calm=False, today=iso))
    entered = entered_text(me) + ["PPL"]  # the program's name is the person's own text, as a routine's
    names, notes = names_of(me), entered_notes(me)
    findings = []
    for s in seen:
        findings += frame_findings(s) + page_rule_findings(s) + titled_card_findings(s)
        if not s.message:
            findings += built_findings(s, me)
        findings += page_findings(s.address, s.markup, entered, me, route=None)
        findings += step_4_findings(s.address, s.markup, names, notes, message=s.message, entered=entered)
    assert not findings, "\n".join(findings)
    assert card(seen[0].markup)["id"] == made and card(seen[3].markup) is None  # the landing after the delete


def test_the_card_s_own_words_keep_the_text_rules():
    for text in (web.PROGRAM_REST, web.PROGRAM_NONE_LEFT, "Week 3 of 8, from Mon 2 Nov.",
                 "Next: Push day on Mon 23 Nov.", "Program deleted.", "Program restored.",
                 "That program is not deleted, so there was nothing to undo."):
        assert tr.check(text, tr.PAGE) == [], text
