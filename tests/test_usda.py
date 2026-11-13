"""The USDA client: the search as a POST and the food as a GET through a fake urlopen, the tries, the cache,
every failure as usda_unavailable, the key never seen."""

import contextvars
import http.client
import io
import json
import socket
import urllib.error
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import calllog, config, db, services
from app.models import CallLog, UsdaFood, UsdaSearch
from app.services import usda
from tests.conftest import settings as make_settings

KEY = "secret-usda-key-7731"


def banana(fdc_id=173944, kcal=89, data_type="SR Legacy", shape="search"):
    if shape == "search":
        nutrients = [{"nutrientId": 1003, "nutrientNumber": "203", "unitName": "G", "value": 1.1},
                     {"nutrientId": 1008, "nutrientNumber": "208", "unitName": "KCAL", "value": kcal}]
    else:
        nutrients = [{"nutrient": {"id": 1008, "number": "208", "name": "Energy", "unitName": "kcal"}, "amount": kcal}]
    return {"fdcId": fdc_id, "description": "Bananas, raw", "dataType": data_type, "foodNutrients": nutrients}


class Fake:
    """Stands in for urllib.request.urlopen: answers each request with the next reply, the last one again and
    again, or raises it. Keeps every request (its method, address, headers and body) with its timeout."""

    def __init__(self, *replies):
        self.replies, self.requests = replies, []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(reply, BaseException):
            raise reply
        body = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
        return io.BytesIO(body)


@pytest.fixture
def keyed():
    config.use_settings(make_settings(usda_api_key=KEY))


def fake(monkeypatch, *replies):
    f = Fake(*replies)
    monkeypatch.setattr(usda, "urlopen", f)
    return f


def status(code):
    return urllib.error.HTTPError(f"{usda.BASE}/foods/search?api_key={KEY}", code, "x", {}, None)


def as_a_call(lookup):
    """lookup() run as a call is: (its answer, the notes its call-log row gets)."""
    def call():
        row = calllog.start("find_food", db.utcnow(), None)
        notes = calllog.new_notes()
        answer = lookup()
        calllog.finish(row, 1, True, None, 1, notes=notes)
        with db.new_session() as s:
            return answer, s.get(CallLog, row).notes
    return contextvars.copy_context().run(call)


def usda_lines(logs):
    return [r.getMessage() for r in logs.records if r.name == "spotter.usda"]


def no_key_anywhere(*things):
    for thing in things:
        assert KEY not in json.dumps(thing, default=str), thing


def test_without_a_key_it_is_off_and_makes_no_call(monkeypatch):
    f = fake(monkeypatch, {"foods": []})
    assert usda.search("banana") == (None, usda.off())
    assert usda.food(1)[1]["error"] == "usda_off" and f.requests == []


def test_a_search_hit_is_parsed_cached_and_sent_with_the_key_in_a_header(keyed, monkeypatch):
    f = fake(monkeypatch, {"foods": [banana(), banana(2, 52, "Foundation"), banana(3, 60, "Branded"),
                                     {"fdcId": 4, "dataType": "Foundation", "foodNutrients": []}]})
    found, err = usda.search("  Banana ")
    assert err is None and [x["fdc_id"] for x in found] == [173944, 2]   # branded and energyless left out
    assert found[0] == {"fdc_id": 173944, "description": "Bananas, raw", "data_type": "SR Legacy",
                        "kcal_per_100g": 89.0}
    [(request, timeout)] = f.requests
    assert timeout == 5 and request.get_method() == "POST" and request.full_url == usda.BASE + "/foods/search"
    assert "?" not in request.full_url and request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"query": "banana", "dataType": ["Foundation", "SR Legacy", "Survey (FNDDS)"],
                                        "pageSize": 25}
    assert request.get_header("X-api-key") == KEY and KEY not in request.full_url + request.data.decode()
    with db.new_session() as s:
        cached = s.get(UsdaSearch, "banana")
        assert json.loads(cached.fdc_ids) == [173944, 2] and s.get(UsdaFood, 173944).kcal_per_100g == 89
    no_key_anywhere(found)


def test_a_cached_search_makes_no_call(keyed, monkeypatch):
    fake(monkeypatch, {"foods": [banana()]})
    usda.search("banana")
    f = fake(monkeypatch, socket.timeout("would fail"))
    found, err = usda.search("BANANA")
    assert err is None and found[0]["fdc_id"] == 173944 and f.requests == []


def test_a_stale_cache_is_fetched_again(keyed, monkeypatch):
    fake(monkeypatch, {"foods": [banana(kcal=89)]})
    usda.search("banana")
    with db.session_scope() as s:
        s.get(UsdaSearch, "banana").fetched_at = db.utcnow() - timedelta(days=31)
    f = fake(monkeypatch, {"foods": [banana(kcal=90)]})
    found, _ = usda.search("banana")
    assert len(f.requests) == 1 and found[0]["kcal_per_100g"] == 90


def test_a_miss_is_an_empty_list_and_is_cached(keyed, monkeypatch):
    fake(monkeypatch, {"foods": []})
    assert usda.search("zzz") == ([], None)
    f = fake(monkeypatch, socket.timeout())
    assert usda.search("zzz") == ([], None) and f.requests == []


@pytest.mark.parametrize("failure", [
    socket.timeout("timed out"),
    urllib.error.HTTPError(f"https://api.nal.usda.gov/fdc/v1/foods/search?api_key={KEY}", 403, "Forbidden", {}, None),
    urllib.error.HTTPError("https://api.nal.usda.gov/fdc/v1/foods/search", 500, "Server Error", {}, None),
    urllib.error.URLError(f"no route ({KEY})"),
    b"not json at all",
    b'{"unexpected": true}',
    b'["a list"]',
    http.client.IncompleteRead(b"{\"foods\": ["),
    http.client.BadStatusLine("HTTP/1.1 ???"),
], ids=["timeout", "403", "500", "network", "malformed", "wrong-shape", "list", "incomplete", "bad-status"])
def test_every_failure_is_usda_unavailable_and_never_carries_the_key(keyed, monkeypatch, failure):
    fake(monkeypatch, failure)
    found, err = usda.search("banana")
    assert found is None and err["error"] == "usda_unavailable" and "saved food" in err["fix"]
    found, err2 = usda.food(173944)
    assert found is None and err2["error"] == "usda_unavailable"
    no_key_anywhere(err, err2)
    with db.new_session() as s:
        assert s.get(UsdaSearch, "banana") is None   # a failure is not cached


def test_food_by_id_reads_the_detail_shape_and_caches(keyed, monkeypatch):
    f = fake(monkeypatch, banana(shape="detail"))
    found, err = usda.food(173944)
    assert err is None and found["kcal_per_100g"] == 89 and "/food/173944" in f.requests[0][0].full_url
    assert f.requests[0][0].get_method() == "GET" and f.requests[0][0].data is None
    assert f.requests[0][0].full_url == usda.BASE + "/food/173944"  # nothing after the id, not even a "?"
    f2 = fake(monkeypatch, socket.timeout())
    assert usda.food(173944)[0]["kcal_per_100g"] == 89 and f2.requests == []
    with db.session_scope() as s:
        s.get(UsdaFood, 173944).fetched_at = db.utcnow() - timedelta(days=31)
    f3 = fake(monkeypatch, banana(kcal=95, shape="detail"))
    assert usda.food(173944)[0]["kcal_per_100g"] == 95 and len(f3.requests) == 1


def test_food_by_id_without_energy_or_with_another_id_is_unavailable(keyed, monkeypatch):
    fake(monkeypatch, {"fdcId": 173944, "dataType": "SR Legacy", "description": "x", "foodNutrients": [
        {"nutrient": {"id": 1008, "number": "208", "unitName": "kJ"}, "amount": 372}]})
    assert usda.food(173944)[1]["error"] == "usda_unavailable"
    fake(monkeypatch, banana(fdc_id=1, shape="detail"))
    assert usda.food(173944)[1]["error"] == "usda_unavailable"


def test_energy_falls_back_to_the_nutrient_number(keyed, monkeypatch):
    item = {"fdcId": 5, "dataType": "Survey (FNDDS)", "description": "Dal",
            "foodNutrients": [{"nutrientNumber": "208", "unitName": "KCAL", "value": 116}]}
    fake(monkeypatch, {"foods": [item]})
    assert usda.search("dal")[0][0]["kcal_per_100g"] == 116


def test_the_key_never_reaches_a_result_through_the_services(keyed, monkeypatch, make_user):
    person = make_user()
    fake(monkeypatch, urllib.error.HTTPError(f"https://x/?api_key={KEY}", 403, f"bad key {KEY}", {}, None))
    results = [services.find_food(person.id, "banana"),
               services.log_food(person.id, {"entries": [{"usda_fdc_id": 173944, "grams": 100}]}, "claude")]
    no_key_anywhere(*results)
    assert results[1]["error"] == "usda_unavailable"


def test_a_long_query_is_cut_to_the_cache_key_length(keyed, monkeypatch):
    fake(monkeypatch, {"foods": []})
    usda.search("b" * 500)
    with db.new_session() as s:
        assert [len(r.query_key) for r in s.scalars(select(UsdaSearch))] == [128]



def test_a_redirect_is_refused_and_the_key_never_travels(keyed, monkeypatch):
    """The real opener, with only the network faked: a 302 to another host must not be followed."""
    import email.message
    import urllib.request
    import urllib.response
    seen = []

    def https_open(self, request):
        seen.append((request.full_url, request.get_header("X-api-key")))
        headers = email.message.Message()
        headers["Location"] = "https://collector.example.net/steal"
        response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
        response.msg = "Found"
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, "https_open", https_open)
    found, err = usda.search("banana")
    assert found is None and err["error"] == "usda_unavailable"
    assert len(seen) == 1 and seen[0][0].startswith(usda.BASE)
    assert all("collector.example.net" not in url for url, _ in seen)


def test_a_cache_store_that_fails_still_returns_the_answer(keyed, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    fake(monkeypatch, {"foods": [banana()]})

    def collide(*args, **kwargs):  # as when another first search for the same query wrote the row first
        raise IntegrityError("INSERT INTO usda_search", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(usda, "_upsert", collide)
    found, err = usda.search("banana")
    assert err is None and found[0]["fdc_id"] == 173944
    fake(monkeypatch, banana(shape="detail"))
    assert usda.food(173944)[0]["kcal_per_100g"] == 89


def test_two_first_searches_for_one_query_both_answer_and_leave_one_cache_row(keyed, monkeypatch):
    import threading
    barrier = threading.Barrier(2)

    class Slow(Fake):
        def __call__(self, request, timeout=None):
            barrier.wait(timeout=5)  # both have missed the cache before either stores
            return super().__call__(request, timeout)

    monkeypatch.setattr(usda, "urlopen", Slow({"foods": [banana()]}))
    results, failures = [], []

    def go():
        try:
            results.append(usda.search("banana"))
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert not failures and [r[1] for r in results] == [None, None]
    with db.new_session() as s:
        assert len(list(s.scalars(select(UsdaSearch)))) == 1 and len(list(s.scalars(select(UsdaFood)))) == 1


# ---------------------------------------------------------------- tries (contract usda-steady.md)

def test_a_400_then_an_answer_answers_on_the_second_try(keyed, monkeypatch, logs):
    f = fake(monkeypatch, status(400), {"foods": [banana()]})
    (found, err), notes = as_a_call(lambda: usda.search("banana"))
    assert err is None and found[0]["fdc_id"] == 173944 and len(f.requests) == 2 and notes == "usda_tries=2"
    assert usda_lines(logs) == ["USDA lookup failed: bad_request (try 1)"]
    f = fake(monkeypatch, status(400), banana(5, shape="detail"))  # the food by id: still a GET, the same rule
    (found, err), notes = as_a_call(lambda: usda.food(5))
    assert err is None and found["kcal_per_100g"] == 89 and notes == "usda_tries=2"
    assert [r.get_method() for r, _ in f.requests] == ["GET", "GET"]


def test_three_400s_fail_after_exactly_three_and_say_nothing_of_the_query_the_key_or_the_address(keyed, monkeypatch,
                                                                                                 logs):
    query = "marker-query-5521"
    f = fake(monkeypatch, status(400))
    (found, err), notes = as_a_call(lambda: usda.search(query))
    assert found is None and err == usda.unavailable("bad_request") and len(f.requests) == 3
    assert notes == "usda=bad_request usda_tries=3"
    assert usda_lines(logs) == [f"USDA lookup failed: bad_request (try {n})" for n in (1, 2, 3)]
    spotter_lines = [r.getMessage() for r in logs.records if r.name.startswith("spotter")]
    for text in (notes, json.dumps(err), *spotter_lines):
        assert KEY not in text and query not in text and "nal.usda.gov" not in text and "https://" not in text, text


@pytest.mark.parametrize("failure, reason, tries", [
    (status(503), "http_503", 3), (status(500), "http_500", 3), (socket.timeout("timed out"), "no_answer", 3),
    (urllib.error.URLError("no route"), "no_answer", 3), (status(401), "key_refused", 1),
    (status(403), "key_refused", 1), (status(429), "rate_limited", 1), (status(302), "redirect", 1),
    (status(404), "http_404", 1), (b"not json at all", "bad_reply", 1), (b'{"unexpected": true}', "bad_reply", 1),
], ids=["503", "500", "timeout", "network", "401", "403", "429", "redirect", "404", "broken", "wrong-shape"])
def test_only_bad_request_a_5xx_and_no_answer_are_tried_again(keyed, monkeypatch, failure, reason, tries):
    for lookup in (lambda: usda.search("banana"), lambda: usda.food(173944)):
        f = fake(monkeypatch, failure)
        (found, err), notes = as_a_call(lookup)
        assert found is None and err == usda.unavailable(reason) and len(f.requests) == tries
        assert notes == f"usda={reason}" + (f" usda_tries={tries}" if tries > 1 else "")


@pytest.mark.parametrize("seconds, tries", [(4.0, 2), (3.9, 3)])
def test_no_new_try_once_8_seconds_have_gone_since_the_first_began(keyed, monkeypatch, seconds, tries):
    now = [100.0]
    monkeypatch.setattr(usda, "clock", lambda: now[0])

    class Slow(Fake):  # each try takes `seconds` by the test's clock
        def __call__(self, request, timeout=None):
            now[0] += seconds
            return super().__call__(request, timeout)

    for lookup in (lambda: usda.search("banana"), lambda: usda.food(173944)):
        f = Slow(status(503))
        monkeypatch.setattr(usda, "urlopen", f)
        (found, err), notes = as_a_call(lookup)
        assert err["error"] == "usda_unavailable" and len(f.requests) == tries and notes.endswith(f"usda_tries={tries}")


def test_a_call_of_several_lookups_notes_the_most_tries_any_one_took(keyed, monkeypatch):
    def three_then_two():
        fake(monkeypatch, status(503), status(503), banana(1, shape="detail"))
        first = usda.food(1)
        fake(monkeypatch, status(503), banana(2, shape="detail"))
        return first, usda.food(2)

    (first, second), notes = as_a_call(three_then_two)
    assert first[1] is None and second[1] is None and notes == "usda_tries=3"

    def two_then_a_refused_key():
        fake(monkeypatch, status(503), banana(3, shape="detail"))
        usda.food(3)
        f = fake(monkeypatch, status(401))
        return usda.food(4), len(f.requests)

    ((found, err), requests), notes = as_a_call(two_then_a_refused_key)
    assert err == usda.unavailable("key_refused") and requests == 1
    assert notes == "usda_tries=2 usda=key_refused"  # the call's most tries, beside the reason of the one that failed
