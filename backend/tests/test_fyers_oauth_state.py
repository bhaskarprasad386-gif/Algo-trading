from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from app.brokers.fyers_oauth_state import FyersOAuthStateStore


def test_create_builds_registered_fyers_auth_url_and_random_state():
    store = FyersOAuthStateStore(token_factory=lambda size: "random-state")
    state, url = store.create(
        app_id="APP-100",
        redirect_uri="https://example.com/fyers/callback",
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert state == "random-state"
    assert parsed.scheme == "https"
    assert parsed.netloc == "api-t1.fyers.in"
    assert parsed.path == "/api/v3/generate-authcode"
    assert query == {
        "client_id": ["APP-100"],
        "redirect_uri": ["https://example.com/fyers/callback"],
        "response_type": ["code"],
        "state": ["random-state"],
    }


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://example.com/callback",
        "https:///callback",
        "https://user:password@example.com/callback",
        "https://example.com/callback#fragment",
        "",
    ],
)
def test_rejects_unsafe_or_invalid_redirect_uri(redirect_uri):
    store = FyersOAuthStateStore()
    with pytest.raises(ValueError, match="registered HTTPS"):
        store.create(app_id="APP-100", redirect_uri=redirect_uri)


def test_state_is_single_use_and_binds_app_and_redirect():
    store = FyersOAuthStateStore(token_factory=lambda size: "once")
    state, _ = store.create(
        app_id="APP-100",
        redirect_uri="https://example.com/fyers/callback",
    )
    record = store.consume(
        state=state,
        app_id="APP-100",
        redirect_uri="https://example.com/fyers/callback",
    )
    assert record.app_id == "APP-100"
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            state=state,
            app_id="APP-100",
            redirect_uri="https://example.com/fyers/callback",
        )


def test_wrong_app_or_redirect_consumes_state_and_rejects():
    store = FyersOAuthStateStore(token_factory=lambda size: "bound")
    state, _ = store.create(
        app_id="APP-100",
        redirect_uri="https://example.com/fyers/callback",
    )
    with pytest.raises(ValueError, match="does not match"):
        store.consume(
            state=state,
            app_id="OTHER",
            redirect_uri="https://example.com/fyers/callback",
        )
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            state=state,
            app_id="APP-100",
            redirect_uri="https://example.com/fyers/callback",
        )


def test_expired_state_is_rejected():
    now = [100.0]
    store = FyersOAuthStateStore(
        ttl_seconds=30,
        clock=lambda: now[0],
        token_factory=lambda size: "expired",
    )
    state, _ = store.create(
        app_id="APP-100",
        redirect_uri="https://example.com/fyers/callback",
    )
    now[0] = 130.0
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            state=state,
            app_id="APP-100",
            redirect_uri="https://example.com/fyers/callback",
        )


def test_store_is_bounded():
    counter = iter(("state-1", "state-2", "state-3"))
    store = FyersOAuthStateStore(
        max_entries=2,
        token_factory=lambda size: next(counter),
    )
    first, _ = store.create(
        app_id="APP-100",
        redirect_uri="https://example.com/fyers/callback",
    )
    store.create(app_id="APP-100", redirect_uri="https://example.com/fyers/callback")
    store.create(app_id="APP-100", redirect_uri="https://example.com/fyers/callback")
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            state=first,
            app_id="APP-100",
            redirect_uri="https://example.com/fyers/callback",
        )
