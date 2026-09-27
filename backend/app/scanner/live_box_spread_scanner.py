"""Bounded one-second Box Spread quote assembler and scanner."""
from __future__ import annotations
from threading import Lock
from datetime import datetime
from app.backtesting.arbitrage_backtester import OptionQuote
from app.scanner.box_spread import BoxSpreadScanConfig, scan_box_snapshot

class LiveBoxSpreadScanner:
    def __init__(self, *, atm_provider, config_provider=None, on_result=None, policy=None):
        self.atm_provider=atm_provider
        self.config_provider=config_provider or (lambda _s: BoxSpreadScanConfig())
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
        typ=str(payload.get("option_type") or "").strip().upper()
        strike=self._price(payload.get("strike"))
        bid=self._price(payload.get("bid")); ask=self._price(payload.get("ask"))
        if not symbol or cls not in {"STOCK","INDEX"} or ts<=0 or typ not in {"CE","PE"} or strike is None or bid is None or ask is None or ask<bid:return ()
        key=(symbol,ts)
        with self._lock:
            bucket=self._buckets.setdefault(key,{})
            bucket[(strike,typ)]=payload
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
                    grouped.append(OptionQuote(
                        ts,symbol,ce_expiry,s,
                        self._price(ce.get("bid")) or 0,self._price(ce.get("ask")) or 0,
                        self._price(pe.get("bid")) or 0,self._price(pe.get("ask")) or 0,
                        ce_lot,cls,
                        int(float(ce.get("volume") or 0)),int(float(ce.get("oi") or 0))
                    ))
                except (ValueError,TypeError): continue
            atm=self.atm_provider(symbol,ts)
            if atm is None or not grouped:
                self._prune(ts); return ()
            results=scan_box_snapshot(grouped,atm_strike=float(atm),instrument_class=cls,
                                      config=self.config_provider(symbol),policy=self.policy)
            self._prune(ts)
        if results and self.on_result is not None:self.on_result(results)
        return results

    def _prune(self,ts):
        cutoff=ts-3_000_000_000
        self._buckets={k:v for k,v in self._buckets.items() if k[1]>=cutoff}

__all__=["LiveBoxSpreadScanner"]
