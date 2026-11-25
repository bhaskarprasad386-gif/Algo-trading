"""Explicit lane affinity policy, isolated from the live feed manager.

This batch adds a deterministic planning primitive only. It does not apply socket
assignments or change the shared manager's existing shard behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Iterable

from .contracts import InstrumentKey
from .registry import InstrumentDescriptor

@dataclass(frozen=True, order=True)
class AffinitySlot:
    mode: int
    shard: int

    def __post_init__(self):
        if isinstance(self.mode, bool) or not isinstance(self.mode, int) or self.mode not in (1, 2, 3, 4):
            raise ValueError("mode must be 1, 2, 3, or 4")
        if isinstance(self.shard, bool) or not isinstance(self.shard, int) or self.shard < 0:
            raise ValueError("shard must be a non-negative integer")

@dataclass(frozen=True)
class AffinityPlan:
    owner_slots: dict[str, AffinitySlot]
    owner_tokens: dict[str, frozenset[tuple[int, str]]]

@dataclass(frozen=True)
class InstrumentAffinityPlan:
    """Instrument-level placement; consumer ownership is deliberately separate."""

    assignments: dict[InstrumentKey, AffinitySlot]
    token_pairs_by_slot: dict[AffinitySlot, frozenset[tuple[int, str]]]
    unassigned_keys: frozenset[InstrumentKey]

class ConsumerSocketAffinity:
    """Opt-in lane reservations; unclaimed consumers remain adaptive."""

    LOCKED_OWNERS = {
        "nifty": "shared-index-market-data:nifty",
        "banknifty": "shared-index-market-data:banknifty",
        "futures": "shared-index-market-data:futures",
    }

    def __init__(self, *, max_sockets: int = 3, max_tokens_per_socket: int = 1000):
        if isinstance(max_sockets, bool) or not isinstance(max_sockets, int) or max_sockets < 1:
            raise ValueError("max_sockets must be a positive integer")
        if isinstance(max_tokens_per_socket, bool) or not isinstance(max_tokens_per_socket, int) or max_tokens_per_socket < 1:
            raise ValueError("max_tokens_per_socket must be a positive integer")
        self.max_sockets = max_sockets
        self.max_tokens_per_socket = max_tokens_per_socket
        self._lock = RLock()
        self._owner_slots: dict[str, AffinitySlot] = {}
        self._owner_tokens: dict[str, frozenset[tuple[int, str]]] = {}

    @staticmethod
    def locked_slots(mode: int = 3) -> dict[str, AffinitySlot]:
        """Return the intended three-owner allocation without mutating reservations."""
        return {
            ConsumerSocketAffinity.LOCKED_OWNERS["nifty"]: AffinitySlot(mode, 0),
            ConsumerSocketAffinity.LOCKED_OWNERS["banknifty"]: AffinitySlot(mode, 1),
            ConsumerSocketAffinity.LOCKED_OWNERS["futures"]: AffinitySlot(mode, 2),
        }


    def plan_instruments(
        self,
        descriptors: Iterable[InstrumentDescriptor],
        *,
        mode: int,
        exchange_type_resolver: Callable[[InstrumentKey], int],
    ) -> InstrumentAffinityPlan:
        """Plan locked lanes by instrument, never by a strategy consumer.

        One consumer may own instruments on multiple lanes, including the
        combined NIFTY-weekly/BANKNIFTY-monthly options subscription. Descriptors
        without a locked preferred lane remain outside this reservation plan.
        """
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in (1, 2, 3, 4):
            raise ValueError("mode must be one of 1, 2, 3 or 4")
        if not callable(exchange_type_resolver):
            raise TypeError("exchange_type_resolver must be callable")

        assignments: dict[InstrumentKey, AffinitySlot] = {}
        pairs_by_slot: dict[AffinitySlot, set[tuple[int, str]]] = {}
        pair_slots: dict[tuple[int, str], AffinitySlot] = {}
        unassigned_keys: set[InstrumentKey] = set()
        descriptor_by_key: dict[InstrumentKey, InstrumentDescriptor] = {}
        lane_by_key: dict[InstrumentKey, int | None] = {}
        for descriptor in tuple(descriptors):
            if not isinstance(descriptor, InstrumentDescriptor):
                raise TypeError("descriptors must contain InstrumentDescriptor values")
            previous_descriptor = descriptor_by_key.get(descriptor.key)
            if previous_descriptor is not None and previous_descriptor != descriptor:
                raise ValueError(f"conflicting descriptors for {descriptor.key.value}")
            descriptor_by_key[descriptor.key] = descriptor
            lane = descriptor.preferred_socket_lane
            if descriptor.key in lane_by_key and lane_by_key[descriptor.key] != lane:
                raise ValueError(f"conflicting lane affinity for {descriptor.key.value}")
            lane_by_key[descriptor.key] = lane
            if lane is None:
                unassigned_keys.add(descriptor.key)
                continue
            slot = AffinitySlot(mode, lane)
            existing = assignments.get(descriptor.key)
            if existing is not None and existing != slot:
                raise ValueError(f"conflicting lane affinity for {descriptor.key.value}")
            exchange_type = exchange_type_resolver(descriptor.key)
            if isinstance(exchange_type, bool) or not isinstance(exchange_type, int) or exchange_type <= 0:
                raise ValueError("exchange type resolver must return a positive integer")
            pair = (exchange_type, str(descriptor.key.token).strip())
            if not pair[1]:
                raise ValueError("instrument token is required")
            previous_slot = pair_slots.get(pair)
            if previous_slot is not None and previous_slot != slot:
                raise ValueError("broker token identity is assigned to conflicting locked lanes")
            pair_slots[pair] = slot
            assignments[descriptor.key] = slot
            pairs_by_slot.setdefault(slot, set()).add(pair)

        if len(pairs_by_slot) > self.max_sockets:
            raise ValueError("instrument affinity plan exceeds global socket limit")
        for slot, pairs in pairs_by_slot.items():
            if len(pairs) > self.max_tokens_per_socket:
                raise ValueError(f"locked lane {slot.shard} exceeds socket token capacity")
        return InstrumentAffinityPlan(
            assignments=dict(assignments),
            token_pairs_by_slot={
                slot: frozenset(pairs) for slot, pairs in pairs_by_slot.items()
            },
            unassigned_keys=frozenset(unassigned_keys),
        )

    def plan(self, owner_slots: dict[str, AffinitySlot],
              owner_tokens: dict[str, Iterable[tuple[int, str]]]) -> AffinityPlan:
        """Validate a candidate map; no state changes occur until commit()."""
        if set(owner_slots) != set(owner_tokens):
            raise ValueError("each affinity owner must have a token set")
        normalized_slots = {}
        normalized_tokens = {}
        for owner, slot in owner_slots.items():
            name = str(owner).strip()
            if not name or not isinstance(slot, AffinitySlot):
                raise ValueError("owner and AffinitySlot are required")
            raw_tokens = owner_tokens[owner]
            tokens_list = []
            for pair in raw_tokens:
                try:
                    exchange, token = pair
                except (TypeError, ValueError):
                    raise ValueError("token pairs require exchange type and token") from None
                if isinstance(exchange, bool) or not isinstance(exchange, int) or exchange <= 0:
                    raise ValueError("token pairs require positive integer exchange type")
                normalized_token = str(token).strip()
                if not normalized_token:
                    raise ValueError("token pairs require positive exchange type and nonempty token")
                tokens_list.append((exchange, normalized_token))
            tokens = frozenset(tokens_list)
            if len(tokens) > self.max_tokens_per_socket:
                raise ValueError(f"owner {name} exceeds socket token capacity")
            if slot.shard >= self.max_sockets:
                raise ValueError(f"owner {name} shard is outside configured socket range")
            if name in normalized_slots:
                raise ValueError(f"duplicate normalized affinity owner: {name}")
            normalized_slots[name] = slot
            normalized_tokens[name] = tokens
        groups = set(normalized_slots.values())
        if len(groups) > self.max_sockets:
            raise ValueError("affinity plan exceeds global socket limit")
        # Tokens assigned to different owners on one socket may be deduplicated;
        # distinct owners on separate sockets intentionally remain separate.
        occupancy: dict[AffinitySlot, set[tuple[int, str]]] = {}
        for owner, slot in normalized_slots.items():
            occupancy.setdefault(slot, set()).update(normalized_tokens[owner])
        if any(len(tokens) > self.max_tokens_per_socket for tokens in occupancy.values()):
            raise ValueError("combined socket shard exceeds token capacity")
        return AffinityPlan(normalized_slots, normalized_tokens)

    def commit(self, plan: AffinityPlan) -> None:
        if not isinstance(plan, AffinityPlan):
            raise TypeError("plan must be AffinityPlan")
        # Revalidate at commit time: frozen plans still contain mutable dicts,
        # so callers may have altered them after the original plan() validation.
        validated = self.plan(plan.owner_slots, plan.owner_tokens)
        with self._lock:
            self._owner_slots = dict(validated.owner_slots)
            self._owner_tokens = dict(validated.owner_tokens)

    def release(self, owner: str) -> bool:
        name = str(owner).strip()
        with self._lock:
            existed = name in self._owner_slots
            self._owner_slots.pop(name, None)
            self._owner_tokens.pop(name, None)
            return existed

    def resolve(self, owner: str) -> AffinitySlot | None:
        with self._lock:
            return self._owner_slots.get(str(owner).strip())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "owners": {name: {"mode": slot.mode, "shard": slot.shard,
                    "token_count": len(self._owner_tokens.get(name, ()))}
                    for name, slot in self._owner_slots.items()},
                "socket_groups": sorted({(slot.mode, slot.shard) for slot in self._owner_slots.values()}),
            }
