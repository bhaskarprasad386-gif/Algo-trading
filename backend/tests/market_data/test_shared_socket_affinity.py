import pytest

from app.market_data.shared_socket_affinity import (
    AffinitySlot, ConsumerSocketAffinity,
)

NIFTY = ConsumerSocketAffinity.LOCKED_OWNERS["nifty"]
BANK = ConsumerSocketAffinity.LOCKED_OWNERS["banknifty"]
FUTURES = ConsumerSocketAffinity.LOCKED_OWNERS["futures"]

def tokens(start, count, exchange=2):
    return {(exchange, str(start + i)) for i in range(count)}

def test_locked_three_owner_plan_assigns_stable_distinct_shards():
    planner = ConsumerSocketAffinity()
    slots = planner.locked_slots(mode=3)
    plan = planner.plan(slots, {NIFTY: tokens(100, 2), BANK: tokens(200, 2), FUTURES: tokens(300, 48)})
    assert slots[NIFTY] == AffinitySlot(3, 0)
    assert slots[BANK] == AffinitySlot(3, 1)
    assert slots[FUTURES] == AffinitySlot(3, 2)
    planner.commit(plan)
    assert planner.resolve(NIFTY) == AffinitySlot(3, 0)
    assert planner.resolve(BANK) == AffinitySlot(3, 1)
    assert planner.resolve(FUTURES) == AffinitySlot(3, 2)
    assert len(planner.snapshot()["socket_groups"]) == 3

def test_unclaimed_consumers_remain_unassigned_for_adaptive_manager():
    planner = ConsumerSocketAffinity()
    assert planner.resolve("box-spread") is None
    assert planner.resolve("iv-scanner") is None
    assert planner.snapshot()["owners"] == {}

def test_plan_rejects_more_than_three_groups_without_mutating_current_plan():
    planner = ConsumerSocketAffinity()
    original = planner.plan({NIFTY: AffinitySlot(3, 0)}, {NIFTY: tokens(1, 1)})
    planner.commit(original)
    candidate = planner.plan({
        NIFTY: AffinitySlot(3, 0), BANK: AffinitySlot(3, 1),
        FUTURES: AffinitySlot(3, 2),
    }, {NIFTY: tokens(1, 1), BANK: tokens(2, 1), FUTURES: tokens(3, 1)})
    with pytest.raises(ValueError, match="global socket limit"):
        planner.plan(candidate.owner_slots | {"other": AffinitySlot(2, 0)},
            candidate.owner_tokens | {"other": tokens(4, 1)})
    assert planner.resolve(NIFTY) == AffinitySlot(3, 0)

def test_capacity_overflow_rejected_before_commit():
    planner = ConsumerSocketAffinity(max_tokens_per_socket=2)
    with pytest.raises(ValueError, match="capacity"):
        planner.plan({NIFTY: AffinitySlot(3, 0)}, {NIFTY: tokens(10, 3)})
    assert planner.resolve(NIFTY) is None

def test_same_shard_owners_share_capacity_deduplicated_tokens():
    planner = ConsumerSocketAffinity(max_tokens_per_socket=3)
    plan = planner.plan({NIFTY: AffinitySlot(3, 0), "observer": AffinitySlot(3, 0)},
        {NIFTY: tokens(1, 2), "observer": tokens(2, 2)})
    planner.commit(plan)
    assert planner.snapshot()["socket_groups"] == [(3, 0)]

def test_duplicate_token_values_are_deduplicated_but_invalid_tokens_fail_closed():
    planner = ConsumerSocketAffinity()
    plan = planner.plan({NIFTY: AffinitySlot(3, 0)}, {NIFTY: {(2, "abc"), (2, "abc")}})
    assert len(plan.owner_tokens[NIFTY]) == 1
    with pytest.raises(ValueError, match="nonempty token"):
        planner.plan({NIFTY: AffinitySlot(3, 0)}, {NIFTY: {(2, " ")}})

def test_release_is_idempotent_and_only_releases_named_owner():
    planner = ConsumerSocketAffinity()
    plan = planner.plan({NIFTY: AffinitySlot(3, 0), BANK: AffinitySlot(3, 1)},
        {NIFTY: tokens(1, 1), BANK: tokens(2, 1)})
    planner.commit(plan)
    assert planner.release(NIFTY)
    assert not planner.release(NIFTY)
    assert planner.resolve(NIFTY) is None
    assert planner.resolve(BANK) == AffinitySlot(3, 1)

def test_affinity_slot_validates_mode_and_shard():
    with pytest.raises(ValueError):
        AffinitySlot(5, 0)
    with pytest.raises(ValueError):
        AffinitySlot(3, -1)


def test_plan_rejects_shard_index_outside_configured_socket_range():
    planner = ConsumerSocketAffinity(max_sockets=3)
    with pytest.raises(ValueError, match="shard is outside configured socket range"):
        planner.plan({NIFTY: AffinitySlot(3, 3)}, {NIFTY: tokens(1, 1)})
    assert planner.snapshot()["owners"] == {}


def test_commit_revalidates_mutated_plan_before_registry_update():
    planner = ConsumerSocketAffinity(max_sockets=3)
    good = planner.plan({NIFTY: AffinitySlot(3, 0)}, {NIFTY: tokens(1, 1)})
    planner.commit(good)
    forged = planner.plan({BANK: AffinitySlot(3, 1)}, {BANK: tokens(2, 1)})
    forged.owner_slots[BANK] = AffinitySlot(3, 9)
    with pytest.raises(ValueError, match="shard is outside configured socket range"):
        planner.commit(forged)
    assert planner.resolve(NIFTY) == AffinitySlot(3, 0)
    assert planner.resolve(BANK) is None


def test_plan_rejects_owner_names_that_collide_after_normalization():
    planner = ConsumerSocketAffinity()
    with pytest.raises(ValueError, match="duplicate normalized affinity owner"):
        planner.plan({"owner": AffinitySlot(3, 0), " owner ": AffinitySlot(3, 1)},
            {"owner": tokens(1, 1), " owner ": tokens(2, 1)})

@pytest.mark.parametrize("exchange", [True, False, 0, -2, "2"])
def test_plan_rejects_non_integer_or_non_positive_exchange_type(exchange):
    planner = ConsumerSocketAffinity()
    with pytest.raises(ValueError, match="positive integer exchange type"):
        planner.plan({NIFTY: AffinitySlot(3, 0)}, {NIFTY: {(exchange, "tok")}})
    assert planner.snapshot()["owners"] == {}
