from __future__ import annotations
import math
from dataclasses import dataclass
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult

@dataclass(frozen=True)
class CalendarSpreadSignal:
    underlying:str; exchange:str; instrument_type:str; near_contract_month:str; far_contract_month:str
    timestamp_ns:int; near_bid:float; near_ask:float; far_bid:float; far_ask:float; lot_size:int
    edge_long:float; edge_short:float; edge_pct_long:float; edge_pct_short:float
    liquidity_qty:float; capacity_lots:int; rank_score:float

class LiveCalendarSpreadScanner:
    def __init__(self): self._latest={}
    @staticmethod
    def _p(v):
        try: x=float(v); return x if math.isfinite(x) and x>0 else None
        except (TypeError,ValueError): return None
    def observe(self,payload,session_factory=None):
        try:
            u=str(payload.get("underlying") or "").strip().upper(); ex=str(payload.get("exchange") or "").strip().upper()
            kind=str(payload.get("instrument_type") or "").strip().upper(); month=str(payload.get("contract_month") or "").strip()
            ts=int(payload.get("timestamp_ns") or payload.get("exchange_timestamp_ns") or 0); expiry=str(payload.get("expiry") or "").strip()
            bid=self._p(payload.get("bid")); ask=self._p(payload.get("ask")); lot=int(float(payload.get("lot_size") or 0))
            if not u or not ex or not month or not expiry or ts<=0 or not bid or not ask or ask<bid or lot<=0:return None
            key=(u,ex)
            bucket=self._latest.setdefault(key,{})
            bucket[month]=(ts,bid,ask,lot,kind,expiry,float(payload.get("bid_qty") or 0),float(payload.get("ask_qty") or 0))
            if len(bucket)>2:
                for m in sorted(bucket,key=lambda x:bucket[x][5])[:-2]: bucket.pop(m,None)
            if len(bucket)<2:return None
            near_m,far_m=sorted(bucket,key=lambda m:bucket[m][5])[:2]; n=bucket[near_m]; f=bucket[far_m]
            if n[0]!=f[0]: return None
            if n[4]!=f[4] or n[3]!=f[3]: return None
            nq=min(n[6],n[7]); fq=min(f[6],f[7]); liquidity=min(nq,fq) if nq>0 and fq>0 else 0.0
            edge_long=f[1]-n[2]; edge_short=n[1]-f[2]
            base=(n[2]+f[2])/2
            if base<=0:return None
            cap=int(settings.LIVE_CASH_FUTURE_CAPITAL/(base*n[3])) if getattr(settings,"LIVE_CASH_FUTURE_CAPITAL",0)>0 else 0
            score=max(edge_long,edge_short)/base
            signal=CalendarSpreadSignal(u,ex,n[4],near_m,far_m,n[0],n[1],n[2],f[1],f[2],n[3],edge_long,edge_short,edge_long/base*100,edge_short/base*100,liquidity,cap,score)
            self._latest[key]=bucket
            if session_factory and (edge_long>0 or edge_short>0):
                db=session_factory()
                try:
                    row=LiveCalendarSpreadScannerResult(**signal.__dict__); db.add(row); db.commit()
                except IntegrityError: db.rollback()
                finally: db.close()
            return signal
        except Exception:return None
    def snapshot(self,limit=50):
        rows=[]
        for bucket in self._latest.values():
            if len(bucket)<2:continue
            near_m,far_m=sorted(bucket,key=lambda m:bucket[m][5])[:2]; n=bucket[near_m]; f=bucket[far_m]
            if n[0]!=f[0]:continue
            base=(n[2]+f[2])/2
            if base<=0:continue
            rows.append(CalendarSpreadSignal(n and next(iter(self._latest))[0] or "", "", n[4], near_m, far_m,n[0],n[1],n[2],f[1],f[2],n[3],f[1]-n[2],n[1]-f[2],(f[1]-n[2])/base*100,(n[1]-f[2])/base*100,min(n[6],n[7],f[6],f[7]),0,max(f[1]-n[2],n[1]-f[2])/base))
        return sorted(rows,key=lambda x:x.rank_score,reverse=True)[:limit]
