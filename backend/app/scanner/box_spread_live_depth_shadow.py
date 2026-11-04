"""Isolated, in-memory Box Spread market-depth shadow consumer.

This adapter is intentionally opt-in and does not feed the live scanner or
paper execution. It registers its own consumer on the existing shared
WebSocket manager; stopping it clears only that consumer.
"""
from __future__ import annotations

from collections import OrderedDict
from math import isfinite
from threading import RLock
from time import monotonic, time_ns
from typing import Any, Iterable, Mapping
from datetime import datetime

from app.market_data.common_strategy_feed import CommonStrategyMarketFeed
from app.market_data.registry import InstrumentDescriptor


_CONSUMER = "box-live-depth-shadow"
_SIDES = (("BID", "buy"), ("ASK", "sell"))


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number > 0 else None


def _raw_levels(payload: Mapping[str, Any], side: str) -> list[tuple[float, float]]:
    """Read broker depth only; deliberately do not fall back to canonical L1."""
    levels = None
    for key in (f"best_5_{side}_data", f"best_{side}_data", f"best_{side}", side):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            levels = candidate
            break
        if isinstance(candidate, Mapping):
            levels = [candidate]
            break
    parsed: list[tuple[float, float]] = []
    for level in levels or ():
        if not isinstance(level, Mapping):
            continue
        price = _positive_number(level.get("price") or level.get(f"{side}_price"))
        quantity = _positive_number(
            level.get("quantity") or level.get("qty") or level.get(f"{side}_quantity")
        )
        if price is not None and quantity is not None:
            parsed.append((price / 100.0, quantity))
    parsed.sort(key=lambda item: item[0], reverse=(side == "buy"))
    return parsed[:5]


class BoxLiveDepthShadowFeed:
    """Bounded shadow-only depth cache backed by the shared common feed."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        mode: int = 3,
        max_records: int = 512,
        max_arrival_age_seconds: float = 5.0,
        max_provider_age_seconds: float = 5.0,
        feed: Any | None = None,
        consumer: str = _CONSUMER,
    ) -> None:
        if isinstance(max_records, bool) or int(max_records) < 1:
            raise ValueError("max_records must be a positive integer")
        self.enabled = bool(enabled)
        self.max_records = int(max_records)
        self.max_arrival_age_seconds = float(max_arrival_age_seconds)
        self.max_provider_age_seconds = float(max_provider_age_seconds)
        self.consumer = str(consumer or _CONSUMER).strip()
        if not self.consumer:
            raise ValueError("depth feed consumer must be non-empty")
        self._feed = feed or CommonStrategyMarketFeed(self.consumer, mode=mode)
        self._lock = RLock()
        self._records: OrderedDict[Any, dict[str, Any]] = OrderedDict()
        self._metadata: dict[Any, dict[str, Any]] = {}
        self._started = False
        self._counts = {
            "received": 0, "depth_full": 0, "depth_partial": 0,
            "l1_only": 0, "depth_invalid": 0, "invalid_timestamp": 0,
        }

    def start(self, descriptors: Iterable[InstrumentDescriptor]) -> bool:
        """Subscribe only when explicitly enabled; otherwise make no feed changes."""
        if not self.enabled:
            return False
        items = tuple(descriptors)
        if not items:
            raise ValueError("at least one Box Spread option descriptor is required")
        if any(str(item.instrument_type).strip().lower() != "option" for item in items):
            raise ValueError("Box Live Depth Shadow accepts option descriptors only")
        metadata = {}
        for item in items:
            expiry = self._expiry_key(item.expiry)
            metadata[item.key] = {
                "underlying": str(item.underlying or "").strip().upper(),
                "expiry": expiry,
                "strike": float(item.strike) if item.strike is not None else None,
                "option_type": str(item.option_type or "").strip().upper(),
                "lot_size": item.lot_size,
            }
        with self._lock:
            if self._started:
                return True
            self._records.clear()
            self._metadata = metadata
        try:
            self._feed.start(items, self._on_record)
        except Exception:
            # CommonStrategyMarketFeed may have registered the callback before a
            # subscription error; clear only this consumer to avoid a leaked route.
            try:
                self._feed.stop()
            finally:
                with self._lock:
                    self._records.clear()
                    self._metadata.clear()
            raise
        with self._lock:
            self._started = True
        return True

    @staticmethod
    def _expiry_key(value: Any) -> int | None:
        if value is None:
            return None
        text = str(value).strip().upper()
        if text.isdigit():
            try:
                return int(text)
            except ValueError:
                return None
        for fmt in ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return int(datetime.strptime(text, fmt).strftime("%Y%m%d"))
            except ValueError:
                continue
        return None

    @staticmethod
    def _allocate(levels: list[tuple[float, float]], quantity: int) -> float | None:
        remaining = float(quantity)
        notional = 0.0
        filled = 0.0
        for price, available in levels:
            take = min(remaining, available)
            if take > 0:
                notional += take * price
                filled += take
                remaining -= take
            if remaining <= 1e-9:
                break
        if remaining > 1e-9 or filled <= 0:
            return None
        return notional / filled

    def compare_opportunity(
        self,
        opportunity: Any,
        *,
        now_ns: int | None = None,
        now_monotonic: float | None = None,
        max_leg_skew_seconds: float = 2.0,
    ) -> dict[str, Any]:
        """Compare the scanner L1 edge with a complete four-leg depth VWAP edge."""
        wall_now = time_ns() if now_ns is None else int(now_ns)
        arrival_now = monotonic() if now_monotonic is None else float(now_monotonic)
        low, high = opportunity.low, opportunity.high
        direction = str(opportunity.direction).strip().upper()
        if direction not in {"LONG", "SHORT"}:
            return {"status": "INCOMPLETE", "reason": "INVALID_DIRECTION"}
        lot_size = getattr(low, "lot_size", None)
        if isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size <= 0:
            return {"status": "INCOMPLETE", "reason": "INVALID_LOT_SIZE"}
        if getattr(high, "lot_size", None) != lot_size:
            return {"status": "INCOMPLETE", "reason": "LOT_SIZE_MISMATCH"}

        if direction == "LONG":
            legs = (
                ("LOW_CE", low.strike, "CE", "ASK", low.call_timestamp_ns or low.timestamp_ns),
                ("HIGH_CE", high.strike, "CE", "BID", high.call_timestamp_ns or high.timestamp_ns),
                ("HIGH_PE", high.strike, "PE", "ASK", high.put_timestamp_ns or high.timestamp_ns),
                ("LOW_PE", low.strike, "PE", "BID", low.put_timestamp_ns or low.timestamp_ns),
            )
        else:
            legs = (
                ("LOW_CE", low.strike, "CE", "BID", low.call_timestamp_ns or low.timestamp_ns),
                ("HIGH_CE", high.strike, "CE", "ASK", high.call_timestamp_ns or high.timestamp_ns),
                ("HIGH_PE", high.strike, "PE", "BID", high.put_timestamp_ns or high.timestamp_ns),
                ("LOW_PE", low.strike, "PE", "ASK", low.put_timestamp_ns or low.timestamp_ns),
            )

        with self._lock:
            cached = list(self._records.values())
            metadata = dict(self._metadata)
        selected = []
        for role, strike, option_type, side, quote_timestamp in legs:
            candidates = []
            for item in cached:
                meta = metadata.get(item.get("key"), {})
                if (
                    meta.get("underlying") == str(low.underlying).strip().upper()
                    and meta.get("expiry") == int(low.expiry)
                    and meta.get("strike") == float(strike)
                    and meta.get("option_type") == option_type
                ):
                    candidates.append(item)
            if not candidates:
                return {"status": "INCOMPLETE", "reason": f"MISSING_{role}"}
            item = max(candidates, key=lambda row: row["timestamp_ns"])
            provider_age = (wall_now - item["timestamp_ns"]) / 1e9
            arrival_age = arrival_now - item["arrival_monotonic"]
            if not (-1.0 <= provider_age <= self.max_provider_age_seconds):
                return {"status": "INCOMPLETE", "reason": f"STALE_PROVIDER_{role}"}
            if not (0.0 <= arrival_age <= self.max_arrival_age_seconds):
                return {"status": "INCOMPLETE", "reason": f"STALE_ARRIVAL_{role}"}
            if item["depth_status"] == "DEPTH_INVALID":
                return {"status": "INCOMPLETE", "reason": f"INVALID_DEPTH_{role}"}
            if abs(item["timestamp_ns"] - int(quote_timestamp)) > max_leg_skew_seconds * 1e9:
                return {"status": "INCOMPLETE", "reason": f"QUOTE_DEPTH_SKEW_{role}"}
            # Five levels is the maximum depth window, not a minimum.
            # A partial book is usable: size the basket from its actual selected-
            # side liquidity, then cap all four legs to the common whole-lot qty.
            levels = item["asks"] if side == "ASK" else item["bids"]
            if not levels:
                return {"status": "INCOMPLETE", "reason": f"NO_EXECUTABLE_LEVELS_{role}"}
            available_quantity = int(sum(quantity for _, quantity in levels))
            if available_quantity < lot_size:
                return {"status": "INCOMPLETE", "reason": f"INSUFFICIENT_{role}"}
            selected.append({
                "role": role, "side": side, "levels": levels,
                "bid_depth_levels": list(item["bids"]),
                "ask_depth_levels": list(item["asks"]),
                "available_quantity": available_quantity,
                "timestamp_ns": item["timestamp_ns"],
                "depth_status": item["depth_status"],
            })

        # A basket is executable only up to the common whole-lot capacity
        # across all four required sides. Price every leg at that same quantity.
        max_executable_lots = min(
            item["available_quantity"] // lot_size for item in selected
        )
        if max_executable_lots < 1:
            return {"status": "INCOMPLETE", "reason": "INSUFFICIENT_FOUR_LEG_DEPTH"}
        executable_quantity = max_executable_lots * lot_size
        for item in selected:
            # Preserve the full original per-leg capacity before consuming levels
            # for the common executable quantity's VWAP allocation.
            original_capacity = item["available_quantity"]
            levels = item.pop("levels")
            # Persist the exact broker ladder used for this opportunity. The
            # pipeline stores this immutable copy with the snapshot timestamp;
            # historical UI must never reconstruct it from current quotes.
            item["depth_levels"] = [
                {"price": price, "quantity": quantity}
                for price, quantity in levels
            ]
            item["bid_depth_levels"] = [
                {"price": price, "quantity": quantity}
                for price, quantity in item.pop("bid_depth_levels")
            ]
            item["ask_depth_levels"] = [
                {"price": price, "quantity": quantity}
                for price, quantity in item.pop("ask_depth_levels")
            ]
            item["vwap"] = self._allocate(levels, executable_quantity)
            if item["vwap"] is None:
                return {"status": "INCOMPLETE", "reason": f"INSUFFICIENT_{item['role']}"}
            item["available_quantity"] = original_capacity
            item["selected_quantity"] = executable_quantity

        timestamps = [item["timestamp_ns"] for item in selected]
        if max(timestamps) - min(timestamps) > max_leg_skew_seconds * 1e9:
            return {"status": "INCOMPLETE", "reason": "FOUR_LEG_TIMESTAMP_SKEW"}

        prices = {item["role"]: item["vwap"] for item in selected}
        width = float(high.strike) - float(low.strike)
        if direction == "LONG":
            net_debit = prices["LOW_CE"] + prices["HIGH_PE"] - prices["HIGH_CE"] - prices["LOW_PE"]
            depth_edge = width - net_debit
        else:
            net_credit = prices["LOW_CE"] + prices["HIGH_PE"] - prices["HIGH_CE"] - prices["LOW_PE"]
            depth_edge = net_credit - width
        l1_edge = float(opportunity.executable_edge)
        return {
            "status": "COMPLETE",
            "direction": direction,
            "quantity": executable_quantity,
            "max_executable_lots": max_executable_lots,
            "depth_capacity_by_leg": {
                item["role"]: item["available_quantity"] for item in selected
            },
            "l1_edge_per_unit": l1_edge,
            "depth_edge_per_unit": depth_edge,
            "net_entry_price": net_debit if direction == "LONG" else net_credit,
            "difference_per_unit": depth_edge - l1_edge,
            "l1_edge_per_lot": l1_edge * lot_size,
            "depth_edge_per_lot": depth_edge * lot_size,
            "difference_per_lot": (depth_edge - l1_edge) * lot_size,
            "legs": selected,
            "max_timestamp_skew_seconds": round((max(timestamps) - min(timestamps)) / 1e9, 3),
        }

    def _on_record(self, record: Any) -> None:
        instrument = getattr(record, "instrument", None)
        payload = getattr(record, "payload", None)
        if instrument is None or not isinstance(payload, Mapping):
            return
        timestamp_ns = getattr(record, "timestamp_ns", None)
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns <= 0:
            with self._lock:
                self._counts["invalid_timestamp"] += 1
            return

        bids = _raw_levels(payload, "buy")
        asks = _raw_levels(payload, "sell")
        if not bids and not asks:
            state = "L1_ONLY"
        elif len(bids) >= 5 and len(asks) >= 5:
            state = "DEPTH_FULL"
        else:
            state = "DEPTH_PARTIAL"

        # Crossed/invalid best levels are not eligible for shadow comparison.
        if bids and asks and bids[0][0] > asks[0][0]:
            state = "DEPTH_INVALID"

        entry = {
            "key": instrument,
            "instrument": instrument.value,
            "symbol": str(getattr(record, "symbol", "")),
            "timestamp_ns": timestamp_ns,
            "arrival_monotonic": monotonic(),
            "depth_status": state,
            "bids": bids,
            "asks": asks,
        }
        with self._lock:
            self._counts["received"] += 1
            counter_key = state.lower()
            if counter_key in self._counts:
                self._counts[counter_key] += 1
            self._records[instrument] = entry
            self._records.move_to_end(instrument)
            while len(self._records) > self.max_records:
                self._records.popitem(last=False)

    def depth_for_opportunity(self, opportunity: Any) -> dict[str, Any]:
        """Return fresh five-level bid/ask ladders for the opportunity's four contracts."""
        now_ns = time_ns()
        now_arrival = monotonic()
        low, high = opportunity.low, opportunity.high
        roles = (
            ("low_call", low.strike, "CE"),
            ("low_put", low.strike, "PE"),
            ("high_call", high.strike, "CE"),
            ("high_put", high.strike, "PE"),
        )
        output = {}
        with self._lock:
            cached = list(self._records.values())
            metadata = dict(self._metadata)
        for role, strike, option_type in roles:
            matches = [
                item for item in cached
                if (meta := metadata.get(item.get("key"), {})).get("underlying") == str(low.underlying).strip().upper()
                and meta.get("expiry") == int(low.expiry)
                and meta.get("strike") == float(strike)
                and meta.get("option_type") == option_type
            ]
            if not matches:
                output[role] = {"status": "UNAVAILABLE", "bids": [], "asks": []}
                continue
            item = max(matches, key=lambda row: row["timestamp_ns"])
            provider_age = (now_ns - item["timestamp_ns"]) / 1e9
            arrival_age = now_arrival - item["arrival_monotonic"]
            fresh = (
                -1.0 <= provider_age <= self.max_provider_age_seconds
                and 0.0 <= arrival_age <= self.max_arrival_age_seconds
                and item["depth_status"] in {"DEPTH_FULL", "DEPTH_PARTIAL"}
                and bool(item["bids"]) and bool(item["asks"])
            )
            output[role] = {
                "status": "LIVE" if fresh else item["depth_status"],
                "timestamp_ns": item["timestamp_ns"],
                "bids": [{"price": price, "quantity": quantity} for price, quantity in item["bids"]],
                "asks": [{"price": price, "quantity": quantity} for price, quantity in item["asks"]],
            }
        return output

    def snapshot(self, *, now_ns: int | None = None, now_monotonic: float | None = None) -> dict[str, Any]:
        """Return fresh in-memory observations; never persists or changes scanner data."""
        wall_now = time_ns() if now_ns is None else int(now_ns)
        arrival_now = monotonic() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            rows = []
            for item in self._records.values():
                provider_age = (wall_now - item["timestamp_ns"]) / 1e9
                arrival_age = arrival_now - item["arrival_monotonic"]
                fresh = (
                    -1.0 <= provider_age <= self.max_provider_age_seconds
                    and 0.0 <= arrival_age <= self.max_arrival_age_seconds
                )
                rows.append({
                    "instrument": item["instrument"],
                    "symbol": item["symbol"],
                    "timestamp_ns": item["timestamp_ns"],
                    "provider_age_seconds": round(provider_age, 3),
                    "arrival_age_seconds": round(arrival_age, 3),
                    "freshness": "FRESH" if fresh else "STALE",
                    "depth_status": item["depth_status"],
                    "bids": list(item["bids"]),
                    "asks": list(item["asks"]),
                })
            records_by_key = {item["key"]: item for item in self._records.values()}
            readiness_by_group: dict[tuple[str, int | None], dict[str, Any]] = {}
            for key, meta in self._metadata.items():
                underlying = str(meta.get("underlying") or "").strip().upper()
                expiry = meta.get("expiry")
                if not underlying:
                    continue
                group = (underlying, expiry)
                summary = readiness_by_group.setdefault(group, {
                    "underlying": underlying,
                    "expiry": expiry,
                    "subscribed_contracts": 0,
                    "fresh_contracts": 0,
                    "fresh_ce": 0,
                    "fresh_pe": 0,
                    "executable_depth_contracts": 0,
                    "executable_depth_ce": 0,
                    "executable_depth_pe": 0,
                })
                summary["subscribed_contracts"] += 1
                item = records_by_key.get(key)
                if item is None:
                    continue
                provider_age = (wall_now - item["timestamp_ns"]) / 1e9
                arrival_age = arrival_now - item["arrival_monotonic"]
                fresh = (
                    -1.0 <= provider_age <= self.max_provider_age_seconds
                    and 0.0 <= arrival_age <= self.max_arrival_age_seconds
                )
                if not fresh:
                    continue
                summary["fresh_contracts"] += 1
                option_type = str(meta.get("option_type") or "").strip().upper()
                if option_type == "CE":
                    summary["fresh_ce"] += 1
                elif option_type == "PE":
                    summary["fresh_pe"] += 1
                full_depth = (
                    item["depth_status"] == "DEPTH_FULL"
                    and len(item["bids"]) >= 5
                    and len(item["asks"]) >= 5
                )
                if full_depth:
                    if option_type == "CE":
                        summary["full_depth_ce"] = summary.get("full_depth_ce", 0) + 1
                    elif option_type == "PE":
                        summary["full_depth_pe"] = summary.get("full_depth_pe", 0) + 1

                # Five levels are a maximum depth window, not a minimum. A
                # partial book qualifies for staging when both sides carry at
                # least two lots of actual quantity, matching the execution
                # sizing floor used by the four-leg depth comparison.
                try:
                    minimum_two_lots = int(meta.get("lot_size")) * 2
                except (TypeError, ValueError, OverflowError):
                    minimum_two_lots = 0
                usable_depth = (
                    item["depth_status"] in {"DEPTH_FULL", "DEPTH_PARTIAL"}
                    and minimum_two_lots > 0
                    and bool(item["bids"])
                    and bool(item["asks"])
                    and sum(quantity for _price, quantity in item["bids"]) >= minimum_two_lots
                    and sum(quantity for _price, quantity in item["asks"]) >= minimum_two_lots
                )
                if usable_depth:
                    summary["executable_depth_contracts"] += 1
                    if option_type == "CE":
                        summary["executable_depth_ce"] += 1
                    elif option_type == "PE":
                        summary["executable_depth_pe"] += 1
            readiness = []
            for summary in readiness_by_group.values():
                summary.setdefault("full_depth_ce", 0)
                summary.setdefault("full_depth_pe", 0)
                summary["state"] = (
                    "READY"
                    if summary["fresh_ce"] >= 2
                    and summary["fresh_pe"] >= 2
                    and summary["executable_depth_ce"] >= 2
                    and summary["executable_depth_pe"] >= 2
                    and summary["executable_depth_contracts"] >= 4
                    else "WAITING_FOR_DEPTH"
                )
                readiness.append(summary)
            return {
                "enabled": self.enabled,
                "running": self._started,
                "consumer": str(getattr(self._feed, "consumer", self.consumer)),
                "record_count": len(rows),
                "counts": dict(self._counts),
                "readiness": sorted(readiness, key=lambda item: (item["underlying"], item["expiry"] or 0)),
                "records": rows,
            }

    def stop(self) -> None:
        """Detach only this Box Spread consumer; never stop the shared socket."""
        with self._lock:
            started = self._started
            self._started = False
        if started:
            self._feed.stop()
        with self._lock:
            self._records.clear()
            self._metadata.clear()

    def close(self) -> None:
        self.stop()


__all__ = ["BoxLiveDepthShadowFeed"]
