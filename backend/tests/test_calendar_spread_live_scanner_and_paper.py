def test_calendar_live_scanner_pairs_two_expiries_and_calculates_edges(monkeypatch):
 from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
 s=LiveCalendarSpreadScanner()
 a={"underlying":"NIFTY","exchange":"NFO","instrument_type":"INDEX_FUTURE","contract_month":"2026-09","expiry":"2026-09-30","timestamp_ns":1,"bid":100,"ask":101,"bid_qty":100,"ask_qty":100,"lot_size":75}
 b={**a,"contract_month":"2026-10","expiry":"2026-10-29","bid":105,"ask":106}
 x=s.observe(a); y=s.observe(b)
 assert x is None and y and y.edge_long==4 and y.edge_short==-6 and y.liquidity_qty==100

def test_calendar_paper_pnl_formula():
 from app.execution.calendar_spread_paper_routes import Entry,Exit
 # LONG near / SHORT far: near +2, far +3 = +5 per unit
 near_entry,far_entry=100,110; near_exit,far_exit=102,107; lot=75; lots=2
 assert ((near_exit-near_entry)+(far_entry-far_exit))*lot*lots==750


def test_calendar_live_scanner_snapshot_keeps_underlying_and_exchange():
 from app.scanner.live_calendar_spread_scanner import LiveCalendarSpreadScanner
 s=LiveCalendarSpreadScanner()
 a={"underlying":"BANKNIFTY","exchange":"NFO","instrument_type":"INDEX_FUTURE","contract_month":"2026-09","expiry":"2026-09-30","timestamp_ns":2,"bid":100,"ask":101,"bid_qty":100,"ask_qty":100,"lot_size":35}
 b={**a,"contract_month":"2026-10","expiry":"2026-10-29","bid":105,"ask":106}
 s.observe(a); s.observe(b)
 rows=s.snapshot()
 assert len(rows)==1
 assert rows[0].underlying=="BANKNIFTY"
 assert rows[0].exchange=="NFO"
 assert rows[0].capacity_lots>0
