"""Stage 2 settings: the database address, web sign-in, the session key, the signup cap, and secrets
kept out of every repr."""

import dataclasses

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import (
    DEFAULT_DATABASE_URL, NO_DATABASE, ROOT, SESSION_KEY_LABEL, get_settings, load_settings, use_settings,
)

from .conftest import RESOURCE

BASE = {"AUTHKIT_DOMAIN": "example.authkit.app", "MCP_RESOURCE_URL": RESOURCE}


def test_defaults_when_nothing_new_is_set():
    s = load_settings(BASE)
    assert s.configured  # stage 1 still works with only its own settings
    assert s.database_url == DEFAULT_DATABASE_URL
    assert s.database_url.startswith("sqlite:///") and s.database_url.endswith("/spotter.db")
    assert s.database_url == f"sqlite:///{(ROOT / 'spotter.db').as_posix()}"
    assert s.auth_provider == "workos"
    assert s.signups_per_day == 50
    assert not s.web_sign_in_configured and s.session_key is None


def test_the_new_settings_are_read():
    s = load_settings({**BASE, "DATABASE_URL": "sqlite:///elsewhere.db", "WORKOS_CLIENT_ID": " client_01 ",
                       "WORKOS_API_KEY": "sk_test_1", "SESSION_SECRET": "s3", "AUTH_PROVIDER": "other",
                       "SIGNUPS_PER_DAY": "7"})
    assert s.database_url == "sqlite:///elsewhere.db"
    assert s.workos_client_id == "client_01" and s.workos_api_key == "sk_test_1"
    assert s.session_secret == "s3" and s.auth_provider == "other" and s.signups_per_day == 7


@pytest.mark.parametrize("given, used", [
    ("postgres://u:p@db.host:5432/spotter", "postgresql+psycopg://u:p@db.host:5432/spotter"),
    ("postgresql://u:p@db.host/spotter", "postgresql+psycopg://u:p@db.host/spotter"),
    ("postgresql+psycopg://u@h/x", "postgresql+psycopg://u@h/x"),
    ("sqlite:///x.db", "sqlite:///x.db"),
    ("  ", DEFAULT_DATABASE_URL),
])
def test_the_database_address_is_given_the_psycopg_driver(given, used):
    assert load_settings({**BASE, "DATABASE_URL": given}).database_url == used


@pytest.mark.parametrize("value, used", [("0", 0), ("12", 12), ("many", 50), ("-3", 50), ("", 50), ("1.5", 50)])
def test_a_bad_signup_cap_falls_back_to_the_default(value, used):
    assert load_settings({**BASE, "SIGNUPS_PER_DAY": value}).signups_per_day == used


def test_web_sign_in_needs_both_workos_values():
    assert not load_settings({**BASE, "WORKOS_API_KEY": "sk"}).web_sign_in_configured
    assert not load_settings({**BASE, "WORKOS_CLIENT_ID": "client_01"}).web_sign_in_configured
    assert load_settings({**BASE, "WORKOS_API_KEY": "sk", "WORKOS_CLIENT_ID": "client_01"}).web_sign_in_configured


def test_the_site_origin_is_the_origin_of_the_resource_address():
    assert load_settings(BASE).site_origin == "https://spotter.example.app"
    assert load_settings({**BASE, "MCP_RESOURCE_URL": "http://localhost:8000/mcp"}).site_origin == \
        "http://localhost:8000"
    assert load_settings({}).site_origin is None
    assert load_settings({**BASE, "MCP_RESOURCE_URL": "spotter.example.app/mcp"}).site_origin is None


def test_the_session_key_is_derived_from_the_api_key_and_stable():
    a1 = load_settings({**BASE, "WORKOS_API_KEY": "sk_test_a"}).session_key
    a2 = load_settings({**BASE, "WORKOS_API_KEY": "sk_test_a"}).session_key
    b = load_settings({**BASE, "WORKOS_API_KEY": "sk_test_b"}).session_key
    assert a1 == a2 and a1 != b
    assert len(a1) == 64 and "sk_test_a" not in a1  # 32 bytes, hex; not the key itself


def test_session_secret_wins_over_the_derived_key():
    s = load_settings({**BASE, "WORKOS_API_KEY": "sk_test_a", "SESSION_SECRET": "chosen-secret"})
    assert s.session_key == "chosen-secret"


def test_secrets_never_appear_in_a_repr():
    s = load_settings({**BASE, "DATABASE_URL": "postgresql://u:db-pass-123@h/x", "WORKOS_CLIENT_ID": "client_01",
                       "WORKOS_API_KEY": "sk_secret_456", "SESSION_SECRET": "session-secret-789",
                       "USDA_API_KEY": "usda-key-246"})
    for text in (repr(s), str(s)):
        for secret in ("db-pass-123", "sk_secret_456", "session-secret-789", s.session_key, "usda-key-246"):
            assert secret not in text
    assert "client_01" in repr(s)  # public values still show
    hidden = {f.name for f in dataclasses.fields(s) if not f.repr}
    assert hidden == {"database_url", "workos_api_key", "session_secret", "usda_api_key"}


def test_the_usda_key_is_read_and_off_when_unset():
    assert load_settings(BASE).usda_api_key is None
    assert load_settings({**BASE, "USDA_API_KEY": "  "}).usda_api_key is None
    assert load_settings({**BASE, "USDA_API_KEY": " k-1 "}).usda_api_key == "k-1"


def test_get_settings_reads_once_and_use_settings_replaces(app_settings):
    assert get_settings() is app_settings
    other = dataclasses.replace(app_settings, signups_per_day=3)
    use_settings(other)
    assert get_settings() is other


# ---------------------------------------------------------------- no silent SQLite on a real host

@pytest.mark.parametrize("resource", ["https://spotter.example.app/mcp", "https://spotter.example.app:8443/mcp"])
def test_a_public_https_site_without_database_url_is_a_configuration_error(resource):
    s = load_settings({**BASE, "MCP_RESOURCE_URL": resource})
    assert s.database_problem == NO_DATABASE
    assert "DATABASE_URL is not set" in s.database_problem
    assert s.configured  # the sign-in side is unaffected; only the database refuses


@pytest.mark.parametrize("resource", [None, "http://spotter.example.app/mcp", "http://localhost:8000/mcp",
                                      "https://localhost:8443/mcp", "https://127.0.0.1/mcp", "https://[::1]/mcp"])
def test_local_development_keeps_the_sqlite_default(resource):
    env = {"AUTHKIT_DOMAIN": "example.authkit.app"}
    if resource:
        env["MCP_RESOURCE_URL"] = resource
    s = load_settings(env)
    assert s.database_url == DEFAULT_DATABASE_URL and s.database_problem is None


def test_a_set_database_url_is_never_a_problem():
    s = load_settings({**BASE, "DATABASE_URL": "postgres://u:p@h/x"})
    assert s.database_problem is None


@pytest.mark.parametrize("given", ["POSTGRES://u:p@h/x", "PostgreSQL://u:p@h/x", "Postgres://u:p@h/x"])
def test_the_scheme_is_matched_in_any_case(given):
    assert load_settings({**BASE, "DATABASE_URL": given}).database_url == "postgresql+psycopg://u:p@h/x"


def test_the_label_is_part_of_the_derived_key():
    key = load_settings({**BASE, "WORKOS_API_KEY": "sk_test_a"}).session_key
    unlabelled = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=None).derive(b"sk_test_a").hex()
    labelled = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=SESSION_KEY_LABEL).derive(b"sk_test_a").hex()
    assert key == labelled and key != unlabelled
