import pytest

from app.market_data.provider_readiness import ProviderReadiness


def _open(provider="shoonya", *, ack_supported=True):
    state = ProviderReadiness(provider, subscription_ack_supported=ack_supported)
    state.begin_generation(1)
    assert state.transport_open(1)
    return state


def test_shoonya_requires_positive_current_generation_auth_ack():
    state = _open()
    assert state.snapshot()["authentication"] == "UNKNOWN"
    assert state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    snapshot = state.snapshot()
    assert snapshot["authenticated"] is True
    assert snapshot["authentication_evidence_generation"] == 1


def test_shoonya_rejection_and_timeout_never_authenticate():
    rejected = _open()
    assert not rejected.record_authentication_ack(
        1, accepted=False, evidence_kind="shoonya_ak_ack"
    )
    assert rejected.snapshot()["authentication"] == "REJECTED"
    timed_out = _open()
    assert timed_out.authentication_timeout(1)
    assert timed_out.snapshot()["authentication"] == "TIMED_OUT"
    assert timed_out.snapshot()["authenticated"] is False


def test_angel_transport_open_or_market_tick_cannot_prove_authentication():
    state = _open("angel_one", ack_supported=False)
    assert not state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.record_market_event(
        1, canonical_identity_valid=True, exchange_timestamp_valid=True, fresh=True
    )
    snapshot = state.snapshot()
    assert snapshot["transport"] == "OPEN"
    assert snapshot["authentication"] == "NOT_VERIFIABLE"
    assert snapshot["authenticated"] is False
    assert snapshot["feed_ready"] is False
    assert snapshot["parity_eligible"] is False


def test_heartbeat_is_liveness_not_market_data_freshness():
    state = _open()
    assert state.record_heartbeat(1)
    snapshot = state.snapshot()
    assert snapshot["last_liveness"] == "HEARTBEAT"
    assert snapshot["market_event_fresh"] is False
    assert snapshot["feed_ready"] is False


def test_subscription_request_without_ack_is_not_ready():
    state = _open()
    assert state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.request_subscriptions(1, ["NFO|123"])
    snapshot = state.snapshot()
    assert snapshot["subscription"] == "REQUEST_SENT"
    assert snapshot["feed_ready"] is False


def test_subscription_without_ack_protocol_is_not_verifiable():
    state = _open(ack_supported=False)
    assert state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.request_subscriptions(1, ["NFO|123"])
    assert not state.acknowledge_subscriptions(1, ["NFO|123"])
    assert state.snapshot()["subscription"] == "NOT_VERIFIABLE"
    assert state.snapshot()["feed_ready"] is False


def test_feed_ready_requires_auth_subscription_and_valid_fresh_market_event():
    state = _open()
    assert state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.request_subscriptions(1, ["NFO|123"])
    assert state.acknowledge_subscriptions(1, ["NFO|123"])
    assert state.record_market_event(
        1, canonical_identity_valid=True, exchange_timestamp_valid=True, fresh=True
    )
    snapshot = state.snapshot()
    assert snapshot["subscription"] == "ACKNOWLEDGED"
    assert snapshot["feed_ready"] is True
    assert snapshot["parity_eligible"] is True
    assert snapshot["order_eligible"] is False


@pytest.mark.parametrize(
    "identity,timestamp,fresh",
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_invalid_or_stale_market_event_never_makes_feed_ready(identity, timestamp, fresh):
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    state.request_subscriptions(1, ["NFO|123"])
    state.acknowledge_subscriptions(1, ["NFO|123"])
    assert not state.record_market_event(
        1, canonical_identity_valid=identity,
        exchange_timestamp_valid=timestamp, fresh=fresh,
    )
    assert state.snapshot()["feed_ready"] is False


def test_generation_change_clears_auth_subscription_and_freshness():
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    state.request_subscriptions(1, ["NFO|123"])
    state.acknowledge_subscriptions(1, ["NFO|123"])
    state.record_market_event(
        1, canonical_identity_valid=True, exchange_timestamp_valid=True, fresh=True
    )
    state.begin_generation(2)
    snapshot = state.snapshot()
    assert snapshot["transport"] == "OPENING"
    assert snapshot["authentication"] == "UNKNOWN"
    assert snapshot["subscription"] == "NOT_REQUESTED"
    assert snapshot["market_event_fresh"] is False
    assert snapshot["feed_ready"] is False


def test_stale_generation_events_are_rejected():
    state = _open()
    state.begin_generation(2)
    assert not state.transport_open(1)
    assert not state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.snapshot()["authenticated"] is False
    assert state.snapshot()["ignored_stale_generation"] == 2


def test_integrity_fault_latches_until_new_generation():
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    state.request_subscriptions(1, ["NFO|123"])
    state.acknowledge_subscriptions(1, ["NFO|123"])
    state.record_market_event(
        1, canonical_identity_valid=True, exchange_timestamp_valid=True, fresh=True
    )
    assert state.latch_integrity_fault(1, "queue overflow")
    snapshot = state.snapshot()
    assert snapshot["integrity_fault"] == "queue overflow"
    assert snapshot["feed_ready"] is False
    state.begin_generation(2)
    assert state.snapshot()["integrity_fault"] is None


def test_transport_close_invalidates_auth_and_feed_freshness():
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    assert state.transport_closed(1)
    snapshot = state.snapshot()
    assert snapshot["transport"] == "CLOSED"
    assert snapshot["authenticated"] is False
    assert snapshot["feed_ready"] is False


def test_invalid_evidence_types_are_rejected():
    state = _open()
    with pytest.raises(TypeError):
        state.record_authentication_ack(1, accepted="true", evidence_kind="shoonya_ak_ack")
    with pytest.raises(TypeError):
        state.record_market_event(
            1, canonical_identity_valid=1, exchange_timestamp_valid=True, fresh=True
        )



def test_partial_subscription_acknowledgements_accumulate_until_full_coverage():
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    state.request_subscriptions(1, ["NFO|123", "NFO|124"])
    assert state.acknowledge_subscriptions(1, ["NFO|123"])
    assert state.snapshot()["subscription"] == "REQUEST_SENT"
    assert state.acknowledge_subscriptions(1, ["NFO|124"])
    assert state.snapshot()["subscription"] == "ACKNOWLEDGED"


def test_transport_close_clears_generation_scoped_subscription_and_market_evidence():
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    state.request_subscriptions(1, ["NFO|123"])
    state.acknowledge_subscriptions(1, ["NFO|123"])
    state.record_market_event(
        1, canonical_identity_valid=True, exchange_timestamp_valid=True, fresh=True
    )
    assert state.transport_closed(1)
    snapshot = state.snapshot()
    assert snapshot["subscription"] == "NOT_REQUESTED"
    assert snapshot["acknowledged_token_count"] == 0
    assert snapshot["canonical_identity_valid"] is False
    assert snapshot["exchange_timestamp_valid"] is False


def test_subscription_tokens_must_be_explicit_strings():
    state = _open()
    with pytest.raises(TypeError):
        state.request_subscriptions(1, "NFO|123")
    with pytest.raises(TypeError):
        state.request_subscriptions(1, [123])



def test_market_event_expires_by_monotonic_age_without_another_callback():
    state = _open()
    state.record_authentication_ack(1, accepted=True, evidence_kind="shoonya_ak_ack")
    state.request_subscriptions(1, ["NFO|123"])
    state.acknowledge_subscriptions(1, ["NFO|123"])
    assert state.record_market_event(
        1, canonical_identity_valid=True, exchange_timestamp_valid=True,
        fresh=True, received_monotonic_ns=1_000_000_000,
    )
    assert state.snapshot(now_monotonic_ns=1_000_000_001)["feed_ready"] is True
    expired = state.snapshot(now_monotonic_ns=6_000_000_001)
    assert expired["market_event_fresh"] is False
    assert expired["market_event_age_ns"] == 5_000_000_001
    assert expired["feed_ready"] is False


def test_authentication_timeout_is_terminal_for_generation():
    state = _open()
    assert state.authentication_timeout(1)
    assert not state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.snapshot()["authentication"] == "TIMED_OUT"


def test_rejected_authentication_cannot_be_overwritten_by_late_success():
    state = _open()
    assert not state.record_authentication_ack(
        1, accepted=False, evidence_kind="shoonya_ak_ack"
    )
    assert not state.record_authentication_ack(
        1, accepted=True, evidence_kind="shoonya_ak_ack"
    )
    assert state.snapshot()["authentication"] == "REJECTED"
