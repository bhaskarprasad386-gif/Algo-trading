from __future__ import annotations

import pytest

from app.brokers.shoonya_oauth_state import ShoonyaOAuthFlowStore


def test_flow_is_bound_to_client_redirect_uid_and_account():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "flow-id")
    flow_id = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    pending = store.consume(
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.uid == "SH12345"
    assert pending.account_id == "SH12345"


def test_flow_is_single_use():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "single-use")
    flow_id = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    store.consume(
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            flow_id=flow_id,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
        )


def test_flow_expires():
    now = [10.0]
    store = ShoonyaOAuthFlowStore(
        ttl_seconds=30,
        clock=lambda: now[0],
        token_factory=lambda size: "expires",
    )
    flow_id = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    now[0] = 40.0
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            flow_id=flow_id,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
        )


def test_wrong_client_consumes_and_rejects_flow():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "wrong-client")
    flow_id = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    with pytest.raises(ValueError, match="does not match"):
        store.consume(
            flow_id=flow_id,
            client_id="other-client",
            redirect_uri="https://example.com/shoonya/callback",
        )
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            flow_id=flow_id,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
        )


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
def test_rejects_unsafe_callback_uri(redirect_uri):
    store = ShoonyaOAuthFlowStore()
    with pytest.raises(ValueError, match="registered HTTPS"):
        store.create(
            client_id="client-100",
            redirect_uri=redirect_uri,
            uid="SH12345",
            account_id="SH12345",
        )


def test_store_is_bounded():
    values = iter(("flow-1", "flow-2", "flow-3"))
    store = ShoonyaOAuthFlowStore(
        max_entries=2,
        token_factory=lambda size: next(values),
    )
    first = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            flow_id=first,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
        )


def test_concurrent_consumers_cannot_reuse_same_oauth_flow():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier


    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "race-flow")
    flow_id = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    barrier = Barrier(2)


    def consume_once():
        barrier.wait(timeout=2)
        try:
            store.consume(
                flow_id=flow_id,
                client_id="client-100",
                redirect_uri="https://example.com/shoonya/callback",
            )
            return "accepted"
        except ValueError:
            return "rejected"


    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume_once(), range(2)))
    assert sorted(results) == ["accepted", "rejected"]


def test_flow_id_collision_does_not_overwrite_or_evict_existing_flow():
    store = ShoonyaOAuthFlowStore(
        max_entries=1,
        token_factory=lambda size: "duplicate-flow",
    )
    flow_id = store.create(
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )

    with pytest.raises(RuntimeError, match="flow ID collision"):
        store.create(
            client_id="client-200",
            redirect_uri="https://example.com/shoonya/callback",
            uid="SH99999",
            account_id="SH99999",
        )

    pending = store.consume(
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.uid == "SH12345"
    assert pending.account_id == "SH12345"
