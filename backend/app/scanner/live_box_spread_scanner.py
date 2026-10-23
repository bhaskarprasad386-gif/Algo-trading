"""Bounded one-second Box Spread quote assembler and scanner."""
from __future__ import annotations
from threading import Lock
from datetime import datetime
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

    @staticmethod
    def _price(v):
        try:
            n=float(v); return n if n>0 else None
        except (TypeError,ValueError): return None

    @staticmethod
    def _expiry(v):
        text=str(v or "").strip().upper()
        for fmt in ("%d%b%Y","%d%b%y","%Y-%m-%d","%d-%m-%Y"):
            try:return int(datetime.strptime(text,fmt).strftime("%Y%m%d"))
            except ValueError:pass
        raise ValueError(f"invalid option expiry: {v!r}")

    def observe(self,payload):
        symbol=str(payload.get("underlying") or "").strip().upper()
        cls=str(payload.get("instrument_class") or "").strip().upper()
        ts=int(payload.get("source_timestamp_ns") or 0)
        # Only wall-clock epoch timestamps are eligible for the live freshness guard.
        # Compact logical timestamps remain valid for deterministic scanner tests.
        import time
        if ts >= 1_000_000_000_000_000 and time.time_ns() - ts > 5_000_000_000:
            return ()
        typ=str(payload.get("option_type") or "").strip().upper()
        strike=self._price(payload.get("strike"))
        bid=self._price(payload.get("bid")); ask=self._price(payload.get("ask"))
        # Keep an identified leg even when its executable quote/liquidity has
        # not arrived yet. The other three legs may already be usable; retain
        # this candidate for up to three seconds for the missing leg to update.
        if not symbol or cls not in {"STOCK","INDEX"} or ts<=0 or typ not in {"CE","PE"} or strike is None:return ()
        with self._lock:
            expiry=self._expiry(payload.get("expiry"))
            key=self._matching_bucket_key(symbol,ts,expiry)
            bucket=self._buckets.setdefault(key,{})
            leg_key=(strike,typ)
            previous=bucket.get(leg_key)
            # Out-of-order broker updates must never overwrite a newer quote
            # for the same contract within the active assembly window.
            if previous is not None:
                try:
                    previous_ts=int(previous.get("source_timestamp_ns") or 0)
                except (TypeError,ValueError):
                    previous_ts=0
                if previous_ts > ts:
                    self._prune(ts)
                    return ()
            bucket[leg_key]=payload
            grouped=[]
            for s in sorted({k[0] for k in bucket}):
                ce,pe=bucket.get((s,"CE")),bucket.get((s,"PE"))
                if ce is None or pe is None: continue
                try:
                    ce_expiry = self._expiry(ce.get("expiry"))
                    pe_expiry = self._expiry(pe.get("expiry"))
                    ce_lot = int(float(ce.get("lot_size") or 0))
                    pe_lot = int(float(pe.get("lot_size") or 0))
                    if ce_expiry != pe_expiry or ce_lot <= 0 or ce_lot != pe_lot:
                        continue
                    # A leg without both sides of its executable market is
                    # pending, not an immediate candidate rejection. A later
                    # tick can fill it while this bucket remains inside the
                    # three-second assembly window.
                    ce_bid, ce_ask = self._price(ce.get("bid")), self._price(ce.get("ask"))
                    pe_bid, pe_ask = self._price(pe.get("bid")), self._price(pe.get("ask"))
                    if (
                        ce_bid is None or ce_ask is None or ce_ask < ce_bid
                        or pe_bid is None or pe_ask is None or pe_ask < pe_bid
                    ):
                        continue
                    grouped.append(OptionQuote(
                        ts,symbol,ce_expiry,s,
                        ce_bid, ce_ask, pe_bid, pe_ask,
                        ce_lot,cls,
                        # Use the weaker CE/PE leg so one liquid side cannot
                        # mask an illiquid side in the four-leg structure.
                        min(int(float(ce.get("volume") or 0)), int(float(pe.get("volume") or 0))),
                        min(int(float(ce.get("oi") or 0)), int(float(pe.get("oi") or 0))),
                        self._price(ce.get("ltp")), self._price(pe.get("ltp"))
                    ))
                except (ValueError,TypeError): continue
            timestamps=[int(value.get("source_timestamp_ns") or 0) for value in bucket.values() if isinstance(value,dict)]
            if not timestamps or max(timestamps)-min(timestamps)>self.TIMESTAMP_TOLERANCE_NS:
                self._prune(ts); return ()
            anchor_ts=max(timestamps)
            grouped=[OptionQuote(anchor_ts,symbol,q.expiry,q.strike,q.call_bid,q.call_ask,q.put_bid,q.put_ask,q.lot_size,q.instrument_class,q.volume,q.oi,q.call_ltp,q.put_ltp) for q in grouped]
            atm=self.atm_provider(symbol,anchor_ts)
            if atm is None or not grouped:
                self._prune(ts); return ()
            results=scan_box_snapshot(grouped,atm_strike=float(atm),instrument_class=cls,
                                      config=self.config_provider(symbol),policy=self.policy)
            self._prune(ts)
        if results and self.on_result is not None:self.on_result(results)
        return results

    # Box Spread waits at most three seconds for a delayed fourth leg.
    TIMESTAMP_TOLERANCE_NS=3_000_000_000

    def _matching_bucket_key(self,symbol,ts,expiry):
        candidates=[
            key for key in self._buckets
            if key[0]==symbol
            and key[2]==expiry
            and abs(key[1]-ts)<=self.TIMESTAMP_TOLERANCE_NS
        ]
        return min(candidates,key=lambda key:abs(key[1]-ts)) if candidates else (symbol,ts,expiry)

    def _prune(self,ts):
        cutoff=ts-3_000_000_000
        self._buckets={k:v for k,v in self._buckets.items() if k[1]>=cutoff}

__all__=["LiveBoxSpreadScanner"]
