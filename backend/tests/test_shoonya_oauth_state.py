from __future__ import annotations

import pytest

from app.brokers.shoonya_oauth_state import ShoonyaOAuthFlowStore


def test_flow_is_bound_to_client_redirect_uid_and_account():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "flow-id")
    flow_id = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    pending = store.consume(
        owner_id=7,
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.uid == "SH12345"
    assert pending.account_id == "SH12345"


def test_flow_is_single_use():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "single-use")
    flow_id = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    store.consume(
        owner_id=7,
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            owner_id=7,
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
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    now[0] = 40.0
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(
            owner_id=7,
            flow_id=flow_id,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
        )


def test_wrong_client_does_not_invalidate_legitimate_flow():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "wrong-client")
    flow_id = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    with pytest.raises(ValueError, match="does not match"):
        store.consume(
            owner_id=7,
            flow_id=flow_id,
            client_id="other-client",
            redirect_uri="https://example.com/shoonya/callback",
        )
    pending = store.consume(
        owner_id=7,
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.uid == "SH12345"
    assert pending.account_id == "SH12345"


def test_wrong_redirect_does_not_invalidate_legitimate_flow():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "wrong-redirect")
    flow_id = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    with pytest.raises(ValueError, match="does not match"):
        store.consume(
            owner_id=7,
            flow_id=flow_id,
            client_id="client-100",
            redirect_uri="https://attacker.example/callback",
        )
    pending = store.consume(
        owner_id=7,
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.owner_id == 7


def test_expired_flow_is_pruned_to_release_capacity():
    now = [100.0]
    values = iter(("expired-flow", "fresh-flow"))
    store = ShoonyaOAuthFlowStore(
        ttl_seconds=10,
        max_entries=1,
        clock=lambda: now[0],
        token_factory=lambda size: next(values),
    )
    store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    now[0] = 110.0
    fresh = store.create(
        owner_id=8,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH99999",
        account_id="SH99999",
    )
    assert store.consume(
        owner_id=8,
        flow_id=fresh,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    ).owner_id == 8


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://example.com/callback",
        "https:///callback",
        "https://user:password@example.com/callback",
        "https://example.com/callback#fragment",
    ],
)
def test_rejects_unsafe_callback_uri(redirect_uri):
    store = ShoonyaOAuthFlowStore()
    with pytest.raises(ValueError, match="registered HTTPS"):
        store.create(
            owner_id=7,
            client_id="client-100",
            redirect_uri=redirect_uri,
            uid="SH12345",
            account_id="SH12345",
        )


def test_store_is_bounded_without_evicting_valid_flows():
    values = iter(("flow-1", "flow-2", "flow-3"))
    store = ShoonyaOAuthFlowStore(
        max_entries=2,
        token_factory=lambda size: next(values),
    )
    first = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )
    second = store.create(
        owner_id=8,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH99999",
        account_id="SH99999",
    )
    with pytest.raises(RuntimeError, match="capacity is exhausted"):
        store.create(
            owner_id=9,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
            uid="SH00000",
            account_id="SH00000",
        )
    assert store.consume(
        owner_id=7,
        flow_id=first,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    ).owner_id == 7
    assert store.consume(
        owner_id=8,
        flow_id=second,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    ).owner_id == 8



def test_capacity_does_not_evict_another_valid_flow():
    values = iter(("owner-a-flow", "owner-b-flow"))
    store = ShoonyaOAuthFlowStore(
        max_entries=1,
        token_factory=lambda size: next(values),
    )
    first = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )

    with pytest.raises(RuntimeError, match="capacity is exhausted"):
        store.create(
            owner_id=8,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
            uid="SH99999",
            account_id="SH99999",
        )

    pending = store.consume(
        owner_id=7,
        flow_id=first,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.owner_id == 7

def test_concurrent_consumers_cannot_reuse_same_oauth_flow():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier


    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "race-flow")
    flow_id = store.create(
        owner_id=7,
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
                owner_id=7,
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
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )

    with pytest.raises(RuntimeError, match="flow ID collision"):
        store.create(
            owner_id=7,
            client_id="client-200",
            redirect_uri="https://example.com/shoonya/callback",
            uid="SH99999",
            account_id="SH99999",
        )

    pending = store.consume(
        owner_id=7,
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.uid == "SH12345"
    assert pending.account_id == "SH12345"


def test_wrong_owner_cannot_consume_or_invalidate_another_owners_flow():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "owner-bound-flow")
    flow_id = store.create(
        owner_id=7,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
        uid="SH12345",
        account_id="SH12345",
    )

    with pytest.raises(ValueError, match="does not belong to this owner"):
        store.consume(
            owner_id=8,
            flow_id=flow_id,
            client_id="client-100",
            redirect_uri="https://example.com/shoonya/callback",
        )

    pending = store.consume(
        owner_id=7,
        flow_id=flow_id,
        client_id="client-100",
        redirect_uri="https://example.com/shoonya/callback",
    )
    assert pending.owner_id == 7


def test_manual_flow_needs_no_redirect_uri_and_is_single_use():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "manual-flow")
    flow_id = store.create(
        owner_id=17,
        client_id="client-100",
        uid="SH12345",
        account_id="SH12345",
    )
    pending = store.consume(
        owner_id=17,
        flow_id=flow_id,
        client_id="client-100",
    )
    assert pending.redirect_uri is None
    assert pending.uid == "SH12345"
    with pytest.raises(ValueError, match="missing, expired, or already used"):
        store.consume(owner_id=17, flow_id=flow_id, client_id="client-100")


def test_manual_flow_rejects_wrong_owner_and_wrong_client_without_consuming():
    store = ShoonyaOAuthFlowStore(token_factory=lambda size: "manual-bound-flow")
    flow_id = store.create(
        owner_id=17,
        client_id="client-100",
        uid="SH12345",
        account_id="SH12345",
    )
    with pytest.raises(ValueError, match="does not belong to this owner"):
        store.consume(owner_id=18, flow_id=flow_id, client_id="client-100")
    with pytest.raises(ValueError, match="does not match"):
        store.consume(owner_id=17, flow_id=flow_id, client_id="other-client")
    assert store.consume(owner_id=17, flow_id=flow_id, client_id="client-100").owner_id == 17
