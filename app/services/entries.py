"""Any entry by its id: read it, delete it, restore it.

The kinds are the entry tables themselves (models.entry_models(), named by table), so a table a
later stage adds is readable, deletable and restorable here with no new code. A goal can also be
read by id; it is never deleted, only closed with set_goal (status dropped).

get_entry(user_id, kind, entry_id)       -> {kind, id, entry, links}
delete_entry(user_id, kind, entry_id)    -> {kind, id, deleted: true, entry, note}
restore_entry(user_id, kind, entry_id)   -> {kind, id, restored: true, entry, replaced [entries][, note]}

- Every lookup goes through common.owned(). Someone else's id, a deleted entry (for read and
  delete) and an id that does not exist all give the same not_found.
- Delete is soft: it sets deleted_at. Restore clears it. A restore of an entry that is not deleted
  is refused with not_deleted.
- One live entry per day. A table with a partial unique index on live rows (bodyweight: one per
  person and date) cannot hold two live rows for the same values. Restoring an entry there
  soft-deletes the live one in its place, and the result names it under "replaced", so the swap
  can itself be undone with another restore. The live one leaves the index before the restored
  one returns, because SQLite checks the index statement by statement.
"""

from sqlalchemy import Index, select

from app import db, links
from app.models import Goal, LiftSession, entry_models
from app.rules import error, quote
from app.services.common import lock_user, no_account, not_found, owned, row_dict
from app.services.goals import goal_dict
from app.services.lifts import session_dict


def entry_kinds() -> dict[str, type]:
    """Every entry kind, by table name."""
    return {m.__tablename__: m for m in entry_models()}


def _readable() -> dict[str, type]:
    return {**entry_kinds(), "goal": Goal}


def _model(kinds: dict[str, type], kind):
    """The model for a kind, or None. Only a string names a kind; anything else is no kind."""
    return kinds.get(kind) if isinstance(kind, str) else None


def _unknown(kind, kinds: dict[str, type]) -> dict:
    if kind == "goal":
        return error("unknown_kind", "A goal is not deleted or restored: it is closed or reopened.",
                     "Use set_goal with goal_id and status dropped (or open).")
    return error("unknown_kind", f"'{quote(kind)}' is not a kind of entry.", f"Use one of: {', '.join(kinds)}.")


def entry_dict(row) -> dict:
    if isinstance(row, LiftSession):
        return session_dict(row)
    if isinstance(row, Goal):
        return goal_dict(row)
    return row_dict(row)


def _links(row) -> dict:
    if isinstance(row, Goal):
        return {"goals": links.page("goals")}
    return {"day": links.page("day", date=row.date)}


def _live_unique(model) -> list[list[str]]:
    """The column sets of each partial unique index on live rows, beyond user_id."""
    out = []
    for index in model.__table__.indexes:
        if not isinstance(index, Index) or not index.unique:
            continue
        where = [index.dialect_options[d].get("where") for d in ("sqlite", "postgresql")]
        if any(w is not None and "deleted_at" in str(w) for w in where):
            out.append([c.name for c in index.columns if c.name != "user_id"])
    return out


def get_entry(user_id: int, kind: str, entry_id) -> dict:
    kinds = _readable()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.new_session() as s:
        row = owned(s, model, user_id, entry_id)
        if row is None:
            return not_found(kind)
        return {"kind": kind, "id": row.id, "entry": entry_dict(row), "links": _links(row)}


def delete_entry(user_id: int, kind: str, entry_id) -> dict:
    kinds = entry_kinds()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.session_scope() as s:
        if lock_user(s, user_id) is None:
            return no_account()
        row = owned(s, model, user_id, entry_id)
        if row is None:
            return not_found(kind)
        row.deleted_at = db.utcnow()
        s.flush()
        return {"kind": kind, "id": row.id, "deleted": True, "entry": entry_dict(row),
                "note": "Deleted. restore_entry with the same kind and id brings it back."}


def restore_entry(user_id: int, kind: str, entry_id) -> dict:
    kinds = entry_kinds()
    model = _model(kinds, kind)
    if model is None:
        return _unknown(kind, kinds)
    with db.session_scope() as s:
        if lock_user(s, user_id) is None:
            return no_account()
        row = owned(s, model, user_id, entry_id, deleted=True)
        if row is None:
            return not_found(kind)
        if row.deleted_at is None:
            return error("not_deleted", "This entry is not deleted, so there is nothing to restore.",
                         "Nothing to do. get_entry shows it as it is.")
        replaced = []
        for columns in _live_unique(model):
            replaced += list(s.scalars(select(model).where(
                model.user_id == user_id, model.deleted_at.is_(None), model.id != row.id,
                *[getattr(model, c) == getattr(row, c) for c in columns])))
        now = db.utcnow()
        for other in replaced:
            other.deleted_at = now
        s.flush()  # the live one leaves the index first
        row.deleted_at = None
        s.flush()
        out = {"kind": kind, "id": row.id, "restored": True, "entry": entry_dict(row),
               "replaced": [entry_dict(r) for r in replaced]}
        if replaced:
            out["note"] = ("Restored. It took the place of the live entry for the same day, which is now deleted: "
                           "restore_entry with that id swaps them back.")
        return out
