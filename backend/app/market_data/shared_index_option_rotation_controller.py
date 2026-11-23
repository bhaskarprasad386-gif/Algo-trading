"""Controller connecting spot-movement trigger to contract selection and safe rotation."""
from __future__ import annotations

from threading import RLock
from typing import Callable, Iterable, Any

from .contracts import MarketDataRecord, InstrumentType
from .registry import InstrumentDescriptor
from .shared_index_option_selection import select_locked_index_options
from .common_strategy_feed import CommonStrategyMarketFeed
from .shared_index_spot_rotation_trigger import SpotRotationTrigger



class SharedIndexOptionRotationController:
    """Run bounded resolver/rotation work after spot thresholds, not every tick."""



    def __init__(self, *, lifecycle, instrument_rows: Callable[[], Iterable[dict]],
                 expiry_for: Callable[[str], str], thresholds: dict[str, float],
                 cooldown_seconds: float = 1.0, clock=None):
        self.lifecycle = lifecycle
        self.instrument_rows = instrument_rows
        self.expiry_for = expiry_for
        kwargs = {"thresholds": thresholds, "cooldown_seconds": cooldown_seconds}
        if clock is not None:
            kwargs["clock"] = clock
        self.trigger = SpotRotationTrigger(**kwargs)
        self._lock = RLock()
        self._last_rotation: dict[str, dict[str, Any]] = {}
        self._rotation_failures = 0
        self._rotation_successes = 0



    def on_record(self, record: MarketDataRecord) -> bool:
        if record.instrument_type is not InstrumentType.INDEX or record.ltp is None:
            return False
        underlying = str(record.underlying or "").strip().upper()
        if underlying not in {"NIFTY", "BANKNIFTY"}:
            return False
        # Serialize trigger bookkeeping with pending rotations and contract replacement.
        with self._lock:
            if not self.trigger.on_spot(underlying, record.ltp):
                return False
            epoch = self.trigger.current_epoch()
            success = self._rotate(underlying, record.ltp)
            self.trigger.rotation_result(underlying, success=success, spot=record.ltp, epoch=epoch)
            return success



    def flush_due(self) -> tuple[str, ...]:
        # Lock the pending-drain and rotation sequence against live spot callbacks.
        with self._lock:
            epoch = self.trigger.current_epoch()
            due = self.trigger.flush_due()
            completed = []
            for underlying in due:
                spot = self.trigger.snapshot()["last_spot"].get(underlying)
                success = spot is not None and self._rotate(underlying, spot)
                self.trigger.rotation_result(underlying, success=success, spot=spot, epoch=epoch)
                if success:
                    completed.append(underlying)
            return tuple(completed)



    def _rotate(self, underlying: str, spot: float) -> bool:
        # Guard direct calls and pending flushes; RLock permits nested entry.
        with self._lock:
            return self._rotate_locked(underlying, spot)

    def _rotate_locked(self, underlying: str, spot: float) -> bool:
        try:
            selection = select_locked_index_options(
                self.instrument_rows(), underlying=underlying,
                expiry=self.expiry_for(underlying), spot_price=spot,
            )
            selected_descriptors = tuple(CommonStrategyMarketFeed.descriptor(
                exchange="NFO", token=contract.token, symbol=contract.trading_symbol,
                instrument_type="option", segment="NFO", expiry=contract.expiry,
                strike=contract.strike, option_type=contract.option_type,
                lot_size=contract.lot_size, underlying=contract.underlying,
            ) for contract in selection.contracts)
            current_descriptors = tuple(
                descriptor for descriptor in self.lifecycle.option_descriptors()
                if descriptor.underlying != underlying
            ) if callable(getattr(self.lifecycle, "option_descriptors", None)) else ()
            descriptors = current_descriptors + selected_descriptors
            # Lifecycle performs subscribe-new-before-remove. If resolver fails,
            # this call is never made and the previous option owner stays intact.
            self.lifecycle.rotate_options(descriptors)
            with self._lock:
                self._rotation_successes += 1
                self._last_rotation[underlying] = {
                    "expiry": selection.expiry, "atm_strike": selection.atm_strike,
                    "contract_count": selection.contract_count, "spot": float(spot),
                }
            return True
        except Exception as exc:
            with self._lock:
                self._rotation_failures += 1
                self._last_rotation[underlying] = {
                    "failed": True, "reason": str(exc)[:240], "spot": float(spot),
                }
            return False



    def snapshot(self) -> dict:
        with self._lock:
            return {"rotation_successes": self._rotation_successes,
                    "rotation_failures": self._rotation_failures,
                    "last_rotation": dict(self._last_rotation),
                    "trigger": self.trigger.snapshot()}



__all__ = ["SharedIndexOptionRotationController"]
