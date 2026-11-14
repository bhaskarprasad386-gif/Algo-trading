"""Fail-closed provider session/readiness state machine.

This is an isolated evidence reducer. It does not own transports, infer broker
acknowledgements, select providers, or route data to scanners or orders.
"""
from __future__ import annotations

from threading import RLock
from time import monotonic_ns
from typing import Iterable


class ProviderReadiness:
    """Track one broker's evidence for one monotonically increasing generation."""

    PROVIDERS = frozenset({"angel_one", "shoonya"})
    AUTH_ACK_KIND = "shoonya_ak_ack"

    def __init__(self, provider: str, *, subscription_ack_supported: bool) -> None:
        if provider not in self.PROVIDERS:
            raise ValueError("unsupported provider")
        if type(subscription_ack_supported) is not bool:
            raise TypeError("subscription_ack_supported must be bool")
        self.provider = provider
        self._subscription_ack_supported = subscription_ack_supported
        self._lock = RLock()
        self._generation = 0
        self._transport = "CLOSED"
        self._auth = "NOT_VERIFIABLE" if provider == "angel_one" else "UNKNOWN"
        self._auth_evidence: str | None = None
        self._auth_generation: int | None = None
        self._desired_tokens: frozenset[str] = frozenset()
        self._requested = False
        self._acknowledged_tokens: frozenset[str] = frozenset()
        self._market_event_fresh = False
        self._market_identity_valid = False
        self._market_timestamp_valid = False
        self._last_market_event_generation: int | None = None
        self._market_event_received_monotonic_ns: int | None = None
        self._max_market_age_ns = 5_000_000_000
        self._last_liveness = "NONE"
        self._integrity_fault: str | None = None
        self._counters = {
            "ignored_stale_generation": 0,
            "heartbeat_events": 0,
            "market_events_rejected": 0,
        }

    def begin_generation(self, generation: int) -> None:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("generation must be a positive integer")
        with self._lock:
            if generation <= self._generation:
                raise ValueError("generation must strictly increase")
            self._generation = generation
            self._transport = "OPENING"
            self._auth = "NOT_VERIFIABLE" if self.provider == "angel_one" else "UNKNOWN"
            self._auth_evidence = None
            self._auth_generation = None
            self._desired_tokens = frozenset()
            self._requested = False
            self._acknowledged_tokens = frozenset()
            self._market_event_fresh = False
            self._market_identity_valid = False
            self._market_timestamp_valid = False
            self._last_market_event_generation = None
            self._market_event_received_monotonic_ns = None
            self._last_liveness = "NONE"
            self._integrity_fault = None

    def transport_open(self, generation: int) -> bool:
        with self._lock:
            if not self._current(generation):
                return False
            self._transport = "OPEN"
            return True

    def transport_closed(self, generation: int, *, reason: str = "closed") -> bool:
        with self._lock:
            if not self._current(generation):
                return False
            self._transport = "CLOSED"
            if self.provider == "shoonya" and self._auth == "AUTHENTICATED":
                self._auth = "UNKNOWN"
            self._auth_evidence = None
            self._auth_generation = None
            self._market_event_fresh = False
            self._market_identity_valid = False
            self._market_timestamp_valid = False
            self._last_market_event_generation = None
            self._last_liveness = "NONE"
            self._requested = False
            self._acknowledged_tokens = frozenset()
            return True

    def record_authentication_ack(
        self, generation: int, *, accepted: bool, evidence_kind: str
    ) -> bool:
        if type(accepted) is not bool or not isinstance(evidence_kind, str):
            raise TypeError("authentication evidence must be explicitly typed")
        with self._lock:
            if not self._current(generation) or self._transport != "OPEN":
                self._counters["ignored_stale_generation"] += 1
                return False
            if self._auth != "UNKNOWN":
                return False
            # SmartWebSocketV2 has no verified broker-auth ACK in this contract.
            if self.provider != "shoonya" or evidence_kind != self.AUTH_ACK_KIND:
                self._auth = "NOT_VERIFIABLE" if self.provider == "angel_one" else "UNKNOWN"
                self._auth_evidence = None
                self._auth_generation = None
                return False
            self._auth = "AUTHENTICATED" if accepted else "REJECTED"
            self._auth_evidence = evidence_kind
            self._auth_generation = generation
            if not accepted:
                self._market_event_fresh = False
            return accepted

    def authentication_timeout(self, generation: int) -> bool:
        with self._lock:
            if not self._current(generation) or self._auth != "UNKNOWN":
                return False
            if self.provider == "shoonya":
                self._auth = "TIMED_OUT"
            return True

    def request_subscriptions(self, generation: int, tokens: Iterable[str]) -> bool:
        if isinstance(tokens, (str, bytes)):
            raise TypeError("tokens must be an iterable of token strings, not a string")
        values = tuple(tokens)
        if any(not isinstance(token, str) for token in values):
            raise TypeError("subscription tokens must be strings")
        normalized = frozenset(token.strip() for token in values)
        if not normalized or any(not token for token in normalized):
            raise ValueError("subscription tokens must be non-empty")
        with self._lock:
            if not self._current(generation) or self._transport != "OPEN":
                return False
            self._desired_tokens = normalized
            self._requested = True
            self._acknowledged_tokens = frozenset()
            return True

    def acknowledge_subscriptions(self, generation: int, tokens: Iterable[str]) -> bool:
        if isinstance(tokens, (str, bytes)):
            raise TypeError("tokens must be an iterable of token strings, not a string")
        values = tuple(tokens)
        if any(not isinstance(token, str) for token in values):
            raise TypeError("acknowledged tokens must be strings")
        normalized = frozenset(token.strip() for token in values)
        if not normalized or any(not token for token in normalized):
            raise ValueError("acknowledged tokens must be non-empty")
        with self._lock:
            if (not self._current(generation) or not self._requested
                    or not self._subscription_ack_supported):
                return False
            if not normalized.issubset(self._desired_tokens):
                return False
            self._acknowledged_tokens = self._acknowledged_tokens.union(normalized)
            return True

    def record_heartbeat(self, generation: int) -> bool:
        with self._lock:
            if not self._current(generation) or self._transport != "OPEN":
                return False
            self._last_liveness = "HEARTBEAT"
            self._counters["heartbeat_events"] += 1
            # A heartbeat is never a market-data event.
            return True

    def record_market_event(
        self, generation: int, *, canonical_identity_valid: bool,
        exchange_timestamp_valid: bool, fresh: bool,
        received_monotonic_ns: int | None = None,
    ) -> bool:
        if any(type(value) is not bool for value in (
            canonical_identity_valid, exchange_timestamp_valid, fresh
        )):
            raise TypeError("market event validity flags must be bool")
        received_at = monotonic_ns() if received_monotonic_ns is None else received_monotonic_ns
        if isinstance(received_at, bool) or not isinstance(received_at, int) or received_at <= 0:
            raise ValueError("received_monotonic_ns must be a positive integer")
        with self._lock:
            if not self._current(generation) or self._transport != "OPEN":
                self._counters["market_events_rejected"] += 1
                return False
            self._last_liveness = "MARKET_EVENT"
            self._market_identity_valid = canonical_identity_valid
            self._market_timestamp_valid = exchange_timestamp_valid
            self._market_event_fresh = fresh
            self._last_market_event_generation = generation
            self._market_event_received_monotonic_ns = received_at
            if not (canonical_identity_valid and exchange_timestamp_valid and fresh):
                self._counters["market_events_rejected"] += 1
                return False
            return True

    def latch_integrity_fault(self, generation: int, reason: str) -> bool:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("integrity fault reason is required")
        with self._lock:
            if not self._current(generation):
                return False
            self._integrity_fault = reason.strip()[:160]
            self._market_event_fresh = False
            return True

    def _current(self, generation: int) -> bool:
        if isinstance(generation, bool) or not isinstance(generation, int):
            return False
        if generation != self._generation:
            self._counters["ignored_stale_generation"] += 1
            return False
        return True

    def snapshot(self, *, now_monotonic_ns: int | None = None) -> dict[str, object]:
        now = monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        if isinstance(now, bool) or not isinstance(now, int) or now <= 0:
            raise ValueError("now_monotonic_ns must be a positive integer")
        with self._lock:
            if not self._subscription_ack_supported:
                subscription_state = "NOT_VERIFIABLE"
            elif not self._requested:
                subscription_state = "NOT_REQUESTED"
            elif self._desired_tokens.issubset(self._acknowledged_tokens):
                subscription_state = "ACKNOWLEDGED"
            else:
                subscription_state = "REQUEST_SENT"
            event_age_ns = (
                max(0, now - self._market_event_received_monotonic_ns)
                if self._market_event_received_monotonic_ns is not None
                and now >= self._market_event_received_monotonic_ns else None
            )
            event_fresh = bool(
                self._market_event_fresh
                and event_age_ns is not None
                and event_age_ns <= self._max_market_age_ns
            )
            authenticated = (
                self.provider == "shoonya"
                and self._auth == "AUTHENTICATED"
                and self._auth_generation == self._generation
            )
            feed_ready = bool(
                self._transport == "OPEN"
                and authenticated
                and subscription_state == "ACKNOWLEDGED"
                and event_fresh
                and self._market_identity_valid
                and self._market_timestamp_valid
                and self._last_market_event_generation == self._generation
                and self._integrity_fault is None
            )
            return {
                "provider": self.provider,
                "generation": self._generation,
                "transport": self._transport,
                "authentication": self._auth,
                "authenticated": authenticated,
                "authentication_evidence": self._auth_evidence,
                "authentication_evidence_generation": self._auth_generation,
                "subscription": subscription_state,
                "desired_token_count": len(self._desired_tokens),
                "acknowledged_token_count": len(self._acknowledged_tokens),
                "last_liveness": self._last_liveness,
                "market_event_fresh": event_fresh,
                "market_event_age_ns": event_age_ns,
                "max_market_age_ns": self._max_market_age_ns,
                "canonical_identity_valid": self._market_identity_valid,
                "exchange_timestamp_valid": self._market_timestamp_valid,
                "integrity_fault": self._integrity_fault,
                "feed_ready": feed_ready,
                "parity_eligible": feed_ready,
                "order_eligible": False,
                **self._counters,
            }
