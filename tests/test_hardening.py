"""Hardening, package H1 (contract docs/contracts/hardening.md, decisions 1 to 9): a small request does a small amount
of work, a delete stops when deleting is turned off, the export cannot be run in a loop, and retention cleanup leaves
the calls alone.

Frozen copies of today's lifts.history, metrics.prs, metrics.goal_now_series, get_routines' weights and get_log (as
they stood at fd83753) live in this file as oracles: each new result equals the oracle's with ==, on the 16-week and
4-week seeds and on generated histories. Work is bounded by count, never by clock. No test reads the real date: the
clock is fixed (NOW), and the export gate's clock is its own seam.
"""

import random
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload
from starlette.testclient import TestClient

from app import calllog, db, formatting, links, mcp_server, metrics, rules, services, users, web
from app.models import (
    MEALS, MEASUREMENT_KINDS, Bodyweight, CallLog, CardioEntry, EntryVersion, Feedback, FoodEntry, Goal, LiftSession,
    LiftSet, Measurement, User,
)
from app.services import cardio, entries, export, food, lifts, routines
from app.services import log as log_service
from app.services.common import exercises, no_account, row_dict
from evals.four_weeks import four_weeks
from evals.sixteen_weeks import sixteen_weeks

from .conftest import sign
from .test_mcp import call, ok
from .test_routines import statements_of
from .test_web import client, only_user, post, seam, session_of, sign_in, web_client  # noqa: F401 - fixtures

NOW = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)
TODAY = date(2026, 11, 10)
START_SWEEP = calllog.start_sweep  # the real starter: tests/conftest.py puts sweep in its place for every test


@pytest.fixture
def fixed(monkeypatch):
    """The clock at NOW."""
    monkeypatch.setattr(db, "utcnow", lambda: NOW)


# ---------------------------------------------------------------- the oracles: today's code, frozen at fd83753

def old_prs(prior_sets, session_sets):
    """metrics.prs as it stood."""
    if not prior_sets:
        return []
    out = []
    prev_e, now_e = metrics.session_best(prior_sets), metrics.session_best(session_sets)
    if now_e is not None and prev_e is not None and now_e > prev_e:
        out.append({"kind": "estimated_max", "value": now_e, "previous": prev_e})
    prev_w, now_w = metrics.max_weight(prior_sets), metrics.max_weight(session_sets)
    if now_w is not None and prev_w is not None and now_w > prev_w:
        out.append({"kind": "weight", "value": now_w, "previous": prev_w})
    return out


def old_history(s, user_id, today):
    """lifts.history as it stood: every earlier set of a lift handed to prs for each session."""
    sessions = sorted(s.scalars(
        select(LiftSession).where(LiftSession.user_id == user_id, LiftSession.deleted_at.is_(None))
        .options(selectinload(LiftSession.sets).selectinload(LiftSet.exercise))),
        key=lambda t: metrics.order_key(t.date, t.id, today))
    points, logged, last, last_top, best_set, bests, prior, most_reps = {}, {}, {}, {}, {}, {}, {}, {}
    weighted = set()
    for t in sessions:
        by_lift = {}
        for x in sorted(t.sets, key=lambda x: x.id):
            by_lift.setdefault(x.exercise.name, []).append((x.weight_kg, x.reps))
            e = metrics.epley(x.weight_kg, x.reps)
            top = best_set.get(x.exercise.name)
            if e is not None and (top is None or e > top["estimated_max_kg"]):
                best_set[x.exercise.name] = {"weight_kg": x.weight_kg, "reps": x.reps,
                                             "date": t.date.isoformat(), "estimated_max_kg": e}
            if x.weight_kg is not None and x.weight_kg > 0:
                weighted.add(x.exercise.name)
            most = most_reps.get(x.exercise.name)
            if x.reps is not None and (most is None or x.reps > most["reps"]):
                most_reps[x.exercise.name] = {"reps": x.reps, "date": t.date.isoformat()}
        for lift, lift_sets in by_lift.items():
            logged[lift] = logged.get(lift, 0) + 1
            last[lift] = t.date
            last_top[lift] = {"date": t.date, "weight_kg": max(w for w, _ in lift_sets)}
            best = metrics.session_best(lift_sets)
            if best is not None:
                points.setdefault(lift, []).append((t.date, best))
            for hit in old_prs(prior.get(lift, []), lift_sets):
                bests.setdefault(lift, []).append({"date": t.date.isoformat(), "exercise": lift, **hit,
                                                   "session_id": t.id})
            prior.setdefault(lift, []).extend(lift_sets)
    best_set = {lift: {**b, "estimated_max_kg": formatting.rounded(b["estimated_max_kg"], "estimated_max")}
                for lift, b in best_set.items()}
    reps_only = {lift: most for lift, most in most_reps.items() if lift not in weighted}
    return {"sessions": sessions, "points": points, "logged": logged, "last": last, "last_top": last_top,
            "best_set": best_set, "reps_only": reps_only, "bests": bests}


def old_goal_now_series(kind, points, today, since):
    """metrics.goal_now_series as it stood: goal_now over the points up to each date."""
    ordered = metrics.in_order(points, today)
    dates = sorted({metrics.on_or_before(d, today) for d, _ in ordered if metrics.on_or_before(d, today) >= since})
    out = []
    for day in dates:
        value = metrics.goal_now(kind, [(d, v) for d, v in ordered if metrics.on_or_before(d, today) <= day], day)
        if value is not None:
            out.append((day, value))
    return out


def old_weights(s, user_id, today, names):
    """get_routines' weights as they stood: lifts.history's last_top, for the lifts its routines name."""
    tops = old_history(s, user_id, today)["last_top"]
    return {name: tops[name] for name in names if name in tops}


def old_get_log(user_id, date_=None, days=1, any_date=False):
    """get_log as it stood: every row from the window's start on, the end applied in Python, the history whole."""
    count, err = log_service._days(days)
    if err:
        return err
    with db.new_session() as s:
        user = s.get(User, user_id)
        if user is None:
            return no_account()
        today = users.today_for(user)
        units = users.units_for(user)
        if date_ is None or (isinstance(date_, str) and not date_.strip()):
            end, later_too = today, True
        else:
            end, later_too = rules.to_date(date_), False
            if end is None:
                return rules.error("invalid_date", f"date '{rules.quote(date_)}' is not a date.",
                                   "Give the date as YYYY-MM-DD, or leave it out for today.")
            if end > today:
                return rules.error("date_in_future", f"date {end.isoformat()} is after today ({today.isoformat()}).",
                                   f"Give today or an earlier date. {rules.TODAY_NOTE}")
            floor = log_service.oldest_day(user)
            if end < floor and not any_date:
                return rules.error("date_too_old", f"date {end.isoformat()} is before {floor.isoformat()}, the oldest "
                                   "day this account can hold.",
                                   f"Give a date from {floor.isoformat()} to today, or leave it out for today.")
        start = end - timedelta(days=count - 1)

        def shown(d):
            return start <= d <= end or (later_too and d > end)

        history = old_history(s, user.id, today)
        bests = {}
        for per_lift in history["bests"].values():
            for b in per_lift:
                raw = {k: b[k] for k in ("exercise", "kind", "value", "previous")}
                bests.setdefault(b["session_id"], []).append({**lifts.rounded_best(raw),
                                                              "text": lifts.best_text(raw, units)})
        sessions = [t for t in history["sessions"] if shown(t.date)]
        weigh_ins = [b for b in s.scalars(select(Bodyweight).where(
            Bodyweight.user_id == user.id, Bodyweight.deleted_at.is_(None), Bodyweight.date >= start)
            .order_by(Bodyweight.id)) if shown(b.date)]
        kinds = {k: n for n, k in enumerate(MEASUREMENT_KINDS)}
        measured = sorted((m for m in s.scalars(select(Measurement).where(
            Measurement.user_id == user.id, Measurement.deleted_at.is_(None), Measurement.date >= start))
            if shown(m.date)), key=lambda m: (kinds.get(m.kind, len(kinds)), m.id))
        eaten = [f for f in s.scalars(select(FoodEntry).where(
            FoodEntry.user_id == user.id, FoodEntry.deleted_at.is_(None), FoodEntry.date >= start)
            .order_by(FoodEntry.id).options(selectinload(FoodEntry.saved_food))) if shown(f.date)]
        moved = [c for c in s.scalars(select(CardioEntry).where(
            CardioEntry.user_id == user.id, CardioEntry.deleted_at.is_(None), CardioEntry.date >= start)
            .order_by(CardioEntry.id).options(selectinload(CardioEntry.exercise))) if shown(c.date)]
        dates = [end - timedelta(days=n) for n in range(count)]
        later = sorted({e.date for e in (*sessions, *weigh_ins, *measured, *eaten, *moved) if e.date > end},
                       reverse=True)
        out_days = []
        for day in later + dates:
            weighed = [b for b in weigh_ins if b.date == day]
            food_rows = [f for f in eaten if f.date == day]
            cardio_rows = [c for c in moved if c.date == day]
            meals = {meal: [{**food.entry_dict(f), **food.saved_parts(f)} for f in food_rows if f.meal == meal]
                     for meal in MEALS}
            day_sessions = [t for t in sessions if t.date == day]
            out_days.append({
                "date": day.isoformat(),
                "sessions": [{**lifts.session_dict(t), "new_bests": bests.get(t.id, [])} for t in day_sessions],
                "weigh_in": row_dict(weighed[-1]) if weighed else None,
                "measurements": [row_dict(m) for m in measured if m.date == day],
                "food": {"meals": meals, **food.day_numbers(user, food_rows, cardio_rows)},
                "cardio": [cardio.cardio_dict(c) for c in cardio_rows],
                "summary": log_service._day_summary(day_sessions, weighed[-1] if weighed else None, cardio_rows,
                                                    today, units),
            })
        totals = {"sessions": len(sessions), "weigh_ins": len(weigh_ins), "measurements": len(measured),
                  "food_entries": len(eaten), "cardio_entries": len(moved)}
        p = log_service._plural
        summary = (f"{p(len(out_days), 'day')}, {p(totals['sessions'], 'session')}, "
                   f"{p(totals['weigh_ins'], 'weigh-in')}, {p(totals['measurements'], 'measurement')}, "
                   f"{p(totals['food_entries'], 'food entry', 'food entries')}, "
                   f"{p(totals['cardio_entries'], 'cardio session')}.")
        return {"from": start.isoformat(), "to": end.isoformat(), "days": out_days, "totals": totals,
                "summary": summary,
                "links": {"day": links.page("day", date=end), "progress": links.page("progress")}}


# ---------------------------------------------------------------- generated histories

LIFTS = ("Back Squat", "Bench Press", "Deadlift", "Pull-Up", "Dip")
WEIGHTS = (0.0, 40.0, 60.0, 60.0, 62.5, 80.0, 100.0)  # 0 kg sets and equal bests
REPS = (1, 3, 5, 5, 8, 10, 12, 15)  # over 10 reps too


def generated(user_id: int, today: date, days: int, seed: int, extras: bool = False, after: int = 2) -> int:
    """A history written through the models, oldest first: rest days, one or two sessions a day, 1 to 3 sets of 1 or 2
    lifts, 0 kg sets, sets over 10 reps, equal bests, Dip never above 0 kg, and `after` days dated after today. With
    extras, a weigh-in, a food entry and every few days a waist and a cardio entry each day. Returns the sets written."""
    rng = random.Random(seed)
    written = 0
    with db.session_scope() as s:
        ids = {e.name: e.id for e in exercises(s, "lift")}
        run = next(e for e in exercises(s, "cardio") if e.name == "Outdoor Run")
        rows = []
        for n in range(days - 1, -after - 1, -1):
            d = today - timedelta(days=n)
            for k in range(rng.choice((0, 0, 1, 1, 2))):
                session = LiftSession(user_id=user_id, date=d, notes="", logged_by="web", dedupe_key=f"g{n}k{k}")
                numbers: dict[str, int] = {}
                for lift in rng.sample(LIFTS, rng.choice((1, 2))):
                    for _ in range(rng.choice((1, 2, 3))):
                        numbers[lift] = numbers.get(lift, 0) + 1
                        weight = 0.0 if lift == "Dip" else rng.choice(WEIGHTS)
                        session.sets.append(LiftSet(exercise_id=ids[lift], set_number=numbers[lift], weight_kg=weight,
                                                    reps=rng.choice(REPS)))
                        written += 1
                rows.append(session)
            if extras:
                kg = round(80 + rng.uniform(-2, 2), 1)
                rows.append(Bodyweight(user_id=user_id, date=d, kg=kg, logged_by="web", dedupe_key=f"b{n}"))
                rows.append(FoodEntry(user_id=user_id, date=d, meal=rng.choice(MEALS), description="Dal",
                                      kcal=float(rng.choice((180, 250, 400))), source="you_said", estimated=False,
                                      logged_by="web", dedupe_key=f"f{n}"))
                if n % 3 == 0:
                    rows.append(Measurement(user_id=user_id, date=d, kind="waist", value=90.0 - n / 100, unit="cm",
                                            logged_by="web", dedupe_key=f"m{n}"))
                if n % 4 == 0:
                    rows.append(CardioEntry(user_id=user_id, date=d, exercise_id=run.id, minutes=30.0, distance_km=5.0,
                                            speed_kmh=10.0, notes="", logged_by="web", dedupe_key=f"c{n}",
                                            estimated_kcal=300, estimate_weight_kg=80.0))
        s.add_all(rows)
    return written


def with_bests(history: dict) -> dict:
    """history's fields as values: the sessions by id, so two reads compare with ==."""
    return {**history, "sessions": [t.id for t in history["sessions"]]}


def dates_of(ids: list[int]) -> list[date]:
    with db.session_scope() as s:
        return [s.get(LiftSession, i).date for i in ids]


def histories(user_id: int, today: date) -> tuple[dict, dict]:
    """lifts.history and the oracle's, as values. The oracle's last_top is left out here: lifts.history no longer
    has it (logic-audit.md R1), and old_weights still reads it."""
    with db.new_session() as s:
        old = {k: v for k, v in old_history(s, user_id, today).items() if k != "last_top"}
        return with_bests(lifts.history(s, user_id, today)), with_bests(old)


# ---------------------------------------------------------------- D1: the delete switch under the lock

@pytest.fixture
def deleter(http, fixed):
    """(client, token, account, a weigh-in's id) of a person whose assistant may delete."""
    token = sign()
    account = ok(http, token, "whoami")["account_id"]
    assert "error" not in services.update_settings(account, {"time_zone": "UTC"}, "web")
    entry = services.log_bodyweight(account, {"kg": 80}, "claude")["id"]
    return http, token, account, entry


def live(entry: int) -> bool:
    with db.session_scope() as s:
        return s.get(Bodyweight, entry).deleted_at is None


def last_row() -> CallLog:
    with db.session_scope() as s:
        return s.scalars(select(CallLog).order_by(CallLog.id.desc())).first()


def turned_to_log_first(monkeypatch, account: int, drop_check: bool = False):
    """services.delete_entry, wrapped: the switch is turned to log and committed right before the real one runs, after
    the wrapper's own check. drop_check: the call runs without the wrapper's may_delete, as a caller that forgot it."""
    real = services.delete_entry

    def turned(*args, **kwargs):
        assert "error" not in services.set_claude_can(account, "log")
        if drop_check:
            kwargs.pop("may_delete", None)
        return real(*args, **kwargs)

    monkeypatch.setattr(services, "delete_entry", turned)


@pytest.mark.parametrize("confirmed", [True, False], ids=["delete", "preview"])
def test_a_switch_turned_to_log_while_a_delete_waits_refuses_it_in_the_wrappers_words(deleter, monkeypatch, confirmed):
    http, token, account, entry = deleter
    code = ok(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": entry})["confirm"]
    turned_to_log_first(monkeypatch, account)
    sent = {"kind": "bodyweight", "entry_id": entry, **({"confirm": code} if confirmed else {})}
    result = call(http, token, "delete_entry", sent)
    assert result["isError"] is True
    assert result["structuredContent"] == mcp_server.not_allowed("delete_entry", "log")  # word for word, links too
    assert result["structuredContent"]["links"] == {"settings": links.page("settings")}
    assert live(entry)
    row = last_row()
    assert (row.function, row.error_code, row.outcome, row.step) == ("delete_entry", "not_allowed", "refused",
                                                                    "permission")


def test_the_web_deletes_on_a_log_account_and_log_delete_deletes_as_before(deleter):
    http, token, account, entry = deleter
    code = ok(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": entry})["confirm"]
    assert ok(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": entry, "confirm": code})["deleted"]
    assert not live(entry)
    other = services.log_bodyweight(account, {"kg": 81, "date": "2026-11-09"}, "claude")["id"]
    assert "error" not in services.set_claude_can(account, "log")
    out = services.delete_entry(account, "bodyweight", other)  # the web's call: no by, the person's own click
    assert out["deleted"] is True and not live(other)


def test_with_no_check_passed_a_log_account_raises_and_nothing_is_deleted(deleter, monkeypatch):
    http, token, account, entry = deleter
    with db.session_scope() as s:
        code = entries.confirm_code(account, "bodyweight", s.get(Bodyweight, entry))
    assert "error" not in services.set_claude_can(account, "log")
    with pytest.raises(PermissionError):
        services.delete_entry(account, "bodyweight", entry, "claude", confirm=code)
    with pytest.raises(PermissionError):
        services.delete_entry(account, "bodyweight", entry, "claude")
    assert live(entry)
    # Through /mcp, a caller that forgot the check would be internal_error, with nothing deleted.
    assert "error" not in services.set_claude_can(account, "log_delete")
    turned_to_log_first(monkeypatch, account, drop_check=True)
    result = call(http, token, "delete_entry", {"kind": "bodyweight", "entry_id": entry, "confirm": code})
    assert result["isError"] is True and result["structuredContent"]["error"] == "internal_error" and live(entry)


def test_with_no_check_passed_a_log_delete_account_deletes(deleter):
    _, _, account, entry = deleter
    with db.session_scope() as s:
        code = entries.confirm_code(account, "bodyweight", s.get(Bodyweight, entry))
    assert services.delete_entry(account, "bodyweight", entry, "claude", confirm=code)["deleted"] is True
    assert not live(entry)


# ---------------------------------------------------------------- D2: the export gate, and one moment read

EXPORT = {"files": {"account.csv": "account_id\n1\n", "README.txt": "What each file holds.\n"},
          "rows": {"account.csv": 1}, "summary": ""}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def gate_clock(monkeypatch) -> Clock:
    held = Clock()
    monkeypatch.setattr(web, "export_clock", held)
    return held


def built(monkeypatch, then=None) -> list:
    """services.export_data stood in for: each call is recorded, then `then(user_id)` runs (a seam that holds the
    export while another is asked for), then the canned files."""
    seen = []

    def fake(user_id):
        seen.append(user_id)
        if then is not None:
            then(user_id)
        return EXPORT

    monkeypatch.setattr(services, "export_data", fake)
    return seen


def refused(r, title: str, line: str, fix: str, wait_seconds: int) -> bool:
    return (r.status_code == 429 and f'<h1 class="title">{title}</h1>' in r.text and f"<p>{line}</p>" in r.text
            and f'<p class="muted">{fix}</p>' in r.text and r.headers["retry-after"] == str(wait_seconds))


def test_the_sixth_export_in_an_hour_is_refused_and_nothing_is_built(client, monkeypatch, gate_clock):
    sign_in(client)
    seen = built(monkeypatch)
    for minute in (0, 10, 20, 30, 40):
        gate_clock.now = 1000.0 + minute * 60
        r = post(client, "/settings/export")
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert len(seen) == 5
    gate_clock.now = 1000.0 + 50 * 60
    r = post(client, "/settings/export")
    assert refused(r, "Too many exports", "This account has been exported 5 times in the last hour.",
                   "Try again in 10 minutes.", 600)
    gate_clock.now = 1000.0 + 59.5 * 60
    r = post(client, "/settings/export")
    assert refused(r, "Too many exports", "This account has been exported 5 times in the last hour.",
                   "Try again in 1 minute.", 60)
    assert len(seen) == 5  # a refusal builds nothing and is not counted
    gate_clock.now = 1000.0 + 60 * 60  # the first has left the hour
    assert post(client, "/settings/export").status_code == 200 and len(seen) == 6


def test_a_second_export_while_one_runs_is_refused(client, monkeypatch, gate_clock):
    sign_in(client)
    answers = []

    def again(user_id):
        if len(seen) == 1:  # the first export, held: the person asks for another
            answers.append(post(client, "/settings/export"))

    seen = built(monkeypatch, again)
    assert post(client, "/settings/export").status_code == 200
    [r] = answers
    assert refused(r, "Export under way", "An export of this account is being put together now.",
                   "Wait for it to download, then try again if you need another.", 60)
    assert len(seen) == 1
    assert post(client, "/settings/export").status_code == 200 and len(seen) == 2  # freed once the first was sent


def test_a_third_export_at_once_in_the_process_is_refused(web_client, seam, monkeypatch, gate_clock):
    from app import web_auth  # noqa: PLC0415

    first = web_client()
    sign_in(first)
    others = []
    for n in (2, 3):
        other = TestClient(first.app, base_url=str(first.base_url), follow_redirects=False)  # the same app: one gate
        seam.identity = web_auth.Identity(f"user_0{n}WEB", f"p{n}@example.com", f"P{n}", f"session_0{n}")
        sign_in(other)
        others.append(other)
    second, third = others
    answers = {}

    def hold(user_id):
        if len(seen) == 1:  # the first person's export, held: a second person's starts
            answers["second"] = post(second, "/settings/export")
        elif len(seen) == 2:  # both held: a third person's is one too many
            answers["third"] = post(third, "/settings/export")

    seen = built(monkeypatch, hold)
    assert post(first, "/settings/export").status_code == 200
    assert answers["second"].status_code == 200
    assert refused(answers["third"], "Exports are busy", "Spotter is putting other exports together right now.",
                   "Try again in a minute.", 60)
    assert len(seen) == 2
    assert post(third, "/settings/export").status_code == 200 and len(seen) == 3  # both freed


def test_the_export_otherwise_is_as_today(client, gate_clock):
    sign_in(client)
    services.log_bodyweight(only_user().id, {"kg": 80}, "web")
    r = post(client, "/settings/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"


class FakeBind:
    def __init__(self, name: str) -> None:
        self.dialect = type("Dialect", (), {"name": name})()


class FakeSession:
    def __init__(self, name: str) -> None:
        self.name, self.asked = name, []

    def get_bind(self):
        return FakeBind(self.name)

    def connection(self, **kwargs):
        self.asked.append(kwargs)


def test_on_postgres_the_export_reads_in_one_repeatable_read_read_only_transaction():
    pg, lite = FakeSession("postgresql"), FakeSession("sqlite")
    export.one_moment(pg)
    export.one_moment(lite)
    assert pg.asked == [{"execution_options": {"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}}]
    assert lite.asked == []


def test_export_data_sets_the_moment_before_its_first_read(make_user, monkeypatch):
    uid = make_user().id
    order = []
    real = export.one_moment
    monkeypatch.setattr(export, "one_moment", lambda s: order.append("moment") or real(s))

    def first_read(conn, cursor, statement, parameters, context, executemany):
        order.append("read")

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", first_read)
    try:
        assert "files" in services.export_data(uid)
    finally:
        event.remove(engine, "before_cursor_execute", first_read)
    assert order[0] == "moment" and order[1] == "read"


# ---------------------------------------------------------------- D3: get_log bounds the end in SQL

def loaded_rows(fn) -> tuple[object, dict[str, list]]:
    """fn's result, and the rows of each entry model it loaded."""
    seen = {m.__tablename__: [] for m in (LiftSession, Bodyweight, Measurement, FoodEntry, CardioEntry)}
    listeners = []
    for model in (LiftSession, Bodyweight, Measurement, FoodEntry, CardioEntry):
        def keep(target, context, name=model.__tablename__):
            seen[name].append(target.date)
        event.listen(model, "load", keep)
        listeners.append((model, keep))
    try:
        return fn(), seen
    finally:
        for model, keep in listeners:
            event.remove(model, "load", keep)


def test_one_day_600_days_back_loads_only_that_days_rows(make_user, fixed):
    uid = make_user().id
    generated(uid, TODAY, 730, seed=11, extras=True)
    day = TODAY - timedelta(days=600)
    result, seen = loaded_rows(lambda: services.get_log(uid, day.isoformat()))
    assert [d["date"] for d in result["days"]] == [day.isoformat()]
    for name in ("bodyweight", "measurement", "food_entry", "cardio_entry"):
        assert all(d == day for d in seen[name]), name
    assert len(seen["food_entry"]) == 1 and len(seen["bodyweight"]) == 1
    assert seen["lift_session"] and max(seen["lift_session"]) <= day  # the history up to that day, never after
    _, before = loaded_rows(lambda: old_get_log(uid, day.isoformat()))
    assert len(before["food_entry"]) == 603  # today's read loaded every row from that day on, two after today too


def window_reads(uid: int, last: date, first: date, step: int = 14):
    """(dated read, old) for every window of `step` days ending from `last` back to `first`."""
    end = last
    while end >= first:
        yield services.get_log(uid, end.isoformat(), step, True), old_get_log(uid, end.isoformat(), step, True)
        end -= timedelta(days=step)


def test_every_field_of_get_log_is_todays_on_the_16_week_seed(make_user, fixed):
    uid = make_user().id
    assert "error" not in sixteen_weeks(uid)
    assert services.get_log(uid) == old_get_log(uid)
    assert services.get_log(uid, None, 14) == old_get_log(uid, None, 14)
    for new, old in window_reads(uid, TODAY, TODAY - timedelta(days=364)):
        assert new == old
    for n in range(0, 112):  # every day of the sixteen weeks, one day a read
        on = (TODAY - timedelta(days=n)).isoformat()
        assert services.get_log(uid, on) == old_get_log(uid, on), on


def test_every_field_of_get_log_is_todays_on_a_two_year_history_with_later_days(make_user, fixed):
    uid = make_user().id
    generated(uid, TODAY, 730, seed=5, extras=True, after=2)
    undated = services.get_log(uid)
    assert undated == old_get_log(uid)
    later = [d["date"] for d in undated["days"] if d["date"] > TODAY.isoformat()]
    assert later and all(d["sessions"] or d["weigh_in"] for d in undated["days"] if d["date"] in later)
    assert services.get_log(uid, None, 14) == old_get_log(uid, None, 14)
    for new, old in window_reads(uid, TODAY, TODAY - timedelta(days=729)):
        assert new == old
    for n in range(0, 730, 15):
        on = (TODAY - timedelta(days=n)).isoformat()
        assert services.get_log(uid, on) == old_get_log(uid, on), on


# ---------------------------------------------------------------- D4 to D7: the oracles

TODAYS = (0, -40, 3)  # several todays: the seed's, one before its last 40 days (so many sessions come after), one on


@pytest.mark.parametrize("seed", ["sixteen", "four"])
def test_history_equals_the_oracle_on_the_seeds_at_several_todays(make_user, fixed, seed):
    uid = make_user().id
    assert "error" not in (sixteen_weeks if seed == "sixteen" else four_weeks)(uid)
    for shift in TODAYS:
        new, old = histories(uid, TODAY + timedelta(days=shift))
        assert new == old, shift


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_history_equals_the_oracle_on_generated_histories_at_several_todays(make_user, seed):
    uid = make_user().id
    generated(uid, TODAY, 200, seed=seed, after=3)
    for shift in (*TODAYS, -1, -2, -3):
        new, old = histories(uid, TODAY + timedelta(days=shift))
        assert new == old, shift
        assert new["bests"] and "Dip" in new["reps_only"] and "Dip" not in new["best_set"]


def test_history_with_a_last_day_gives_the_bests_of_the_sessions_up_to_it(make_user):
    uid = make_user().id
    generated(uid, TODAY, 120, seed=9)
    whole, _ = histories(uid, TODAY)
    last = TODAY - timedelta(days=50)
    with db.new_session() as s:
        cut = with_bests(lifts.history(s, uid, TODAY, last_day=last))
        assert cut["sessions"] == [i for i, d in zip(whole["sessions"], dates_of(whole["sessions"])) if d <= last]
        kept = set(cut["sessions"])

        def by_session(h):
            return sorted((b["session_id"], b["kind"], b["value"], b["previous"]) for v in h["bests"].values()
                          for b in v if b["session_id"] in kept)

        assert by_session(cut) == by_session(whole) and by_session(cut)  # a session's bests depend only on earlier ones
        assert with_bests(lifts.history(s, uid, TODAY, last_day=TODAY)) == whole  # today or later: every session


def test_prs_equals_the_oracle():
    rng = random.Random(4)
    for _ in range(3000):
        prior = [(rng.choice(WEIGHTS), rng.choice(REPS)) for _ in range(rng.randrange(0, 6))]
        now = [(rng.choice(WEIGHTS), rng.choice(REPS)) for _ in range(rng.randrange(1, 5))]
        assert metrics.prs(prior, now) == old_prs(prior, now)


def points_for(kind: str, rng: random.Random, today: date) -> list[tuple[date, float]]:
    """A series with gaps, dense days (several points on one date) and points after today."""
    out = []
    day = today - timedelta(days=rng.randrange(60, 400))
    while day <= today + timedelta(days=3):
        for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
            if kind == "bodyweight":
                value = round(80 + rng.uniform(-3, 3), 2)
            elif kind == "cardio_best":
                value = round(rng.uniform(20, 35), 1)
            elif kind == "lift":
                value = round(rng.uniform(60, 140), 2)
            else:
                value = round(rng.uniform(80, 95), 1)
            out.append((day, value))
        day += timedelta(days=rng.choice((1, 1, 2, 5, 13, 30)))
    rng.shuffle(out)  # the caller's order: goal_now_series sorts by effective date, stably
    return out


@pytest.mark.parametrize("kind", ["lift", "bodyweight", "measurement", "cardio_best"])
def test_goal_now_series_equals_the_oracle(kind):
    rng = random.Random(kind)
    for trial in range(60):
        pts = points_for(kind, rng, TODAY)
        for shift in (0, -20, 2):
            today = TODAY + timedelta(days=shift)
            since = today - timedelta(days=rng.choice((0, 30, 120, 500)))
            assert metrics.goal_now_series(kind, pts, today, since) == old_goal_now_series(kind, pts, today, since), \
                (kind, trial, shift)
    assert metrics.goal_now_series(kind, [], TODAY, TODAY) == [] == old_goal_now_series(kind, [], TODAY, TODAY)


def test_goal_now_series_equals_the_oracle_on_the_seeds(make_user, fixed):
    uid = make_user().id
    assert "error" not in sixteen_weeks(uid)
    with db.new_session() as s:
        user = s.get(User, uid)
        goals = list(s.scalars(select(Goal).where(Goal.user_id == uid).options(selectinload(Goal.exercise))))
        from app.services import goals as goal_service  # noqa: PLC0415
        loaded = goal_service.load_numbers(s, uid, TODAY, goals)
        for g in goals:
            pts = goal_service._points(g, loaded, TODAY)
            assert pts and metrics.goal_now_series(g.kind, pts, TODAY, g.start_date) == \
                old_goal_now_series(g.kind, pts, TODAY, g.start_date)
        assert user is not None


NAMES = {"Back Squat", "Bench Press", "Deadlift", "Pull-Up", "Dip", "Barbell Row", "Overhead Press"}


def test_get_routines_weights_follow_the_one_order_when_a_later_dated_session_came_first(make_user, fixed):
    """A session dated after today (a change of time zone), logged before today's: in the one order both stand at
    today, so the one logged later, today's, is the latest, though its real date is earlier."""
    uid = make_user().id
    with db.session_scope() as s:
        squat = next(e.id for e in exercises(s, "lift") if e.name == "Back Squat")
        for on, kg in ((TODAY + timedelta(days=2), 120.0), (TODAY, 90.0), (TODAY - timedelta(days=3), 100.0)):
            session = LiftSession(user_id=uid, date=on, notes="", logged_by="web", dedupe_key=f"o{kg}")
            session.sets.append(LiftSet(exercise_id=squat, set_number=1, weight_kg=kg, reps=5))
            s.add(session)
            s.flush()

    def cut(s, today, names):  # latest_sessions cut to each lift's date and weight, as old_weights gives them
        return {name: {"date": x["date"], "weight_kg": x["weight_kg"]}
                for name, x in routines.latest_sessions(s, uid, today, names).items()}

    with db.new_session() as s:
        tops = cut(s, TODAY, {"Back Squat"})
        assert tops == old_weights(s, uid, TODAY, {"Back Squat"}) == {"Back Squat": {"date": TODAY, "weight_kg": 90.0}}
        later = cut(s, TODAY + timedelta(days=2), {"Back Squat"})  # a later today: real dates
        assert later == old_weights(s, uid, TODAY + timedelta(days=2), {"Back Squat"})
        assert later["Back Squat"]["weight_kg"] == 120.0


def test_get_routines_weights_equal_the_oracle(make_user, fixed):
    def cut(s, uid, today, names):  # latest_sessions cut to each lift's date and weight, as old_weights gives them
        return {name: {"date": x["date"], "weight_kg": x["weight_kg"]}
                for name, x in routines.latest_sessions(s, uid, today, names).items()}

    for seed, maker in (("sixteen", sixteen_weeks), ("four", four_weeks), (6, None), (8, None)):
        uid = make_user().id
        if maker:
            assert "error" not in maker(uid)
        else:
            generated(uid, TODAY, 150, seed=seed, after=3)
        for shift in (*TODAYS, -1, -2):
            today = TODAY + timedelta(days=shift)
            with db.new_session() as s:
                assert cut(s, uid, today, NAMES) == old_weights(s, uid, today, NAMES), (seed, shift)
                some = {"Back Squat", "Dip", "Deadlift"}
                assert cut(s, uid, today, some) == old_weights(s, uid, today, some)


# ---------------------------------------------------------------- bounded work, by count

def test_history_calls_epley_at_most_twice_a_set(make_user, monkeypatch):
    uid = make_user().id
    sets = generated(uid, TODAY, 300, seed=3)
    calls = []
    real = metrics.epley
    monkeypatch.setattr(metrics, "epley", lambda w, r: calls.append(1) or real(w, r))
    with db.new_session() as s:
        lifts.history(s, uid, TODAY)
        assert 0 < len(calls) <= 2 * sets
        calls.clear()
        old_history(s, uid, TODAY)
        assert len(calls) > 10 * sets  # today's: once per earlier set, for each session


def test_goal_now_series_sorts_a_fixed_number_of_times(monkeypatch):
    sorts = []
    monkeypatch.setattr(metrics, "sorted", lambda *a, **k: sorts.append(1) or sorted(*a, **k), raising=False)
    counts = []
    for kind in ("bodyweight", "lift"):
        for days in (10, 400):
            pts = [(TODAY - timedelta(days=n), 80.0 + n / 100) for n in range(days)]
            sorts.clear()
            assert len(metrics.goal_now_series(kind, pts, TODAY, TODAY - timedelta(days=days))) == days
            counts.append(len(sorts))
    assert counts[0] == counts[1] == counts[2] == counts[3] <= 2
    sorts.clear()
    old_goal_now_series("lift", pts, TODAY, TODAY - timedelta(days=400))
    assert len(sorts) > 400  # today's: once a date, in recent_best


def test_get_routines_sends_one_statement_for_its_weights_and_never_calls_history(make_user, fixed, monkeypatch):
    uid = make_user().id
    generated(uid, TODAY, 60, seed=2)
    assert "error" not in services.save_routine(uid, {"name": "Legs", "lifts": [
        {"exercise": "Back Squat", "sets": 3, "reps": 5}, {"exercise": "Dip", "sets": 3, "reps": 10}]}, "web")

    def never(*args, **kwargs):
        raise AssertionError("get_routines walked the history")

    monkeypatch.setattr(lifts, "history", never)
    out, seen = statements_of(lambda: services.get_routines(uid))
    assert "error" not in out and len(seen) == 4  # the user, the routines, the program, the weights and last
    assert [sql for sql, _ in seen if "lift_set" in sql and "row_number" in sql.lower()] == \
           [sql for sql, _ in seen if "lift_set" in sql]


@pytest.fixture
def counted_history(monkeypatch) -> list:
    """lifts.history, its calls counted."""
    calls = []
    real = lifts.history

    def history(*args, **kwargs):
        calls.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr(lifts, "history", history)
    return calls


@pytest.mark.parametrize("path", ["/day/2026-11-10", "/progress", "/progress/back-squat", "/goals"])
def test_each_page_works_the_lift_history_out_once(client, fixed, counted_history, path):
    sign_in(client)
    assert "error" not in four_weeks(only_user().id)  # Back Squat's goal is open
    counted_history.clear()
    r = client.get(path)
    assert r.status_code == 200 and len(counted_history) == 1, (path, counted_history)


def test_goal_charts_gives_the_closed_goals_get_goals_gives_and_progress_its_bests(make_user, fixed):
    uid = make_user().id
    assert "error" not in four_weeks(uid)
    with db.session_scope() as s:
        first = s.scalars(select(Goal.id).where(Goal.user_id == uid).order_by(Goal.id)).first()
    assert "error" not in services.set_goal(uid, {"goal_id": first, "status": "hit"}, "web")
    charted, listed = services.goal_charts(uid), services.get_goals(uid, True)
    assert charted["closed"] == listed["closed"] and len(charted["closed"]) == 1
    assert [g["goal_id"] for g in charted["goals"]] == [g["goal_id"] for g in listed["goals"]]
    with db.new_session() as s:
        history = lifts.history(s, uid, TODAY)
    for exercise in (None, "Pull-Up"):
        page = services.get_progress(uid, exercise, for_page=True)
        plain = services.get_progress(uid, exercise)
        assert page == {**plain, "best_sets": history["best_set"], "reps_only": history["reps_only"]}
        assert "best_sets" not in plain and "reps_only" not in plain  # no MCP result changes
    assert history["reps_only"] == {"Pull-Up": {"reps": 6, "date": "2026-10-16"}}


def test_today_goes_to_the_day_page_which_reads_get_log_once_undated(client, fixed, monkeypatch):
    sign_in(client)
    real, seen = services.get_log, []
    monkeypatch.setattr(services, "get_log", lambda *a, **k: seen.append(a[1:]) or real(*a, **k))
    r = client.get("/today")
    assert r.status_code == 303
    assert client.get(r.headers["location"]).status_code == 200 and seen == [(None, 1)]
    seen.clear()
    assert client.get("/day/2026-11-09").status_code == 200 and seen == [("2026-11-09", 1, True)]


def test_today_s_page_still_draws_when_the_persons_midnight_falls_inside_its_read(client, monkeypatch):
    """The page's date is the 10th; by the time get_log reads, it is the 11th for the person. The undated read has no
    block for the 10th, so the page reads that day once more, dated, and draws it, as fd83753 did (review N2)."""
    clock = {"now": datetime(2026, 11, 10, 23, 59, 59, tzinfo=timezone.utc)}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    sign_in(client)
    uid = only_user().id
    assert "error" not in services.update_settings(uid, {"time_zone": "UTC"}, "web")
    assert "error" not in services.log_bodyweight(uid, {"kg": 81.3}, "web")
    real, seen = services.get_log, []

    def past_midnight(*args, **kwargs):
        seen.append(args[1:])
        clock["now"] = datetime(2026, 11, 11, 0, 0, 1, tzinfo=timezone.utc)
        return real(*args, **kwargs)

    monkeypatch.setattr(services, "get_log", past_midnight)
    r = client.get("/day/2026-11-10")
    assert r.status_code == 200 and "81.3" in r.text
    assert seen == [(None, 1), ("2026-11-10", 1, True)]  # the second read only in this edge
    seen.clear()
    clock["now"] = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(services, "get_log", lambda *a, **k: seen.append(a[1:]) or real(*a, **k))
    assert client.get("/day/2026-11-11").status_code == 200 and seen == [(None, 1)]  # otherwise one read


# ---------------------------------------------------------------- D8: retention cleanup off the call

def old_calls(count: int, days: int = 91) -> None:
    started = NOW - timedelta(days=days)
    with db.session_scope() as s:
        s.execute(insert(CallLog), [{"function": "whoami", "started_at": started, "ms": 1, "ok": True,
                                     "result_bytes": 1} for _ in range(count)])


def call_rows() -> int:
    with db.session_scope() as s:
        return len(s.scalars(select(CallLog.id)).all())


def deletes_of(fn, table: str) -> list[int]:
    """The rows each DELETE of this table took, in order."""
    counts = []

    def after(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"DELETE FROM {table} "):
            counts.append(cursor.rowcount)

    engine = db.get_engine()
    event.listen(engine, "after_cursor_execute", after)
    try:
        fn()
    finally:
        event.remove(engine, "after_cursor_execute", after)
    return counts


def test_a_call_returns_while_its_sweep_still_runs_and_a_second_does_not_start_beside_it(http, fixed, monkeypatch):
    monkeypatch.setattr(calllog, "start_sweep", START_SWEEP)
    monkeypatch.setattr(calllog, "_swept", None)
    monkeypatch.setattr(calllog, "_swept_keys", None)
    old_calls(3)
    entered, release, threads = threading.Event(), threading.Event(), []
    real_delete, real_beside = calllog.delete, calllog._sweep_beside

    def held(*args):
        entered.set()
        release.wait(10)
        return real_delete(*args)

    def beside():
        threads.append(threading.current_thread())
        real_beside()

    monkeypatch.setattr(calllog, "delete", held)
    monkeypatch.setattr(calllog, "_sweep_beside", beside)
    token = sign()
    try:
        ok(http, token, "whoami")  # it answers while its sweep is held
        assert entered.wait(10) and len(threads) == 1 and threads[0].is_alive()
        ok(http, token, "whoami")  # due still, but one runs: none starts beside it
        assert len(threads) == 1
        assert call_rows() == 5  # the three old ones are still there: the sweep is held
    finally:
        release.set()
        for thread in threads:
            thread.join(10)
    assert not threads[0].is_alive() and call_rows() == 2 and calllog._running is False
    assert calllog._swept == NOW


def test_2500_old_rows_go_in_3_statements_of_at_most_1000(make_user, fixed, monkeypatch):
    old_calls(2500)
    old_calls(4, days=89)
    monkeypatch.setattr(calllog, "_swept", None)
    monkeypatch.setattr(calllog, "_swept_keys", NOW)
    assert deletes_of(calllog.sweep, "call_log") == [1000, 1000, 500]
    assert call_rows() == 4 and calllog._swept == NOW and calllog._retry is None
    assert (calllog.SWEEP_BATCH, calllog.SWEEP_BATCHES, calllog.SWEEP_RETRY_MINUTES) == (1000, 50, 10)


def test_at_the_cap_the_rest_stays_last_swept_stays_and_it_is_due_again_in_10_minutes(fixed, monkeypatch):
    old_calls(25)
    before = NOW - timedelta(hours=25)
    monkeypatch.setattr(calllog, "_swept", before)
    monkeypatch.setattr(calllog, "_swept_keys", NOW)
    monkeypatch.setattr(calllog, "SWEEP_BATCH", 10)
    monkeypatch.setattr(calllog, "SWEEP_BATCHES", 2)
    assert deletes_of(calllog.sweep, "call_log") == [10, 10]
    assert call_rows() == 5 and calllog._swept == before and calllog._retry == NOW + timedelta(minutes=10)
    clock = {"now": NOW + timedelta(minutes=9)}
    monkeypatch.setattr(db, "utcnow", lambda: clock["now"])
    assert deletes_of(calllog.sweep, "call_log") == []  # its retry time lies ahead
    clock["now"] = NOW + timedelta(minutes=10)
    assert deletes_of(calllog.sweep, "call_log") == [5]
    assert call_rows() == 0 and calllog._swept == clock["now"] and calllog._retry is None


def test_a_failing_delete_keeps_last_swept_sets_the_retry_and_logs_its_one_line(fixed, monkeypatch, logs):
    old_calls(2500)
    before = NOW - timedelta(hours=30)
    monkeypatch.setattr(calllog, "_swept", before)
    monkeypatch.setattr(calllog, "_swept_keys", NOW)

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM call_log "):
            raise OperationalError("DELETE", {}, Exception("held"))

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", fail)
    try:
        calllog.sweep()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert calllog._swept == before and calllog._retry == NOW + timedelta(minutes=10)
    assert [r.getMessage() for r in logs.records if "swept" in r.getMessage()] == [
        "The call log could not be swept: OperationalError"]  # its batches stopped at the first failure
    assert call_rows() == 2500


def test_the_modules_own_delete_reaches_every_batch(fixed, monkeypatch):
    old_calls(2500)
    monkeypatch.setattr(calllog, "_swept", None)
    monkeypatch.setattr(calllog, "_swept_keys", NOW)
    tables = []
    real = calllog.delete
    monkeypatch.setattr(calllog, "delete", lambda model: tables.append(model.__tablename__) or real(model))
    calllog.sweep()
    assert tables == ["call_log"] * 3 + ["entry_version", "feedback"] and call_rows() == 0


def test_a_retry_time_left_by_one_test_never_holds_back_the_next():
    """A failing sweep two tests up left a retry time ten minutes on: tests/conftest.py cleared it before this one."""
    assert calllog._retry is None and calllog._retry_keys is None
    assert calllog.start_sweep == calllog.sweep  # and every test sweeps inline


def test_the_older_tables_still_go_in_batches(make_user, fixed, monkeypatch):
    uid = make_user().id
    with db.session_scope() as s:
        s.add_all([EntryVersion(user_id=uid, kind="bodyweight", entry_id=1, by="web", why="edit", before="{}",
                                created_at=NOW - timedelta(days=91)) for _ in range(3)])
        s.add_all([Feedback(user_id=uid, text=f"old note {n}", topic="other", dedupe_key=f"{n:064d}",
                            created_at=NOW - timedelta(days=366)) for n in range(3)])
    monkeypatch.setattr(calllog, "_swept", None)
    monkeypatch.setattr(calllog, "_swept_keys", NOW)
    monkeypatch.setattr(calllog, "SWEEP_BATCH", 2)
    assert deletes_of(calllog.sweep, "entry_version") == [2, 1]
    with db.session_scope() as s:
        assert s.scalars(select(EntryVersion.id)).all() == [] and s.scalars(select(Feedback.id)).all() == []


# ---------------------------------------------------------------- D9: a set list replaced old first

UNIQUE_SETS = "ix_test_unique_set_number"


@pytest.fixture
def unique_set_numbers():
    """A unique index on (session_id, exercise_id, set_number), made here when the schema has none (H2 adds one)."""
    engine = db.get_engine()
    with engine.connect() as c:
        have = any(i["unique"] and i["column_names"] == ["session_id", "exercise_id", "set_number"]
                   for i in inspect(c).get_indexes("lift_set"))
    if not have:
        with engine.begin() as c:
            c.execute(text(f"CREATE UNIQUE INDEX {UNIQUE_SETS} ON lift_set (session_id, exercise_id, set_number)"))
    try:
        yield
    finally:
        if not have:
            with engine.begin() as c:
                c.execute(text(f"DROP INDEX {UNIQUE_SETS}"))


def stored_sets(session_id: int) -> list[tuple]:
    with db.session_scope() as s:
        return [(x.exercise.name, x.set_number, x.weight_kg, x.reps)
                for x in sorted(s.get(LiftSession, session_id).sets, key=lambda x: x.id)]


def test_an_edit_of_a_set_list_its_undo_and_a_second_edit_pass_under_a_unique_set_number(
        make_user, fixed, unique_set_numbers):
    uid = make_user().id
    sid = services.log_lifts(uid, {"sets": [{"exercise": "Back Squat", "weight_kg": 100, "reps": 5}] * 3}, "web")["id"]
    with db.new_session() as s:  # the index holds: a second set 1 of the lift in the session is refused
        s.add(LiftSet(session_id=sid, exercise_id=s.get(LiftSession, sid).sets[0].exercise_id, set_number=1,
                      weight_kg=1.0, reps=1))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()
    same = [{"exercise": "Back Squat", "weight_kg": w, "reps": 5} for w in (102.5, 102.5, 105)]
    out = services.edit_entry(uid, "lift_session", sid, {"sets": same}, "web")
    assert out["changed"] == ["sets"]
    assert stored_sets(sid) == [("Back Squat", 1, 102.5, 5), ("Back Squat", 2, 102.5, 5), ("Back Squat", 3, 105.0, 5)]
    undone = services.restore_entry(uid, "lift_session", sid, "web", undo_edit=True)
    assert undone["undone"] is True
    assert stored_sets(sid) == [("Back Squat", n, 100.0, 5) for n in (1, 2, 3)]
    again = [{"exercise": "Back Squat", "weight_kg": 90, "reps": 8}, {"exercise": "Bench Press", "weight_kg": 60,
                                                                     "reps": 5}]
    assert services.edit_entry(uid, "lift_session", sid, {"sets": again}, "web")["changed"] == ["sets"]
    assert stored_sets(sid) == [("Back Squat", 1, 90.0, 8), ("Bench Press", 1, 60.0, 5)]
