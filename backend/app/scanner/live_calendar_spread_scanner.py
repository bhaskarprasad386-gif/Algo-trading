from __future__ import annotations
from dataclasses import dataclass, replace
from threading import RLock
from app.core.config import settings
from app.market_data.contracts import MarketDataRecord, InstrumentType
from app.market_data.opportunity import OpportunityLeg, OpportunitySignal, OrderSide, gross_profit_from_points, qualifies_opportunity
from app.models.live_calendar_spread_scanner_result import LiveCalendarSpreadScannerResult
from app.notifications.calendar_spread_alerts import CalendarSpreadAlertService
from sqlalchemy.exc import IntegrityError

@dataclass(frozen=True)
class CalendarSpreadSignal:
    underlying: str
    exchange: str
    instrument_type: str
    near_contract_month: str
    far_contract_month: str
    timestamp_ns: int
    near_bid: float
    near_ask: float
    far_bid: float
    far_ask: float
    lot_size: int
    edge_long: float
    edge_short: float
    edge_pct_long: float
    edge_pct_short: float
    liquidity_qty: float
    capacity_lots: int
    rank_score: float
    direction: str
    gap_points: float
    gross_profit: float
    qualifies: bool
    signal: OpportunitySignal

class LiveCalendarSpreadScanner:
    strategy_id = "calendar-spread"
    # Angel One exchange timestamps can differ by a few hundred milliseconds
    # between near/far contracts even when both belong to the same 1-second
    # market snapshot. Do not require nanosecond identity for pairing.
    TIMESTAMP_TOLERANCE_NS = 1_000_000_000
    SNAPSHOT_MAX_AGE_NS = 5_000_000_000

    def __init__(self, *, minimum_gap_points: float = 0.0, minimum_gross_profit: float = 0.0, alerts: CalendarSpreadAlertService | None = None):
        if minimum_gap_points < 0 or minimum_gross_profit < 0:
            raise ValueError("thresholds cannot be negative")
        self.minimum_gap_points = float(minimum_gap_points)
        self.minimum_gross_profit = float(minimum_gross_profit)
        self._latest: dict[tuple[str,str], dict[str, MarketDataRecord]] = {}
        self._signals: dict[tuple[str,str], CalendarSpreadSignal] = {}
        self._lock = RLock()
        self.alerts = alerts or CalendarSpreadAlertService()

    @staticmethod
    def _record_ok(record: MarketDataRecord) -> bool:
        return (
            record.instrument_type in {InstrumentType.FUTURE, InstrumentType.COMMODITY}
            and record.timestamp_ns > 0 and record.is_executable_quote
            and record.lot_size is not None and record.lot_size > 0 and bool(record.expiry)
        )

    def update(self, record: MarketDataRecord, *, contract_month: str | None = None) -> CalendarSpreadSignal | None:
        if not self._record_ok(record):
            return None
        key=(str(record.underlying or record.symbol).strip().upper(), record.instrument.exchange.strip().upper())
        month=(contract_month or record.expiry or "").strip()
        if not month:
            return None
        with self._lock:
            bucket=self._latest.setdefault(key,{})
            bucket[month]=record
            ordered=sorted(bucket.items(), key=lambda item: (item[1].expiry or item[0], item[0]))
            if len(ordered)>2:
                self._latest[key]={k:v for k,v in ordered[:2]}
            if len(self._latest[key])<2:
                return None
            ordered=sorted(self._latest[key].items(), key=lambda item: (item[1].expiry or item[0], item[0]))
            near_m, near=ordered[0]; far_m, far=ordered[1]
            if abs(near.timestamp_ns - far.timestamp_ns) > self.TIMESTAMP_TOLERANCE_NS or near.lot_size != far.lot_size or near.instrument.exchange != far.instrument.exchange:
                return None
            long_edge=float(far.bid)-float(near.ask); short_edge=float(near.bid)-float(far.ask)
            if long_edge < 0 and short_edge < 0:
                return None
            if long_edge >= short_edge:
                gap=long_edge; direction="LONG_NEAR_SHORT_FAR"
                legs=(OpportunityLeg(near,OrderSide.BUY,"near-entry"),OpportunityLeg(far,OrderSide.SELL,"far-entry"))
            else:
                gap=short_edge; direction="SHORT_NEAR_LONG_FAR"
                legs=(OpportunityLeg(near,OrderSide.SELL,"near-entry"),OpportunityLeg(far,OrderSide.BUY,"far-entry"))
            lot=int(near.lot_size); gross=gross_profit_from_points(gap,lot)
            qualifies=qualifies_opportunity(gap_points=gap,gross_profit=gross,minimum_gap_points=self.minimum_gap_points,minimum_gross_profit=self.minimum_gross_profit)
            # OpportunitySignal requires leg timestamps to be identical. Once the
            # pair passes Calendar-specific skew tolerance, anchor both leg snapshots
            # to the near-leg timestamp. Original feed records remain unchanged.
            signal_near = replace(near, timestamp_ns=near.timestamp_ns)
            signal_far = replace(far, timestamp_ns=near.timestamp_ns)
            signal_legs = (OpportunityLeg(signal_near, OrderSide.BUY if direction == "LONG_NEAR_SHORT_FAR" else OrderSide.SELL, "near-entry"), OpportunityLeg(signal_far, OrderSide.SELL if direction == "LONG_NEAR_SHORT_FAR" else OrderSide.BUY, "far-entry"))
            base=max(float(near.ask),float(far.ask),1e-12)
            nq=min(float(near.bid_qty or 0),float(near.ask_qty or 0)); fq=min(float(far.bid_qty or 0),float(far.ask_qty or 0))
            liquidity=min(nq,fq) if nq>0 and fq>0 else 0.0
            cap=int(settings.LIVE_CASH_FUTURE_CAPITAL/(base*lot)) if settings.LIVE_CASH_FUTURE_CAPITAL>0 else 0
            signal=OpportunitySignal(strategy_id=self.strategy_id,opportunity_type="calendar-spread",symbol=key[0],timestamp_ns=near.timestamp_ns,gap_points=gap,gross_profit=gross,lot_size=lot,qualifies=qualifies,minimum_gap_points=self.minimum_gap_points,minimum_gross_profit=self.minimum_gross_profit,legs=signal_legs,expiry=far.expiry,metadata={"direction":direction,"exchange":key[1],"near_contract_month":near_m,"far_contract_month":far_m,"source":"common-market-data","live_orders":False})
            result=CalendarSpreadSignal(key[0],key[1],str(near.instrument_type.value),near_m,far_m,near.timestamp_ns,float(near.bid),float(near.ask),float(far.bid),float(far.ask),lot,long_edge,short_edge,long_edge/base*100,short_edge/base*100,liquidity,cap,gap/base,direction,gap,gross,qualifies,signal)
            self._signals[key]=result
        if qualifies and (session_factory := getattr(self, "_session_factory", None)):
            self._persist(result, session_factory)
            db=session_factory()
            try:
                self.alerts.persist(db, result)
                self.alerts.emit(db, result)
            finally:
                db.close()
        return result

    def set_session_factory(self, session_factory):
        self._session_factory=session_factory

    def _persist(self, signal, session_factory):
        db=session_factory()
        try:
            row=LiveCalendarSpreadScannerResult(
                underlying=signal.underlying,exchange=signal.exchange,instrument_type=signal.instrument_type,
                near_contract_month=signal.near_contract_month,far_contract_month=signal.far_contract_month,
                timestamp_ns=signal.timestamp_ns,near_bid=signal.near_bid,near_ask=signal.near_ask,
                far_bid=signal.far_bid,far_ask=signal.far_ask,lot_size=signal.lot_size,
                edge_long=signal.edge_long,edge_short=signal.edge_short,edge_pct_long=signal.edge_pct_long,
                edge_pct_short=signal.edge_pct_short,liquidity_qty=signal.liquidity_qty,
                capacity_lots=signal.capacity_lots,rank_score=signal.rank_score,
            )
            db.add(row); db.commit()
        except IntegrityError:
            db.rollback()
        finally:
            db.close()

    def observe(self,payload,session_factory=None):
        if session_factory is not None:self.set_session_factory(session_factory)
        try:
            from app.market_data.contracts import InstrumentKey
            raw_kind=str(payload.get("instrument_type","future")).lower()
            kind=InstrumentType.COMMODITY if raw_kind in {"commodity","commodity_future"} else InstrumentType.FUTURE
            record=MarketDataRecord(
                instrument=InstrumentKey(str(payload.get("exchange") or payload.get("segment") or ""),str(payload.get("segment") or payload.get("exchange") or ""),str(payload.get("token") or payload.get("symbol") or payload.get("underlying") or "calendar")),
                symbol=str(payload.get("symbol") or payload.get("underlying") or "calendar"),instrument_type=kind,
                timestamp_ns=int(payload.get("timestamp_ns") or 0),timeframe=str(payload.get("timeframe") or "1s"),
                ltp=payload.get("ltp"),bid=payload.get("bid"),ask=payload.get("ask"),bid_qty=payload.get("bid_qty"),ask_qty=payload.get("ask_qty"),
                volume=payload.get("volume"),oi=payload.get("oi"),open=payload.get("open"),high=payload.get("high"),low=payload.get("low"),close=payload.get("close"),
                underlying=payload.get("underlying"),expiry=payload.get("expiry"),lot_size=payload.get("lot_size"),tick_size=payload.get("tick_size"),
            )
            return self.update(record)
        except Exception:return None

    def snapshot(self,limit=50,*,minimum_gap_points=None,minimum_gross_profit=None):
        min_gap=self.minimum_gap_points if minimum_gap_points is None else float(minimum_gap_points)
        min_gross=self.minimum_gross_profit if minimum_gross_profit is None else float(minimum_gross_profit)
        if min_gap<0 or min_gross<0:raise ValueError("thresholds cannot be negative")
        import time
        now_ns = time.time_ns()
        with self._lock:values=tuple(self._signals.values())
        values=tuple(x for x in values if now_ns - x.timestamp_ns <= self.SNAPSHOT_MAX_AGE_NS and qualifies_opportunity(gap_points=x.gap_points,gross_profit=x.gross_profit,minimum_gap_points=min_gap,minimum_gross_profit=min_gross))
        return tuple(sorted(values,key=lambda x:(x.gross_profit,x.gap_points),reverse=True)[:limit])
