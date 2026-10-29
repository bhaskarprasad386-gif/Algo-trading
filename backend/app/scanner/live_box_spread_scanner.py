"""Bounded one-second Box Spread quote assembler and scanner."""
from __future__ import annotations
from threading import Lock
from datetime import datetime
from collections import Counter
from app.backtesting.arbitrage_backtester import OptionQuote
from app.core.config import settings
from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot

class LiveBoxSpreadScanner:
    def __init__(self, *, atm_provider, config_provider=None, on_result=None, policy=None):
        self.atm_provider=atm_provider
        self.config_provider=config_provider or (lambda _s: BoxSpreadScanConfig(min_executable_edge=float(settings.LIVE_BOX_SPREAD_MIN_ARBITRAGE_POINTS)))
        self.on_result=on_result
        self.policy=policy
        self._lock=Lock(); self._buckets={}
        self._diagnostic_counts=Counter()
        self._last_diagnostics={}

    @staticmethod
    def _price(v):
        from math import isfinite
        try:
            n = float(v)
            return n if isfinite(n) and n > 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _expiry(v):
        text=str(v or "").strip().upper()
        for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d","%d-%m-%Y"):
            try:return int(datetime.strptime(text,fmt).strftime("%Y%m%d"))
            except ValueError:pass
        raise ValueError(f"invalid option expiry: {v!r}")

    def diagnostics_snapshot(self):
        """Return bounded counters and the last scan outcome; never includes raw quote payloads."""
        with self._lock:
            return {
                "counts": dict(self._diagnostic_counts),
                "last_scan": dict(self._last_diagnostics),
                "active_buckets": len(self._buckets),
            }

    def observe(self,payload):
        symbol=str(payload.get("underlying") or "").strip().upper()
        cls=str(payload.get("instrument_class") or "").strip().upper()
        try:
            ts = int(payload.get("source_timestamp_ns") or 0)
        except (TypeError, ValueError, OverflowError):
            with self._lock:
                self._diagnostic_counts["invalid_identity_or_timestamp"] += 1
            return ()
        import time
        if ts >= 1_000_000_000_000_000:
            age_ns = time.time_ns() - ts
            if age_ns > 5_000_000_000:
                with self._lock:
                    self._diagnostic_counts["stale_timestamp"] += 1
                return ()
            if age_ns < -1_000_000_000:
                with self._lock:
                    self._diagnostic_counts["future_timestamp"] += 1
                return ()
        typ=str(payload.get("option_type") or "").strip().upper()
        strike=self._price(payload.get("strike"))
        bid=self._price(payload.get("bid")); ask=self._price(payload.get("ask"))
        if not symbol or cls not in {"STOCK","INDEX"} or ts<=0 or typ not in {"CE","PE"} or strike is None:
            with self._lock:
                self._diagnostic_counts["invalid_identity_or_timestamp"] += 1
            return ()
        if bid is None or ask is None:
            with self._lock:
                for side, value, raw in (("bid", bid, payload.get("bid")), ("ask", ask, payload.get("ask"))):
                    if value is None:
                        key = "missing_" + side + "_quote" if raw is None or str(raw).strip() == "" else "invalid_" + side + "_quote"
                        self._diagnostic_counts[key] += 1
            return ()
        if ask < bid:
            with self._lock:
                self._diagnostic_counts["crossed_market_quote"] += 1
            return ()
        with self._lock:
            try:
                expiry = self._expiry(payload.get("expiry"))
            except (TypeError, ValueError):
                self._diagnostic_counts["invalid_expiry"] += 1
                return ()
            self._diagnostic_counts["ticks_accepted_for_assembly"] += 1
            key=self._matching_bucket_key(symbol,ts,expiry)
            bucket=self._buckets.setdefault(key,{})
            leg_key=(strike,typ)
            previous=bucket.get(leg_key)
            if previous is not None:
                try: previous_ts=int(previous.get("source_timestamp_ns") or 0)
                except (TypeError,ValueError): previous_ts=0
                if previous_ts > ts:
                    self._diagnostic_counts["out_of_order_ticks"] += 1
                    self._prune(ts)
                    return ()
            bucket[leg_key]=payload
            grouped=[]
            missing_or_non_executable_pairs=0
            for s in sorted({k[0] for k in bucket}):
                ce,pe=bucket.get((s,"CE")),bucket.get((s,"PE"))
                if ce is None or pe is None:
                    missing_or_non_executable_pairs += 1
                    continue
                try:
                    ce_expiry=self._expiry(ce.get("expiry")); pe_expiry=self._expiry(pe.get("expiry"))
                    ce_lot=int(float(ce.get("lot_size") or 0)); pe_lot=int(float(pe.get("lot_size") or 0))
                    if ce_expiry != pe_expiry or ce_lot <= 0 or ce_lot != pe_lot:
                        self._diagnostic_counts["expiry_or_lot_mismatch"] += 1
                        continue
                    ce_bid,ce_ask=self._price(ce.get("bid")),self._price(ce.get("ask"))
                    pe_bid,pe_ask=self._price(pe.get("bid")),self._price(pe.get("ask"))
                    if ce_bid is None or ce_ask is None or ce_ask < ce_bid or pe_bid is None or pe_ask is None or pe_ask < pe_bid:
                        missing_or_non_executable_pairs += 1
                        continue
                    ce_ts=int(ce.get("source_timestamp_ns") or 0); pe_ts=int(pe.get("source_timestamp_ns") or 0)
                    grouped.append(OptionQuote(
                        max(ce_ts,pe_ts),symbol,ce_expiry,s,ce_bid,ce_ask,pe_bid,pe_ask,ce_lot,cls,
                        min(int(float(ce.get("volume") or 0)),int(float(pe.get("volume") or 0))),
                        min(int(float(ce.get("oi") or 0)),int(float(pe.get("oi") or 0))),
                        self._price(ce.get("ltp")),self._price(pe.get("ltp")),ce_ts,pe_ts,
                    ))
                except (ValueError,TypeError):
                    self._diagnostic_counts["malformed_leg_pair"] += 1
                    continue
            if missing_or_non_executable_pairs:
                self._diagnostic_counts["missing_or_non_executable_strike_pairs"] += missing_or_non_executable_pairs
            timestamps=[int(value.get("source_timestamp_ns") or 0) for value in bucket.values() if isinstance(value,dict)]
            if not timestamps:
                self._prune(ts); return ()
            anchor_ts=max(timestamps)
            atm=self.atm_provider(symbol,anchor_ts)
            if atm is None:
                self._diagnostic_counts["atm_unavailable"] += 1
                self._last_diagnostics={"symbol":symbol,"expiry":expiry,"grouped_strikes":len(grouped),"result_count":0,"outcome":"atm_unavailable"}
                self._prune(ts); return ()
            if not grouped:
                self._diagnostic_counts["no_executable_grouped_strikes"] += 1
                self._last_diagnostics={"symbol":symbol,"expiry":expiry,"grouped_strikes":0,"result_count":0,"outcome":"waiting_for_complete_executable_call_put_pairs"}
                self._prune(ts); return ()
            scan_stats={}
            results=scan_box_snapshot(grouped,atm_strike=float(atm),instrument_class=cls,
                                      config=self.config_provider(symbol),policy=self.policy,diagnostics=scan_stats)
            self._diagnostic_counts["snapshots_scanned"] += 1
            for name,value in scan_stats.items():
                self._diagnostic_counts["scan_"+name] += int(value)
            self._last_diagnostics={"symbol":symbol,"expiry":expiry,"atm_strike":float(atm),
                "grouped_strikes":len(grouped),"result_count":len(results),
                "min_executable_edge":float(self.config_provider(symbol).min_executable_edge),
                "outcome":"qualified" if results else "no_qualifying_box",
                "scan":scan_stats}
            self._prune(ts)
        if results and self.on_result is not None:self.on_result(results)
        return results

    TIMESTAMP_TOLERANCE_NS=3_000_000_000

    def _matching_bucket_key(self,symbol,ts,expiry):
        candidates=[key for key in self._buckets if key[0]==symbol and key[2]==expiry and abs(key[1]-ts)<=self.TIMESTAMP_TOLERANCE_NS]
        return min(candidates,key=lambda key:abs(key[1]-ts)) if candidates else (symbol,ts,expiry)

    def _prune(self,ts):
        cutoff=ts-3_000_000_000
        self._buckets={k:v for k,v in self._buckets.items() if k[1]>=cutoff}

__all__=["LiveBoxSpreadScanner"]
