"""Stage 5 on the web, the account side: sample data on Today and Settings, the sample banner and tags,
the saved-food line, Foods through list_saved_foods, Export my data, Delete my account, and the
provider seam's delete_identity.

The page tests replace the services with the contract's result shapes. A few run the real services
end to end.
"""

import asyncio
import inspect
import io
import re
import zipfile
from datetime import timedelta

import pytest
import workos
from sqlalchemy import select
from workos.user_management._resource import AsyncUserManagement

from app import db, services, web, web_auth
from app.models import Bodyweight, CallLog, User

from .conftest import settings
from .test_web import (  # noqa: F401 - fixtures
    CSP, SITE, TOO_MANY, WEB, FakeSeam, calls, client, only_user, post, seam, session_of, sign_in,
    stock, stock_food, today_of, user_count, web_client,
)


BANNER = "Sample data is on this account. Remove it on Settings."


def plain(page: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", page))


@pytest.fixture
def signed(client):
    sign_in(client)
    return client


@pytest.fixture
def sample_state(monkeypatch):
    """sample_state(empty, rows): what the pages are told about sample data."""
    def install(empty: bool, rows: int):
        monkeypatch.setattr(services, "sample_state", lambda *a: {"empty": empty, "sample_rows": rows}, raising=False)

    return install


def recorder(monkeypatch, name: str, answer: dict) -> list:
    sent = []
    monkeypatch.setattr(services, name, lambda *args: sent.append(args) or dict(answer), raising=False)
    return sent


# ---------------------------------------------------------------- Add sample data on an empty Today

def test_an_empty_today_offers_sample_data(signed):
    today = today_of(only_user()).isoformat()
    page = signed.get(f"/day/{today}").text
    form = re.search(r'<form method="post" action="/settings/sample">(.*?)</form>', page, re.S).group(1)
    assert f'name="csrf_token" value="{session_of(signed)["csrf"]}"' in form and "Add sample data" in form
    assert BANNER not in plain(page)


def test_sample_data_is_offered_only_on_an_empty_accounts_today(signed, sample_state):
    today = today_of(only_user())
    yesterday = (today - timedelta(days=1)).isoformat()
    assert 'action="/settings/sample"' not in signed.get(f"/day/{yesterday}").text  # not on another day
    services.log_bodyweight(only_user().id, {"kg": "80", "date": yesterday}, "claude")
    assert 'action="/settings/sample"' not in signed.get(f"/day/{today.isoformat()}").text  # not once anything is there
    sample_state(empty=True, rows=0)
    assert 'action="/settings/sample"' in signed.get(f"/day/{today.isoformat()}").text  # the service decides


def test_add_sample_data_goes_through_the_write_path_then_today(signed, monkeypatch):
    sent = recorder(monkeypatch, "add_sample_data", {"added": {}, "from": "", "to": "", "summary": "", "links": {}})
    r = post(signed, "/settings/sample")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/today"
    assert sent == [(only_user().id, "web")]
    assert calls(only_user().id) == [("add_sample_data", True, None)]


@pytest.mark.parametrize("code, says", [
    ("not_empty", "Sample data goes only into an empty account"),
    ("already_sample", "Sample data is already on this account."),
    ("invalid_field", "Sample data could not be added, so nothing was changed."),
])
def test_a_refused_sample_add_says_why_in_the_pages_words(signed, monkeypatch, code, says):
    recorder(monkeypatch, "add_sample_data", {"error": code, "message": "For Claude: not_empty.", "fix": "For Claude."})
    r = post(signed, "/settings/sample")
    assert r.status_code in (400, 409) and says in r.text and "For Claude" not in r.text
    assert calls(only_user().id) == [("add_sample_data", False, code)]


def test_settings_offers_remove_only_with_sample_rows(signed, sample_state):
    assert 'action="/settings/sample/remove"' not in signed.get("/settings").text
    sample_state(empty=False, rows=12)
    page = signed.get("/settings").text
    form = re.search(r'<form method="post" action="/settings/sample/remove">(.*?)</form>', page, re.S).group(1)
    assert f'value="{session_of(signed)["csrf"]}"' in form and "Remove sample data" in form


def test_remove_sample_data_goes_through_the_write_path_then_today(signed, monkeypatch):
    sent = recorder(monkeypatch, "remove_sample_data", {"removed": {}, "summary": "", "links": {}})
    r = post(signed, "/settings/sample/remove")
    assert r.status_code == 303 and r.headers["location"] == SITE + "/today"
    assert sent == [(only_user().id, "web")] and calls(only_user().id) == [("remove_sample_data", True, None)]


def test_a_refused_remove_says_so(signed, monkeypatch):
    recorder(monkeypatch, "remove_sample_data", {"error": "invalid_field", "message": "x", "fix": "y"})
    r = post(signed, "/settings/sample/remove")
    assert r.status_code == 400 and "could not be removed, so nothing was changed" in r.text


# ---------------------------------------------------------------- the banner and the tags

def test_every_page_with_entries_carries_the_banner_when_sample_rows_exist(signed, sample_state):
    today = today_of(only_user()).isoformat()
    pages = (f"/day/{today}", "/progress", "/goals", "/foods")
    for path in pages:
        assert BANNER not in plain(signed.get(path).text), path
    sample_state(empty=False, rows=3)
    for path in pages:
        page = signed.get(path).text
        assert BANNER in plain(page) and '<p class="notice sample-banner">' in page, path
        assert 'Remove it on <a href="/settings">Settings</a>.' in page, path
    assert 'action="/settings/sample"' not in signed.get(f"/day/{today}").text  # no offer on a non-empty account


def test_each_sample_row_carries_a_sample_tag(signed, monkeypatch):
    uid = only_user().id
    stock_food(only_user())
    services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 60, "reps": 5}]}, "claude")
    services.log_measurement(uid, {"kind": "waist", "value": "84"}, "claude")
    today = today_of(only_user()).isoformat()
    assert "tag-sample" not in signed.get(f"/day/{today}").text
    real = services.get_log

    def marked(*args):
        result = real(*args)
        for day in result["days"]:
            rows = [*day["sessions"], *day["measurements"], *(day.get("cardio") or []),
                    *(f for meal in ((day.get("food") or {}).get("meals") or {}).values() for f in meal)]
            rows += [day["weigh_in"]] if day.get("weigh_in") else []
            for row in rows:
                row["is_sample"] = True
        return result

    monkeypatch.setattr(services, "get_log", marked)
    page = signed.get(f"/day/{today}").text
    # a session, a weigh-in, a measurement, three food entries, one cardio entry
    assert page.count('<span class="tag tag-sample">sample</span>') == 7
    assert re.search(r'<strong>Session</strong> <span class="tag tag-sample">sample</span>', page)
    assert re.search(r'<strong>80 kg</strong> <span class="tag tag-sample">sample</span>', page)
    assert re.search(r'<td>Treadmill Run <span class="tag tag-sample">sample</span></td>', page)


def test_real_sample_data_fills_today_and_goes_in_one_click(signed):
    today = today_of(only_user()).isoformat()
    assert post(signed, "/settings/sample").status_code == 303
    page = signed.get(f"/day/{today}").text
    assert BANNER in plain(page) and "tag-sample" in page and 'action="/settings/sample"' not in page
    assert BANNER in plain(signed.get("/goals").text) and signed.get("/goals").text.count('value="hit"') == 3
    assert 'action="/settings/sample/remove"' in signed.get("/settings").text
    assert post(signed, "/settings/sample/remove").status_code == 303
    page = signed.get(f"/day/{today}").text
    assert BANNER not in plain(page) and "tag-sample" not in page and 'action="/settings/sample"' in page


# ---------------------------------------------------------------- the saved-food line (decision 14)

@pytest.mark.parametrize("servings, label, says", [
    (2, "1 roti", "2 rotis"), (1, "1 roti", "1 roti"), (1.5, "1 bowl", "1.5 bowls"), (2, "1 cup", "2 cups"),
    (2, "1 medium apple", "2 medium apples"), (2, "1 large egg", "2 large eggs"), (3, "1 small roti", "3 small rotis"),
    (2, "1 oz", "2 × 1 oz"), (2, "1 g", "2 × 1 g"), (2, "1 tbsp", "2 × 1 tbsp"), (2, "1 tsp", "2 × 1 tsp"),
    (2, "1 ml", "2 × 1 ml"), (2, "1 tomato", "2 × 1 tomato"), (2, "1 child", "2 × 1 child"),
    (2, "1 glass", "2 × 1 glass"), (3, "1 patty", "3 × 1 patty"), (2, "1 medium", "2 × 1 medium"),
    (2, "1 bowl dal", "2 × 1 bowl dal"), (2, "100 g", "2 × 100 g"), (1, "100 g", "100 g"), (1, "1 roti (40 g)", "1 roti (40 g)"),
    (0.5, "half plate", "0.5 × half plate"), (2, None, "2"), (2, "", "2"), (2, "  ", "2"),
])
def test_the_serving_in_its_own_words(servings, label, says):
    assert web.serving_text(servings, label) == says


@pytest.mark.parametrize("description, name, same", [
    ("Roti", "Roti", True), ("roti", "Roti", True), (" ROTI ", "Roti", True), ("Two rotis", "Roti", False),
    (None, "Roti", False),
])
def test_a_description_that_only_repeats_the_name_counts_as_the_name(description, name, same):
    assert web.same_name(description, name) is same


def test_a_lower_case_description_is_not_shown_beside_its_name(signed):
    uid = only_user().id
    fed = stock_food(only_user())
    services.log_food(uid, {"entries": [{"description": "roti", "saved_food_id": fed["roti"], "servings": 1,
                                         "meal": "dinner"}]}, "claude")
    dinner = re.search(r"<h3>Dinner</h3>(.*?)</ul>", signed.get(f"/day/{fed['today']}").text, re.S).group(1)
    assert re.search(r'<span>Roti × 1 <span class="muted">\(1 roti\)</span>:', dinner)
    assert '<span class="muted">roti</span>' not in dinner


def lunch_of(client, today: str) -> str:
    return re.search(r"<h3>Lunch</h3>(.*?)</ul>", client.get(f"/day/{today}").text, re.S).group(1)


def test_a_saved_food_line_shows_a_different_description_once(signed):
    uid = only_user().id
    fed = stock_food(only_user())
    services.log_food(uid, {"entries": [{"description": "Two rotis with ghee", "saved_food_id": fed["roti"],
                                         "servings": 2, "meal": "lunch"}]}, "claude")
    lunch = lunch_of(signed, fed["today"])
    assert re.search(r'Roti × 2 <span class="muted">\(2 rotis\)</span>:', lunch)  # the description is the name
    assert re.search(r'Roti × 2 <span class="muted">\(2 rotis\)</span> <span class="muted">Two rotis with ghee</span>:',
                     lunch)


def test_a_deleted_saved_food_still_names_its_entries(signed):
    fed = stock_food(only_user())
    services.delete_entry(only_user().id, "saved_food", fed["roti"])
    assert re.search(r'Roti × 2 <span class="muted">\(2 rotis\)</span>:', lunch_of(signed, fed["today"]))


def test_a_food_with_no_saved_food_is_its_description(signed):
    fed = stock_food(only_user())
    assert re.search(r"<span>Dal:", lunch_of(signed, fed["today"]))


# ---------------------------------------------------------------- Foods reads list_saved_foods

FOODS = {"foods": [
    {"id": 11, "name": "Dal bowl", "serving_label": "1 bowl", "kcal_per_serving": 180, "grams_per_serving": None,
     "times_logged": 7, "last_logged": "2026-11-09", "is_sample": True, "deleted_at": None},
    {"id": 12, "name": "Roti", "serving_label": "1 roti", "kcal_per_serving": 120, "grams_per_serving": 40,
     "times_logged": 0, "last_logged": None, "is_sample": False, "deleted_at": None},
    {"id": 13, "name": "Gone stew", "serving_label": "1 bowl", "kcal_per_serving": 300, "grams_per_serving": None,
     "times_logged": 2, "last_logged": "2026-10-01", "is_sample": False, "deleted_at": "2026-11-01T00:00:00+00:00"},
], "summary": ""}


def test_foods_reads_list_saved_foods_and_shows_live_ones(signed, monkeypatch):
    sent = recorder(monkeypatch, "list_saved_foods", FOODS)
    page = signed.get("/foods").text
    assert sent == [(only_user().id,)]
    dal = re.search(r'<tr>\s*<td>Dal bowl <span class="tag tag-sample">sample</span></td>(.*?)</tr>', page, re.S)
    assert dal and re.findall(r"<td>(.*?)</td>", dal.group(1), re.S)[:3] == ["1 bowl", "180", "7"]
    assert "<td>Roti</td>" in page and "Gone stew" not in page
    assert signed.get("/foods/12/edit").status_code == 200 and signed.get("/foods/13/edit").status_code == 404


# ---------------------------------------------------------------- Export my data

EXPORT = {"files": {"account.csv": "account_id,email\n1,a@example.com\n", "lift_sessions.csv": "id,date\n",
                    "README.txt": "What each file holds.\n"},
          "rows": {"account.csv": 1, "lift_sessions.csv": 0}, "summary": ""}


def test_export_answers_a_zip_of_every_file(signed, monkeypatch):
    sent = recorder(monkeypatch, "export_data", EXPORT)
    r = post(signed, "/settings/export")
    assert r.status_code == 200 and sent == [(only_user().id,)]
    assert r.headers["content-type"] == "application/zip"
    today = today_of(only_user()).isoformat()
    assert r.headers["content-disposition"] == f'attachment; filename="spotter-export-{today}.zip"'
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        assert archive.namelist() == list(EXPORT["files"])
        for name, text in EXPORT["files"].items():
            assert archive.read(name).decode("utf-8") == text
    assert calls(only_user().id) == []  # a read: no write row, nothing counted against the limit


def test_export_keeps_only_each_files_own_name():
    data = web.export_zip({"../../evil.csv": "x", "a\\b.csv": "y", "": "z"})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.namelist() == ["evil.csv", "b.csv", "file.txt"]


def test_a_failed_export_says_so(signed, monkeypatch):
    recorder(monkeypatch, "export_data", {"error": "no_account", "message": "x", "fix": "y"})
    r = post(signed, "/settings/export")
    assert r.status_code == 500 and "could not put your data together" in r.text


def test_export_is_a_post_with_the_token_only(signed, monkeypatch):
    sent = recorder(monkeypatch, "export_data", EXPORT)
    assert signed.get("/settings/export").status_code in (404, 405)  # no GET: a link can never start a download
    for data in ({}, {"csrf_token": "wrong"}):
        assert signed.post("/settings/export", data=data).status_code == 403
    assert sent == []
    page = signed.get("/settings").text
    form = re.search(r'<form method="post" action="/settings/export">(.*?)</form>', page, re.S).group(1)
    assert f'value="{session_of(signed)["csrf"]}"' in form and "Export my data" in form


def test_the_real_export_has_every_file(signed):
    stock(only_user())
    r = post(signed, "/settings/export")
    with zipfile.ZipFile(io.BytesIO(r.content)) as archive:
        names = set(archive.namelist())
    assert {"account.csv", "lift_sessions.csv", "lift_sets.csv", "bodyweight.csv", "measurements.csv", "goals.csv",
            "saved_foods.csv", "food_entries.csv", "cardio_entries.csv", "README.txt"} <= names


# ---------------------------------------------------------------- Delete my account: the confirm page

def test_the_confirm_page_says_what_goes_by_count(signed, make_user):
    uid = only_user().id
    stocked = stock(only_user())
    stock_food(only_user(), weigh_in=False)
    services.delete_entry(uid, "bodyweight", stocked["old_weigh_in"])  # a deleted row goes too
    services.set_goal(uid, {"kind": "bodyweight", "target_value": 70, "target_date": "2099-01-01"}, "claude")
    other = make_user()
    services.log_bodyweight(other.id, {"kg": "90"}, "claude")
    page = signed.get("/settings/delete").text
    counts = dict(re.findall(r'<li class="entry-line"><span>([^<]+)</span><strong>(\d+)</strong></li>', page))
    assert counts == {"Lift sessions": "2", "Weigh-ins": "2", "Measurements": "3", "Food entries": "3",
                      "Cardio entries": "1", "Saved foods": "1", "Goals": "1"}
    assert "so does your sign-in" in page and "This cannot be undone." in page
    form = re.search(r'<form method="post" action="/settings/delete">(.*?)</form>', page, re.S).group(1)
    assert f'value="{session_of(signed)["csrf"]}"' in form and "Delete my account" in form
    assert 'href="/settings/delete"' in signed.get("/settings").text


# ---------------------------------------------------------------- Delete my account: the delete

@pytest.fixture
def provider_delete(monkeypatch):
    """The seam's delete_identity, recorded. `fail` makes it raise that exception."""
    state = {"subjects": [], "fail": None, "events": None}

    async def fake(subject):
        state["subjects"].append(subject)
        if state["events"] is not None:
            state["events"].append("provider")
        if state["fail"] is not None:
            raise state["fail"]

    monkeypatch.setattr(web_auth, "delete_identity", fake)
    return state


def contract_delete(events: list | None = None):
    """Stands in for delete_account as the contract has it: call-log rows kept with no user, then the user
    row and everything it owns (the schema's cascade)."""
    def run(user_id):
        if events is not None:
            events.append("local")
        with db.session_scope() as s:
            for row in s.scalars(select(CallLog).where(CallLog.user_id == user_id)):
                row.user_id = None
            s.delete(s.get(User, user_id))
        return {"deleted": True, "rows": {}}

    return run


def test_delete_removes_the_sign_in_first_then_the_rows_then_the_session(signed, provider_delete, monkeypatch):
    events = provider_delete["events"] = []
    monkeypatch.setattr(services, "delete_account", contract_delete(events), raising=False)
    stock(only_user())
    r = post(signed, "/settings/delete")
    assert r.status_code == 200 and "<h1>Your account is deleted</h1>" in r.text
    assert "Signing in again starts a new, empty account." in r.text
    assert events == ["provider", "local"] and provider_delete["subjects"] == ["user_01WEB"]
    assert user_count() == 0
    assert session_of(signed) == {}  # the session cookie is cleared
    assert "Sign out" not in r.text and 'href="/settings"' not in r.text and 'name="csrf_token"' not in r.text
    assert 'href="/sign-in"' in r.text and "provider.example/logout" not in r.text
    assert signed.get("/settings").headers["location"] == SITE + "/sign-in"
    with db.session_scope() as s:
        logged = [(c.function, c.ok, c.user_id) for c in s.scalars(select(CallLog))]
    assert ("delete_account", True, None) in logged  # the row stays for the counts, with no user


def test_signing_in_again_after_a_delete_starts_a_new_account(signed, provider_delete, monkeypatch):
    monkeypatch.setattr(services, "delete_account", contract_delete(), raising=False)
    services.log_bodyweight(only_user().id, {"kg": "80"}, "claude")
    post(signed, "/settings/delete")
    sign_in(signed)
    assert user_count() == 1  # SQLite may hand the new row the old id; what matters is that it is empty
    with db.session_scope() as s:
        assert s.scalars(select(Bodyweight)).all() == []
    assert 'action="/settings/sample"' in signed.get(f"/day/{today_of(only_user()).isoformat()}").text


@pytest.mark.parametrize("failure", [web_auth.DeleteFailed("ServerError"), RuntimeError("anything")],
                         ids=["delete failed", "any exception"])
def test_a_provider_failure_deletes_nothing(signed, provider_delete, monkeypatch, failure):
    provider_delete["fail"] = failure
    sent = recorder(monkeypatch, "delete_account", {"deleted": True, "rows": {}})
    r = post(signed, "/settings/delete")
    assert r.status_code == 502 and "could not delete your sign-in, so nothing was deleted" in r.text
    assert "Try again in a few minutes." in r.text
    assert sent == [] and user_count() == 1 and calls(only_user().id) == []
    assert signed.get("/settings").status_code == 200  # still signed in


@pytest.mark.parametrize("local", ["refused", "raises"])
def test_a_local_failure_says_the_sign_in_is_gone_and_the_session_still_works(web_client, provider_delete,
                                                                              monkeypatch, local):
    client = web_client(raise_server_exceptions=False)
    sign_in(client)
    if local == "raises":
        def boom(user_id):
            raise RuntimeError("bug")
        monkeypatch.setattr(services, "delete_account", boom, raising=False)
    else:
        recorder(monkeypatch, "delete_account", {"error": "invalid_field", "message": "x", "fix": "y"})
    r = post(client, "/settings/delete")
    assert "Your sign-in is deleted, but your data here is not deleted yet." in r.text
    assert "you are still signed in here" in r.text and "Do not sign out until this finishes." in r.text
    assert r.status_code == 500 and "Try again now" in r.text
    assert provider_delete["subjects"] == ["user_01WEB"] and user_count() == 1
    assert calls(only_user().id)[-1][0] == "delete_account" and calls(only_user().id)[-1][1] is False
    assert client.get("/settings").status_code == 200  # the account row is still there, so is the session


def test_a_retry_after_a_local_failure_finishes(signed, provider_delete, monkeypatch):
    recorder(monkeypatch, "delete_account", {"error": "invalid_field", "message": "x", "fix": "y"})
    assert post(signed, "/settings/delete").status_code == 500
    monkeypatch.setattr(services, "delete_account", contract_delete(), raising=False)
    r = post(signed, "/settings/delete")  # the provider answers "no such user" now: the seam counts it done
    assert r.status_code == 200 and "Your account is deleted" in r.text and user_count() == 0
    assert provider_delete["subjects"] == ["user_01WEB", "user_01WEB"]


def test_the_real_delete(signed, provider_delete, make_user):
    stock(only_user())
    other = make_user()
    services.log_bodyweight(other.id, {"kg": "90"}, "claude")
    assert post(signed, "/settings/delete").status_code == 200
    with db.session_scope() as s:
        assert s.get(User, other.id) is not None and s.scalars(select(User)).all() != []
        assert [b.kg for b in s.scalars(select(Bodyweight))] == [90]
        assert ("delete_account", None) in [(c.function, c.user_id) for c in s.scalars(select(CallLog))]


def test_delete_needs_the_csrf_token_and_a_post(signed, provider_delete, monkeypatch):
    sent = recorder(monkeypatch, "delete_account", {"deleted": True, "rows": {}})
    for data in ({}, {"csrf_token": "wrong"}):
        assert signed.post("/settings/delete", data=data).status_code == 403
    assert provider_delete["subjects"] == [] and sent == [] and user_count() == 1


# ---------------------------------------------------------------- the write limit and CSRF on every new route

@pytest.mark.parametrize("name, path", [
    ("add_sample_data", "/settings/sample"),
    ("remove_sample_data", "/settings/sample/remove"),
])
def test_each_new_write_over_the_limit_is_a_429(signed, provider_delete, monkeypatch, name, path):
    monkeypatch.setattr(services, name, lambda *args: dict(TOO_MANY), raising=False)
    r = post(signed, path)
    assert r.status_code == 429 and "Try again in 3 minutes" in r.text
    assert calls(only_user().id)[-1] == (name, False, "too_many_writes")


def test_delete_goes_through_at_the_write_limit(signed, provider_delete):
    """The person can always leave: a full write limit never stops Delete my account."""
    uid = only_user().id
    now = db.utcnow()
    with db.session_scope() as s:
        s.add_all(CallLog(function="log_bodyweight", started_at=now, ms=1, ok=True, error_code=None, result_bytes=1,
                          user_id=uid) for _ in range(services.WRITE_LIMIT + 5))
    assert post(signed, "/day/" + today_of(only_user()).isoformat() + "/weigh-in", kg="80").status_code == 429
    r = post(signed, "/settings/delete")
    assert r.status_code == 200 and "Your account is deleted" in r.text and user_count() == 0
    assert provider_delete["subjects"] == ["user_01WEB"]


@pytest.mark.parametrize("path, name", [("/settings/sample", "add_sample_data"),
                                        ("/settings/sample/remove", "remove_sample_data")])
def test_the_sample_forms_need_the_csrf_token(signed, monkeypatch, path, name):
    sent = recorder(monkeypatch, name, {})
    for data in ({}, {"csrf_token": "wrong"}):
        assert signed.post(path, data=data).status_code == 403
    assert sent == [] and calls(only_user().id) == []


def test_the_new_pages_carry_no_script_and_the_same_policy(signed):
    services.update_settings(only_user().id, {"time_zone": "Asia/Kolkata"}, "web")
    for path in ("/settings", "/settings/delete", "/goals", "/progress", f"/day/{today_of(only_user()).isoformat()}"):
        r = signed.get(path)
        assert r.headers["content-security-policy"] == CSP and "<script" not in r.text, path
        assert re.search(r"<[^>]+\son[a-z]+\s*=", r.text, re.I) is None and "style=" not in r.text, path


# ---------------------------------------------------------------- the seam: delete_identity

class FakeWorkOS:
    """Stands in for the provider's async client: records what it was built with and who it deleted."""
    built: list[dict] = []
    deleted: list[str] = []
    closed = 0
    fail: Exception | None = None

    def __init__(self, **values):
        FakeWorkOS.built.append(values)
        self.user_management = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        FakeWorkOS.closed += 1

    async def delete_user(self, id):  # noqa: A002 - the library's own name for it
        FakeWorkOS.deleted.append(id)
        if FakeWorkOS.fail is not None:
            raise FakeWorkOS.fail


@pytest.fixture
def fake_workos(monkeypatch, app_settings):
    from app import config

    config.use_settings(settings(**WEB))
    FakeWorkOS.built, FakeWorkOS.deleted, FakeWorkOS.closed, FakeWorkOS.fail = [], [], 0, None
    monkeypatch.setattr(web_auth, "AsyncWorkOSClient", FakeWorkOS)
    return FakeWorkOS


def test_the_library_deletes_a_user_by_id():
    params = inspect.signature(AsyncUserManagement.delete_user).parameters
    assert "id" in params and "user_id" not in params


def test_delete_identity_deletes_the_user_at_the_provider(fake_workos):
    assert asyncio.run(web_auth.delete_identity("user_01GONE")) is None
    assert fake_workos.deleted == ["user_01GONE"] and fake_workos.closed == 1
    [values] = fake_workos.built
    assert values["api_key"] == WEB["workos_api_key"] and values["request_timeout"] == 10 and values["max_retries"] == 0


def test_no_such_user_at_the_provider_counts_as_done(fake_workos, logs):
    fake_workos.fail = workos.NotFoundError("User not found", status_code=404)
    assert asyncio.run(web_auth.delete_identity("user_01GONE")) is None
    assert "already gone" in logs.text


@pytest.mark.parametrize("failure", [workos.ServerError("down", status_code=500),
                                     workos.AuthenticationError("bad key", status_code=401),
                                     OSError("network")], ids=["server", "auth", "network"])
def test_any_other_provider_failure_raises_delete_failed_logged_by_type(fake_workos, logs, failure):
    fake_workos.fail = failure
    with pytest.raises(web_auth.DeleteFailed):
        asyncio.run(web_auth.delete_identity("user_01SECRET"))
    assert type(failure).__name__ in logs.text and "user_01SECRET" not in logs.text and "bad key" not in logs.text


def test_delete_identity_has_a_time_limit(monkeypatch, logs):
    async def slow(subject):
        await asyncio.sleep(5)

    monkeypatch.setattr(web_auth, "_delete_user", slow)
    monkeypatch.setattr(web_auth, "EXCHANGE_SECONDS", 0.05)
    with pytest.raises(web_auth.DeleteFailed):
        asyncio.run(web_auth.delete_identity("user_01SLOW"))
    assert "TimeoutError" in logs.text
