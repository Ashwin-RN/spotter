"""Links: every page name builds from the site address in the settings, never from a request."""

import dataclasses
from datetime import date

import pytest

from app import config
from app.links import NAMES, page, slug

SITE = "https://spotter.example.app"


@pytest.mark.parametrize("name, params, path", [
    ("home", {}, "/"),
    ("sign_in", {}, "/sign-in"),
    ("settings", {}, "/settings"),
    ("today", {}, "/today"),
    ("day", {"date": date(2026, 11, 9)}, "/day/2026-11-09"),
    ("day", {"date": "2026-11-09"}, "/day/2026-11-09"),
    ("progress", {}, "/progress"),
    ("progress_lift", {"lift": "Back Squat"}, "/progress/back-squat"),
    ("goals", {}, "/goals"),
    ("foods", {}, "/foods"),
])
def test_every_page_builds(name, params, path):
    assert page(name, **params) == SITE + path


def test_every_name_is_covered():
    assert set(NAMES) == {"home", "sign_in", "settings", "today", "day", "progress", "progress_lift", "goals",
                          "foods"}


@pytest.mark.parametrize("lift, expected", [
    ("Back Squat", "back-squat"),
    ("Farmer's Carry", "farmers-carry"),
    ("Step-Up", "step-up"),
    ("Dumbbell Romanian Deadlift", "dumbbell-romanian-deadlift"),
    ("  Pull-Up  ", "pull-up"),
])
def test_the_lift_slug(lift, expected):
    assert slug(lift) == expected


def test_the_origin_comes_from_the_settings():
    config.use_settings(dataclasses.replace(config.get_settings(), mcp_resource_url="http://localhost:8000/mcp"))
    assert page("settings") == "http://localhost:8000/settings"


def test_nothing_request_shaped_can_change_the_origin():
    with pytest.raises(TypeError):
        page("settings", host="evil.example")
    with pytest.raises(TypeError):
        page("progress_lift", lift="Back Squat", base_url="https://evil.example")
    assert page("settings") == SITE + "/settings"


def test_no_link_carries_a_secret():
    config.use_settings(dataclasses.replace(config.get_settings(), workos_api_key="sk_secret_456",
                                            session_secret="session-secret-789"))
    built = [page(n) for n in NAMES if n not in ("day", "progress_lift")]
    built += [page("day", date="2026-11-09"), page("progress_lift", lift="Back Squat")]
    for link in built:
        assert "?" not in link and "#" not in link
        assert "secret" not in link and "sk_" not in link


def test_an_unknown_name_raises():
    with pytest.raises(ValueError):
        page("admin")


@pytest.mark.parametrize("name, params", [
    ("day", {}), ("day", {"date": "9 Nov"}), ("progress_lift", {}), ("progress_lift", {"lift": "!!"}),
])
def test_a_missing_or_bad_parameter_raises(name, params):
    with pytest.raises((TypeError, ValueError)):
        page(name, **params)


def test_without_a_site_address_links_refuse():
    config.use_settings(dataclasses.replace(config.get_settings(), mcp_resource_url=None))
    with pytest.raises(RuntimeError):
        page("home")
