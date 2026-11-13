"""USDA FoodData Central: a food's energy per 100 g, looked up by name or by id, and cached.

search(query) -> (results [{fdc_id, description, data_type, kcal_per_100g}], None) or (None, error)
food(fdc_id)  -> ({fdc_id, description, data_type, kcal_per_100g}, None) or (None, error)

- Off without USDA_API_KEY (config.usda_api_key): every call is usda_off.
- The search is a POST to /foods/search, its query, data types and page size in a JSON body and the
  address the path alone; the food by id is a GET of /food/{id}. Through the standard library (urllib),
  with a 5-second timeout on each try, on the opener from app.auth that refuses every redirect: following
  one would carry the key to another host. The key goes in the X-Api-Key header, never in the address,
  and never into an error, a log line or a result.
- Tries: a try that fails with bad_request, no_answer or a status from 500 to 599 is tried again at
  once, TRIES (3) in all, and none is begun once TRIES_SECONDS (8) have gone since the first began, by
  clock(). Any other reason ends the lookup at its first try.
- Never call these while holding the user lock: a lookup is a network call, and on a cache miss it
  writes the cache in a session of its own. log_food resolves its USDA ids before it locks, and stops
  at the first that fails.
- Data types: Foundation, SR Legacy and Survey (FNDDS). Energy: nutrient 1008 (Energy, kcal), by its
  id or by its number, 208; a food with no kcal value is left out of a search and is an error by id.
- Two shared caches, which hold no user id: usda_search (the normalised query -> the fdc ids it found)
  and usda_food (one row per food). A cached answer is used while it is younger than CACHE_DAYS;
  an older one is fetched again.
- Every failure is usda_unavailable, with a fix pointing at a saved food or the person's own number. Its
  reason is the last try's, one word of REASONS, from the HTTP status or the kind of failure: it goes to
  the call's row (calllog.note, usda=reason) and picks the message (MESSAGES, else "USDA did not
  answer."). Each failed try is one warning line, its reason and its number. A lookup of more than one
  try, failed or not, notes usda_tries=N beside it: in a call of several lookups, the most any one
  took. Nothing else about a failure is passed on: it could carry the address, the key or the query.
- The cache is best effort. Rows are written with an upsert, so two first searches for one query at
  once both land; if a store fails anyway, the answer is still returned.
"""

import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import SQLAlchemyError

from app import calllog, db
from app.auth import build_opener
from app.config import get_settings
from app.models import UsdaFood, UsdaSearch
from app.rules import error

BASE = "https://api.nal.usda.gov/fdc/v1"
TIMEOUT_SECONDS = 5
CACHE_DAYS = 30
DATA_TYPES = ("Foundation", "SR Legacy", "Survey (FNDDS)")
ENERGY_ID, ENERGY_NUMBER = 1008, "208"
PAGE_SIZE = 25
QUERY_MAX = 128   # usda_search.query_key
DESCRIPTION_MAX = 255
# Why a lookup failed: http_NNN for any status not named here.
REASONS = ("key_refused", "rate_limited", "bad_request", "redirect", "no_answer", "bad_reply", "no_energy")
STATUS_REASONS = {400: "bad_request", 401: "key_refused", 403: "key_refused", 429: "rate_limited"}
MESSAGES = {"key_refused": "USDA refused Spotter's key.", "rate_limited": "USDA is over its limit for now."}
AGAIN = ("bad_request", "no_answer")  # tried again, as is a status from 500 to 599 (http_5NN)
TRIES, TRIES_SECONDS = 3, 8

log = logging.getLogger("spotter.usda")
urlopen = build_opener().open  # refuses redirects (app.auth); tests replace this, nothing else does


def off() -> dict:
    return error("usda_off", "USDA lookup is off: no key is set.",
                 "Log it with the person's own number (kcal), or as a saved food.")


def unavailable(reason: str | None = None) -> dict:
    return error("usda_unavailable", MESSAGES.get(reason, "USDA did not answer."),
                 "Use a saved food (find_food lists them) or the person's own number (kcal).")


def normalise(query: str) -> str:
    return " ".join(query.split()).casefold()[:QUERY_MAX]


def clock() -> float:
    """The tries' clock, in seconds. Tests replace it."""
    return time.monotonic()


def _get(path: str, params: dict, key: str):
    """One GET, its params in the address: _open's answer."""
    # Measured 2026-11-14: a search with its dataType filter in the address got a 400 at random, with spaces as
    # %20 as with "+"; with no dataType, 20 of 20 answered. So the search is a POST (_post); the food by id a GET.
    query = urllib.parse.urlencode(params, doseq=True, quote_via=urllib.parse.quote)
    address = f"{BASE}{path}?{query}" if query else f"{BASE}{path}"
    return _open(urllib.request.Request(address, headers={"X-Api-Key": key, "Accept": "application/json"}))


def _post(path: str, body: dict, key: str):
    """One POST of body as JSON, the address the path alone: _open's answer."""
    return _open(urllib.request.Request(f"{BASE}{path}", json.dumps(body).encode(), method="POST", headers={
        "X-Api-Key": key, "Accept": "application/json", "Content-Type": "application/json"}))


def _open(request):
    """(the decoded JSON of one try, None), or (None, why it failed: one of REASONS or http_NNN)."""
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:  # a status, or a redirect the opener refused (app.auth)
        return None, "redirect" if 300 <= exc.code < 400 else STATUS_REASONS.get(exc.code, f"http_{exc.code}")
    except ValueError:  # not UTF-8, or not JSON
        return None, "bad_reply"
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        return None, "no_answer"


def _lookup(send, check):
    """(the reply, None) or (None, the last try's reason). A try is send(); it fails on send's reason or on
    check(reply)'s. A failed try is tried again when its reason is in AGAIN or a status from 500 to 599: TRIES in
    all, none begun once TRIES_SECONDS have gone since the first began. One warning line for each failed try;
    the call's row gets usda=reason when the lookup failed, and usda_tries=N when it took more than one and
    more than any earlier lookup of the call."""
    began = clock()
    for tries in range(1, TRIES + 1):
        reply, reason = send()
        reason = reason or check(reply)
        if reason is None:
            break
        log.warning("USDA lookup failed: %s (try %d)", reason, tries)
        if not (reason in AGAIN or reason.startswith("http_5")) or clock() - began >= TRIES_SECONDS:
            break
    if reason is not None:
        calllog.note("usda", reason)
    most = calllog.noted("usda_tries") or ""  # the most tries of any lookup in the call so far
    if tries > (int(most) if most.isdigit() else 1):
        calllog.note("usda_tries", str(tries))
    return (reply, None) if reason is None else (None, reason)


def _energy(nutrients) -> float | None:
    """kcal per 100 g from a food's nutrient list, in either of the two shapes FoodData Central uses."""
    if not isinstance(nutrients, list):
        return None
    for n in nutrients:
        if not isinstance(n, dict):
            continue
        inner = n.get("nutrient") if isinstance(n.get("nutrient"), dict) else n
        nid = inner.get("id", inner.get("nutrientId"))
        number = str(inner.get("number", inner.get("nutrientNumber", "")))
        unit = str(inner.get("unitName", "")).casefold()
        value = n.get("amount", n.get("value"))
        if (nid == ENERGY_ID or number == ENERGY_NUMBER) and unit == "kcal" and isinstance(value, (int, float)):
            return round(float(value), 2)
    return None


def _parsed(item) -> dict | None:
    """One food from a reply, or None when it lacks an id, a known data type or an energy value."""
    if not isinstance(item, dict):
        return None
    fdc_id, data_type = item.get("fdcId"), item.get("dataType")
    kcal = _energy(item.get("foodNutrients"))
    if not isinstance(fdc_id, int) or isinstance(fdc_id, bool) or data_type not in DATA_TYPES or kcal is None:
        return None
    return {"fdc_id": fdc_id, "description": str(item.get("description") or "")[:DESCRIPTION_MAX],
            "data_type": data_type, "kcal_per_100g": kcal}


def _fresh(fetched_at) -> bool:
    return fetched_at is not None and fetched_at >= db.utcnow() - timedelta(days=CACHE_DAYS)


def _upsert(s, model, key: str, values: dict) -> None:
    """Insert the row, or update it when another writer got there first: no race on the key."""
    insert = (postgresql if s.get_bind().dialect.name == "postgresql" else sqlite).insert
    statement = insert(model).values(**values)
    s.execute(statement.on_conflict_do_update(
        index_elements=[key], set_={k: statement.excluded[k] for k in values if k != key}))


def _cache(found: list[dict], query_key: str | None = None) -> None:
    """Store foods (and the search that found them) in the cache. Best effort: a failure here never
    costs the caller its answer."""
    now = db.utcnow()
    try:
        with db.session_scope() as s:
            for f in found:
                _upsert(s, UsdaFood, "fdc_id", {**f, "fetched_at": now})
            if query_key is not None:
                _upsert(s, UsdaSearch, "query_key", {"query_key": query_key, "fetched_at": now,
                                                     "fdc_ids": json.dumps([f["fdc_id"] for f in found])})
    except SQLAlchemyError:
        pass


def _as_dict(row: UsdaFood) -> dict:
    return {"fdc_id": row.fdc_id, "description": row.description, "data_type": row.data_type,
            "kcal_per_100g": row.kcal_per_100g}


def search(query: str) -> tuple[list[dict] | None, dict | None]:
    key = get_settings().usda_api_key
    if not key:
        return None, off()
    query_key = normalise(query)
    with db.session_scope() as s:
        cached = s.get(UsdaSearch, query_key)
        if cached is not None and _fresh(cached.fetched_at):
            try:
                ids = [int(i) for i in json.loads(cached.fdc_ids)]
            except (ValueError, TypeError):
                ids = None
            rows = {r.fdc_id: r for r in s.scalars(select(UsdaFood).where(UsdaFood.fdc_id.in_(ids or [])))}
            if ids is not None and all(i in rows for i in ids):
                return [_as_dict(rows[i]) for i in ids], None
    reply, reason = _lookup(
        lambda: _post("/foods/search", {"query": query_key, "dataType": list(DATA_TYPES), "pageSize": PAGE_SIZE}, key),
        lambda r: None if isinstance(r, dict) and isinstance(r.get("foods"), list) else "bad_reply")
    if reason is not None:
        return None, unavailable(reason)
    found = [f for f in (_parsed(item) for item in reply["foods"]) if f is not None]
    _cache(found, query_key)
    return found, None


def food(fdc_id: int) -> tuple[dict | None, dict | None]:
    key = get_settings().usda_api_key
    if not key:
        return None, off()
    with db.new_session() as s:
        row = s.get(UsdaFood, fdc_id)
        if row is not None and _fresh(row.fetched_at):
            return _as_dict(row), None

    def check(reply) -> str | None:
        found = _parsed(reply)
        if found is not None and found["fdc_id"] == fdc_id:
            return None
        # The food asked for, with no energy value; anything else is not the reply expected.
        asked = isinstance(reply, dict) and reply.get("fdcId") == fdc_id
        return "no_energy" if asked and _energy(reply.get("foodNutrients")) is None else "bad_reply"

    reply, reason = _lookup(lambda: _get(f"/food/{int(fdc_id)}", {}, key), check)
    if reason is not None:
        return None, unavailable(reason)
    found = _parsed(reply)
    _cache([found])
    return found, None
