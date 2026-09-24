from app.backtesting.arbitrage_strategy_adapters import (
    BoxSpreadStrategyAdapter, CalendarSpreadStrategyAdapter, CashFutureStrategyAdapter,
    SyntheticCashCarryStrategyAdapter, CashFutureTradeReporter, CashFutureUniversalMultiLegAdapter,
)


def option(ts, strike, cb, ca, pb, pa, expiry=20261231):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "strike": strike,
            "call_bid": cb, "call_ask": ca, "put_bid": pb, "put_ask": pa}


def future(ts, bid, ask, expiry=20261231):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "bid": bid, "ask": ask}


def calendar(ts, expiry, bid, ask):
    return {"timestamp_ns": ts, "underlying": "ABC", "expiry": expiry, "bid": bid, "ask": ask,
            "strike": 100.0, "option_type": "CALL"}


def test_box_adapter_closes_only_on_later_reverse_edge():
    adapter = BoxSpreadStrategyAdapter(fees_per_unit=1.0)
    low = option(1, 100, 6, 7, 3, 4)
    high = option(1, 110, 2, 3, 2, 3)
    entries = tuple(adapter.entry({"low": low, "high": high, "data_resolution": "1s"}))
    assert len(entries) == 1
    # Executable option quotes produce a 5-point long-box edge: width 10 minus 5-point debit.
    assert entries[0].entry_price == 5.0
    assert adapter.exit(entries[0], {"low": low, "high": high}) is None

    # The adapter's ExitExecution gross PnL is entry price plus the executable
    # reverse edge, so the later 23-point close price yields 28 gross PnL.
    low2 = option(3, 100, 20, 21, 3, 4)
    high2 = option(3, 110, 2, 3, 20, 21)
    close = adapter.exit(entries[0], {"low": low2, "high": high2})
    assert close is not None
    assert close.gross_pnl == 28.0
    assert close.fees == 2.0


def test_synthetic_adapter_preserves_expiry_and_later_exit():
    adapter = SyntheticCashCarryStrategyAdapter(fees_per_unit=0.5)
    event = {"option": option(1, 100, 10, 11, 1, 2), "future": future(1, 111, 112)}
    entries = tuple(adapter.entry(event))
    assert len(entries) == 1
    assert entries[0].expiry == "20261231"
    later = {"option": option(2, 100, 12, 13, 1, 2), "future": future(2, 101, 102)}
    close = adapter.exit(entries[0], later)
    assert close is not None
    assert close.timestamp_ns == 2
    assert close.fees == 1.0


def test_cash_future_adapter_supports_both_directions_without_fabrication():
    event = {"cash_future": {"timestamp_ns": 1, "underlying": "ABC", "spot_bid": 100,
        "spot_ask": 101, "future_bid": 104, "future_ask": 105, "expiry": 20261231,
        "lot_size": 10, "carry_factor": 1.0}}
    adapter = CashFutureStrategyAdapter(fees_per_unit=0.25)
    entries = tuple(adapter.entry(event))
    assert len(entries) == 1
    assert entries[0].entry_price == 3
    assert adapter.exit(entries[0], event) is None
    later = {"cash_future": {**event["cash_future"], "timestamp_ns": 2,
        "spot_bid": 106, "spot_ask": 107, "future_bid": 102, "future_ask": 103}}
    close = adapter.exit(entries[0], later)
    assert close is not None
    assert close.gross_pnl == 6


def test_calendar_adapter_keeps_near_and_far_expiries_explicit():
    adapter = CalendarSpreadStrategyAdapter(fees_per_unit=0.5)
    event = {"near": calendar(1, 20260924, 10, 11), "far": calendar(1, 20261029, 15, 16)}
    entries = tuple(adapter.entry(event))
    assert len(entries) == 1
    assert entries[0].expiry == "20260924"
    assert entries[0].metadata["far_expiry"] == 20261029
    later = {"near": calendar(3, 20260924, 14, 15), "far": calendar(3, 20261029, 12, 13)}
    close = adapter.exit(entries[0], later)
    assert close is not None
    assert close.timestamp_ns == 3
    assert close.gross_pnl == 5


def test_adapters_require_real_quote_payloads():
    adapter = CalendarSpreadStrategyAdapter()
    try:
        tuple(adapter.entry({"near": {}}))
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("invalid quote payload must be rejected")


def _universal_cf_context(ts, cash_payload, future_payload, sequence=1):
    from app.backtesting.engine import EventContext
    from app.backtesting.historical_catalog import HistoricalRecord

    record = HistoricalRecord(
        "test", "NSE:ABC", "tick", ts,
        {
            "cash": cash_payload,
            "future": future_payload,
            "__replay_legs__": {
                "cash": {"source": "test", "instrument": "NSE:ABC", "timeframe": "tick"},
                "future": {"source": "test", "instrument": "NFO:ABC-20261231", "timeframe": "tick"},
            },
        },
        1,
    )
    return EventContext(ts, sequence, "test", "NSE:ABC", record.payload, record)


def test_universal_cash_future_adapter_emits_exact_two_executable_legs():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter
    from app.backtesting.contracts import MultiLegStrategyProtocol
    from app.backtesting.execution import ExecutionSide

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    context = _universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )

    assert isinstance(adapter, MultiLegStrategyProtocol)
    legs = tuple(adapter(context))

    assert len(legs) == 2
    cash_order, cash_book, cash_ts = legs[0]
    future_order, future_book, future_ts = legs[1]
    assert cash_order.instrument == "NSE:ABC"
    assert cash_order.side == ExecutionSide.BUY
    assert cash_order.quantity == 10
    assert cash_book.asks[0].price == 101.0
    assert future_order.instrument == "NFO:ABC-20261231"
    assert future_order.side == ExecutionSide.SELL
    assert future_order.quantity == 10
    assert future_book.bids[0].price == 104.0
    assert cash_ts == future_ts == 1


def test_universal_cash_future_trade_reporter_state_round_trips_open_lifecycle():
    class Writer:
        def __init__(self):
            self.trades = []
        def record_trades(self, trades):
            self.trades.extend(trades)

    writer = Writer()
    reporter = CashFutureTradeReporter(writer)
    reporter._open["CASH"] = {
        "timestamp_ns": 10,
        "entry_price": 100.0,
        "entry_reference_price": 99.0,
        "entry_side": "BUY",
        "entry_quantity": 5,
        "entry_fees": 1.0,
        "entry_slippage": 2.0,
        "entry_edge": 4.0,
        "entry_order_id": "CF:10:src:ABC:1:OPEN:CASH",
    }
    reporter._sequence = 3

    state = reporter.get_state()
    restored = CashFutureTradeReporter(writer)
    restored.set_state(state)

    assert restored.get_state() == state
    assert restored._open["CASH"]["entry_order_id"] == "CF:10:src:ABC:1:OPEN:CASH"
    assert restored._sequence == 3

def test_universal_cash_future_adapter_order_ids_are_unique_for_same_timestamp_events():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    first = tuple(adapter(_universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
        sequence=1,
    )))
    second = tuple(adapter(_universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
        sequence=2,
    )))

    assert len(first) == len(second) == 2
    assert {leg[0].order_id for leg in first}.isdisjoint({leg[0].order_id for leg in second})

def test_universal_cash_future_adapter_closes_only_on_later_reverse_edge():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    entry_context = _universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert len(tuple(adapter(entry_context))) == 2
    adapter.on_atomic_execution(type("Result", (), {"rejected": False})())

    same_context = _universal_cf_context(
        2,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert tuple(adapter(same_context)) == ()

    close_context = _universal_cf_context(
        3,
        {"bid": 106.0, "ask": 107.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 102.0, "ask": 103.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    legs = tuple(adapter(close_context))
    assert len(legs) == 2
    assert legs[0][0].instrument == "NSE:ABC"
    assert legs[0][0].side.value == "SELL"
    assert legs[1][0].instrument == "NFO:ABC-20261231"
    assert legs[1][0].side.value == "BUY"


def test_universal_cash_future_adapter_binds_original_contract_across_rollover():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter
    from app.backtesting.contracts import EventContext
    from app.backtesting.historical_catalog import HistoricalRecord

    def ctx(ts, future_instrument, cash_bid, cash_ask, future_bid, future_ask):
        record = HistoricalRecord(
            "test", "NSE:ABC", "tick", ts,
            {
                "cash": {"bid": cash_bid, "ask": cash_ask, "bid_quantity": 20, "ask_quantity": 20},
                "future": {"bid": future_bid, "ask": future_ask, "bid_quantity": 20, "ask_quantity": 20},
                "__replay_legs__": {
                    "cash": {"source": "test", "instrument": "NSE:ABC", "timeframe": "tick"},
                    "future": {"source": "test", "instrument": future_instrument, "timeframe": "tick"},
                },
            },
            ts,
        )
        return EventContext(ts, sequence=ts, source="test", instrument="NSE:ABC", payload=record.payload, record=record)

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    entry = tuple(adapter(ctx(1, "NFO:ABC-SEP", 100, 101, 104, 105)))
    assert len(entry) == 2
    adapter.on_atomic_execution(type("Result", (), {"rejected": False})())
    close = tuple(adapter(ctx(2, "NFO:ABC-OCT", 106, 107, 102, 103)))
    assert len(close) == 2
    assert close[1][0].instrument == "NFO:ABC-SEP"
