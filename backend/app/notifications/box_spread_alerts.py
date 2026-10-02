"""Durable Box Spread alerts with exact executable Bid/Ask snapshot."""
from __future__ import annotations
from datetime import datetime,timedelta
from app.core.config import settings
from app.models import LiveBoxSpreadAlertHistory, User
from app.notifications.whatsapp import WhatsAppConfig, WhatsAppNotifier
from app.notifications.common import AlertEvent, AlertService
class BoxSpreadAlertService:
    def __init__(self):
        self._alerts=AlertService()
        self._notifier=WhatsAppNotifier(WhatsAppConfig(access_token=settings.WHATSAPP_ACCESS_TOKEN,phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,graph_api_version=settings.WHATSAPP_GRAPH_API_VERSION,enabled=settings.WHATSAPP_ENABLED))
        self._last_sent={}
    @staticmethod
    def _message(r):
        l,h=r.low,r.high
        return ("BOX SPREAD ALERT\n"+f"{l.underlying} {l.instrument_class} | Expiry: {l.expiry}\n"+f"Strikes: {l.strike:g}/{h.strike:g} | Distance: {r.strike_distance}\n"+f"Direction: {r.direction}\n"+
            f"LOW CE Bid/Ask: ₹{l.call_bid:.2f}/₹{l.call_ask:.2f} | LOW PE Bid/Ask: ₹{l.put_bid:.2f}/₹{l.put_ask:.2f}\n"+
            f"HIGH CE Bid/Ask: ₹{h.call_bid:.2f}/₹{h.call_ask:.2f} | HIGH PE Bid/Ask: ₹{h.put_bid:.2f}/₹{h.put_ask:.2f}\n"+
            f"Executable Edge: ₹{r.executable_edge:.4f} | Edge/Lot: ₹{r.edge_per_lot:.2f}\n"+f"Gross P&L: ₹{r.gross_pnl:.2f}")
    def persist(self,db,results):
        retention=max(1,int(settings.LIVE_BOX_SPREAD_RESULT_RETENTION_DAYS))
        db.query(LiveBoxSpreadAlertHistory).filter(LiveBoxSpreadAlertHistory.observed_at < datetime.utcnow()-timedelta(days=retention)).delete(synchronize_session=False)
        added=0
        for r in results:
            l,h=r.low,r.high
            exists=db.query(LiveBoxSpreadAlertHistory.id).filter(LiveBoxSpreadAlertHistory.symbol==l.underlying,LiveBoxSpreadAlertHistory.expiry==l.expiry,LiveBoxSpreadAlertHistory.timestamp_ns==l.timestamp_ns,LiveBoxSpreadAlertHistory.low_strike==l.strike,LiveBoxSpreadAlertHistory.high_strike==h.strike,LiveBoxSpreadAlertHistory.direction==r.direction).first()
            if exists: continue
            db.add(LiveBoxSpreadAlertHistory(observed_at=datetime.utcnow(),timestamp_ns=l.timestamp_ns,symbol=l.underlying,instrument_class=l.instrument_class,expiry=l.expiry,low_strike=l.strike,high_strike=h.strike,direction=r.direction,low_call_bid=l.call_bid,low_call_ask=l.call_ask,low_put_bid=l.put_bid,low_put_ask=l.put_ask,high_call_bid=h.call_bid,high_call_ask=h.call_ask,high_put_bid=h.put_bid,high_put_ask=h.put_ask,executable_edge=r.executable_edge,edge_per_lot=r.edge_per_lot,gross_pnl=r.gross_pnl,lot_size=l.lot_size)); added+=1
        db.commit(); return added
    def notify_users(self,db,results):
        if not results: return 0
        sent=0
        for r in results:
            l,h=r.low,r.high
            sent += self._alerts.dispatch(db, AlertEvent(
                strategy_id="box-spread",
                event_id=f"{l.underlying}:{l.expiry}:{l.strike:g}:{h.strike:g}:{r.direction}",
                symbol=l.underlying, timestamp_ns=l.timestamp_ns,
                message=self._message(r),
                metadata={"gross_profit": r.gross_pnl, "edge": r.executable_edge, "paper_trade": {"direction": r.direction, "expiry": l.expiry, "earliest_expiry": l.expiry, "lot_size": l.lot_size, "lots": 1, "edge": r.executable_edge, "capital_used": abs(float(h.strike) - float(l.strike)) * l.lot_size, "legs": [{"strike": l.strike, "instrument": "LOW_CALL", "side": "BUY" if r.direction=="LONG" else "SELL", "price": l.call_ask if r.direction=="LONG" else l.call_bid}, {"strike": l.strike, "instrument": "LOW_PUT", "side": "BUY" if r.direction=="LONG" else "SELL", "price": l.put_ask if r.direction=="LONG" else l.put_bid}, {"strike": h.strike, "instrument": "HIGH_CALL", "side": "SELL" if r.direction=="LONG" else "BUY", "price": h.call_bid if r.direction=="LONG" else h.call_ask}, {"strike": h.strike, "instrument": "HIGH_PUT", "side": "SELL" if r.direction=="LONG" else "BUY", "price": h.put_bid if r.direction=="LONG" else h.put_ask}]}}
            ))
        return sent
__all__=["BoxSpreadAlertService"]
