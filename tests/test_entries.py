"""get_entry, delete_entry, restore_entry: any entry by id, soft delete, and restore."""

import pytest
from sqlalchemy import select

from app import db, services
from app.models import LiftSession, LiftSet, Measurement, entry_models
from app.services.common import MAX_ID, entry_id
from app.services.entries import entry_kinds

MISSING = 10 ** 9


@pytest.fixture
def user(make_user):
    return make_user()


def log_session(user):
    return services.log_lifts(user.id, {"sets": [{"exercise": "Deadlift", "weight_kg": 100, "reps": 5},
                                                 {"exercise": "Deadlift", "weight_kg": 100, "reps": 4}]}, "claude")


def log_weigh_in(user):
    return services.log_bodyweight(user.id, {"kg": 80}, "web")


LOGGERS = {"lift_session": log_session, "bodyweight": log_weigh_in}


@pytest.mark.parametrize("kind", list(LOGGERS))
def test_read_delete_and_restore(user, kind):
    made = LOGGERS[kind](user)
    entry_id = made["id"]

    read = services.get_entry(user.id, kind, entry_id)
    assert read["kind"] == kind and read["id"] == entry_id and read["entry"]["id"] == entry_id
    assert read["entry"]["deleted_at"] is None and "user_id" not in read["entry"]
    assert read["links"]["day"].endswith(f"/day/{read['entry']['date']}")
    assert services.get_entry(user.id, kind, str(entry_id)) == read

    deleted = services.delete_entry(user.id, kind, entry_id)
    assert deleted["deleted"] is True and deleted["entry"]["deleted_at"] and "restore_entry" in deleted["note"]
    missing = services.get_entry(user.id, kind, MISSING)
    assert services.get_entry(user.id, kind, entry_id) == missing
    assert services.delete_entry(user.id, kind, entry_id) == services.delete_entry(user.id, kind, MISSING) == missing

    restored = services.restore_entry(user.id, kind, entry_id)
    assert restored["restored"] is True and restored["entry"]["deleted_at"] is None and restored["replaced"] == []
    assert services.get_entry(user.id, kind, entry_id)["entry"] == restored["entry"]
    again = services.restore_entry(user.id, kind, entry_id)
    assert again["error"] == "not_deleted" and again["fix"]
    assert services.restore_entry(user.id, kind, MISSING) == missing


def test_not_found_is_one_dict_for_every_way_an_id_can_miss(user):
    session = log_session(user)
    services.delete_entry(user.id, "lift_session", session["id"])
    missing = services.get_entry(user.id, "lift_session", MISSING)
    assert missing["error"] == "not_found" and str(MISSING) not in missing["message"] and missing["fix"]
    for bad in (session["id"], "abc", None, True, 0, -3, 1.5, "", [1]):
        assert services.get_entry(user.id, "lift_session", bad) == missing, bad
        assert services.delete_entry(user.id, "lift_session", bad if bad != session["id"] else MISSING) == missing


def test_entry_id_takes_ints_and_short_ascii_digit_strings_in_range():
    assert entry_id(12) == 12 and entry_id("12") == 12 and entry_id(" 12 ") == 12
    assert entry_id(MAX_ID) == MAX_ID and entry_id(str(MAX_ID)) == MAX_ID
    for bad in (True, False, 0, -1, "0", "-3", MAX_ID + 1, 2 ** 63, -(2 ** 63), "9" * 11, "9" * 5000,
                "²", "１", "１２", "3.0", 3.0, 1.5, None, "", [1], {"id": 1}):
        assert entry_id(bad) is None, bad


def test_any_kind_that_is_not_a_name_is_unknown(user):
    made = log_weigh_in(user)
    for kind in (["bodyweight"], {"bodyweight": 1}, None, 5, "x" * 100_000):
        for call in (services.get_entry, services.delete_entry, services.restore_entry):
            r = call(user.id, kind, made["id"])
            assert r["error"] == "unknown_kind" and len(str(r)) < 1_000, (call.__name__, r)
    assert services.get_entry(user.id, "bodyweight", made["id"])["entry"]["deleted_at"] is None


def test_a_lift_session_keeps_its_sets_through_delete_and_restore(user):
    made = log_session(user)
    services.delete_entry(user.id, "lift_session", made["id"])
    with db.new_session() as s:
        assert len(list(s.scalars(select(LiftSet).where(LiftSet.session_id == made["id"])))) == 2
    restored = services.restore_entry(user.id, "lift_session", made["id"])
    assert restored["entry"]["sets"] == made["session"]["sets"]


def test_unknown_kinds_and_goals(user):
    assert services.get_entry(user.id, "food", 1)["error"] == "unknown_kind"
    assert "lift_session" in services.delete_entry(user.id, "food", 1)["fix"]
    for call in (services.delete_entry, services.restore_entry):
        refused = call(user.id, "goal", 1)
        assert refused["error"] == "unknown_kind" and "set_goal" in refused["fix"]
    goal = services.set_goal(user.id, {"kind": "bodyweight", "target_value": 70, "start_value": 80,
                                       "target_date": "2099-01-01"}, "claude")
    read = services.get_entry(user.id, "goal", goal["id"])
    assert read["entry"] == goal["goal"] and read["links"]["goals"].endswith("/goals")


def test_every_entry_table_is_a_kind_with_no_new_code(user):
    assert set(entry_kinds()) == {m.__tablename__ for m in entry_models()}
    # A table no stage 2 function writes yet still reads, deletes and restores by id.
    with db.session_scope() as s:
        row = Measurement(user_id=user.id, date=db.utcnow().date(), kind="waist", value=82.5, unit="cm",
                          logged_by="web", dedupe_key="0" * 64)
        s.add(row)
    assert services.get_entry(user.id, "measurement", row.id)["entry"]["value"] == 82.5
    assert services.delete_entry(user.id, "measurement", row.id)["deleted"] is True
    assert services.restore_entry(user.id, "measurement", row.id)["restored"] is True


def test_delete_marks_the_row_and_leaves_it_in_place(user):
    made = log_session(user)
    services.delete_entry(user.id, "lift_session", made["id"])
    with db.new_session() as s:
        row = s.get(LiftSession, made["id"])
        assert row is not None and row.deleted_at is not None and row.updated_at >= row.created_at
