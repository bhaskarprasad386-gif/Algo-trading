import pytest

from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor, InstrumentRegistry
from app.market_data.shared_socket_affinity import AffinitySlot, ConsumerSocketAffinity


def _descriptor(token: str) -> InstrumentDescriptor:
    return InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", token),
        symbol=f"NIFTY{token}",
        instrument_type="future",
        exchange="NFO",
        segment="NFO",
        underlying="NIFTY",
        expiry="2026-12-31",
    )


@pytest.mark.parametrize("mode", [True, False, 3.0, "3", None])
def test_affinity_slot_rejects_non_integer_modes(mode):
    with pytest.raises(ValueError, match="mode must be"):
        AffinitySlot(mode, 0)


def test_global_socket_budget_counts_mode_and_shard_as_physical_identity():
    planner = ConsumerSocketAffinity(max_sockets=3)
    valid = planner.plan(
        {
            "nifty": AffinitySlot(3, 0),
            "banknifty": AffinitySlot(3, 1),
            "futures": AffinitySlot(3, 2),
        },
        {
            "nifty": {(1, "NIFTY")},
            "banknifty": {(1, "BANKNIFTY")},
            "futures": {(2, "NIFTY-F"), (2, "BANK-F")},
        },
    )
    planner.commit(valid)

    with pytest.raises(ValueError, match="global socket limit"):
        planner.plan(
            {
                "nifty": AffinitySlot(3, 0),
                "banknifty": AffinitySlot(3, 1),
                "futures": AffinitySlot(3, 2),
                "other-mode": AffinitySlot(2, 0),
            },
            {
                "nifty": {(1, "NIFTY")},
                "banknifty": {(1, "BANKNIFTY")},
                "futures": {(2, "NIFTY-F"), (2, "BANK-F")},
                "other-mode": {(1, "OTHER")},
            },
        )

    assert planner.snapshot()["socket_groups"] == [(3, 0), (3, 1), (3, 2)]
    assert planner.resolve("nifty") == AffinitySlot(3, 0)


def test_same_shard_number_in_different_modes_consumes_two_global_sockets():
    planner = ConsumerSocketAffinity(max_sockets=1)
    with pytest.raises(ValueError, match="global socket limit"):
        planner.plan(
            {"mode-three": AffinitySlot(3, 0), "mode-two": AffinitySlot(2, 0)},
            {"mode-three": {(1, "A")}, "mode-two": {(1, "B")}},
        )
    assert planner.snapshot()["owners"] == {}


def test_same_mode_and_shard_deduplicates_shared_consumer_token_occupancy():
    planner = ConsumerSocketAffinity(max_sockets=1, max_tokens_per_socket=2)
    plan = planner.plan(
        {"spot-owner": AffinitySlot(3, 0), "observer": AffinitySlot(3, 0)},
        {
            "spot-owner": {(1, "NIFTY"), (1, "BANKNIFTY")},
            "observer": {(1, "NIFTY")},
        },
    )
    planner.commit(plan)
    assert planner.snapshot()["socket_groups"] == [(3, 0)]
    assert planner.snapshot()["owners"]["spot-owner"]["token_count"] == 2


def test_registry_preserves_all_consumers_while_selecting_highest_shared_mode():
    registry = InstrumentRegistry()
    descriptor = _descriptor("NIFTY-F")
    registry.register(descriptor)

    registry.subscribe("shared-index-market-data:futures", descriptor.key, mode=3)
    shared = registry.subscribe("iv-skew-flip:futures", descriptor.key, mode=1)
    assert shared.consumers == frozenset({
        "shared-index-market-data:futures",
        "iv-skew-flip:futures",
    })
    assert shared.mode == 3

    downgraded = registry.unsubscribe("shared-index-market-data:futures", descriptor.key)
    assert downgraded is not None
    assert downgraded.consumers == frozenset({"iv-skew-flip:futures"})
    assert downgraded.mode == 1


def test_registry_register_many_conflict_is_atomic():
    registry = InstrumentRegistry()
    existing = _descriptor("EXISTING")
    registry.register(existing)
    conflicting = InstrumentDescriptor(
        **{**existing.__dict__, "symbol": "CONFLICTING"},
    )
    new = _descriptor("NEW")

    with pytest.raises(ValueError, match="conflicting descriptor"):
        registry.register_many((new, conflicting))

    assert registry.get(new.key) is None
    assert registry.get(existing.key) == existing
    assert registry.active_keys() == ()
