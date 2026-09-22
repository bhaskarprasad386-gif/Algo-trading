from app.backtesting.arbitrage_strategy_adapters import (
    BoxSpreadStrategyAdapter, CalendarSpreadStrategyAdapter, CashFutureStrategyAdapter,
    SyntheticCashCarryStrategyAdapter,
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
    low = option(1, 100, 6, 4, 5, 3)
    high = option(1, 110, 2, 3, 2, 2)
    entries = tuple(adapter.entry({"low": low, "high": high, "data_resolution": "1s"}))
    assert len(entries) == 1
    # BoxSpreadBacktester.evaluate() gives width - executable debit = 12.
    assert entries[0].entry_price == 12.0
    assert adapter.exit(entries[0], {"low": low, "high": high}) is None

    # Later quotes create a genuine positive reverse executable edge of 14.
    low2 = option(3, 100, 20, 21, 1, 1.5)
    high2 = option(3, 110, 14, 14.5, 20, 21)
    close = adapter.exit(entries[0], {"low": low2, "high": high2})
    assert close is not None
    assert close.gross_pnl == 14.0
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
        "test",
        "NSE:ABC",
        "tick",
        ts,
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
    from app.backtesting.contracts import HistoricalRecord

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
            }, ts,
        )
        return EventContext(record)

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    opened = tuple(adapter(ctx(1, "NFO:ABC-OLD", 100, 101, 104, 105)))
    assert opened[1][0].instrument == "NFO:ABC-OLD"
    adapter.on_atomic_execution(type("Result", (), {"rejected": False})())
    closed = tuple(adapter(ctx(2, "NFO:ABC-NEW", 106, 107, 102, 103)))
    assert closed[1][0].instrument == "NFO:ABC-OLD"

def test_universal_cash_future_adapter_short_direction_binds_original_future_across_rollover():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter
    from app.backtesting.contracts import EventContext, HistoricalRecord

    def ctx(ts, future_instrument, cash_bid, cash_ask, future_bid, future_ask):
        return EventContext(HistoricalRecord(
            "test", "NSE:ABC", "tick", ts,
            {
                "cash": {"bid": cash_bid, "ask": cash_ask, "bid_quantity": 20, "ask_quantity": 20},
                "future": {"bid": future_bid, "ask": future_ask, "bid_quantity": 20, "ask_quantity": 20},
                "__replay_legs__": {
                    "cash": {"source": "test", "instrument": "NSE:ABC", "timeframe": "tick"},
                    "future": {"source": "test", "instrument": future_instrument, "timeframe": "tick"},
                },
            }, ts,
        ))

    adapter = CashFutureUniversalMultiLegAdapter(
        direction="SHORT_CASH_LONG_FUTURE", quantity=10
    )
    opened = tuple(adapter(ctx(1, "NFO:ABC-OLD", 105, 106, 100, 101)))
    assert opened[0][0].instrument == "NSE:ABC"
    assert opened[0][0].side.value == "SELL"
    assert opened[1][0].instrument == "NFO:ABC-OLD"
    assert opened[1][0].side.value == "BUY"
    adapter.on_atomic_execution(type("Result", (), {"rejected": False})())

    closed = tuple(adapter(ctx(2, "NFO:ABC-NEW", 99, 100, 106, 107)))
    assert closed[0][0].instrument == "NSE:ABC"
    assert closed[0][0].side.value == "BUY"
    assert closed[1][0].instrument == "NFO:ABC-OLD"
    assert closed[1][0].side.value == "SELL"


def test_universal_cash_future_adapter_integrates_open_close_with_atomic_engine():
    from app.backtesting.universal_engine import UniversalEventBacktestEngine

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    open_record = _universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    close_record = _universal_cf_context(
        2,
        {"bid": 106.0, "ask": 107.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 102.0, "ask": 103.0, "bid_quantity": 20, "ask_quantity": 20},
    )

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run_multi_leg([open_record, close_record], adapter)

    assert result.fill_count == 4
    assert engine.portfolio.positions["NSE:ABC"].quantity == 0
    assert engine.portfolio.positions["NFO:ABC-20261231"].quantity == 0
    assert result.realized_pnl == 60.0


def test_universal_cash_future_adapter_short_direction_integrates_open_close_with_atomic_engine():
    from app.backtesting.universal_engine import UniversalEventBacktestEngine

    adapter = CashFutureUniversalMultiLegAdapter(
        direction="SHORT_CASH_LONG_FUTURE", quantity=10
    )
    open_record = _universal_cf_context(
        1,
        {"bid": 105.0, "ask": 106.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    close_record = _universal_cf_context(
        2,
        {"bid": 99.0, "ask": 100.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 106.0, "ask": 107.0, "bid_quantity": 20, "ask_quantity": 20},
    )

    engine = UniversalEventBacktestEngine(100_000.0)
    result = engine.run_multi_leg([open_record, close_record], adapter)

    assert result.fill_count == 4
    assert engine.portfolio.positions["NSE:ABC"].quantity == 0
    assert engine.portfolio.positions["NFO:ABC-20261231"].quantity == 0
    assert result.realized_pnl == 100.0

def test_universal_cash_future_adapter_retries_failed_close_after_engine_rejection():
    from app.backtesting.execution import AtomicExecutionResult

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    open_context = _universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert len(tuple(adapter(open_context))) == 2
    adapter.on_atomic_execution(AtomicExecutionResult((), (), False, ""))

    close_context = _universal_cf_context(
        2,
        {"bid": 106.0, "ask": 107.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 102.0, "ask": 103.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert len(tuple(adapter(close_context))) == 2
    adapter.on_atomic_execution(AtomicExecutionResult((), (), True, "rejected"))

    retry_context = _universal_cf_context(
        3,
        {"bid": 106.0, "ask": 107.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 102.0, "ask": 103.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert len(tuple(adapter(retry_context))) == 2

def test_universal_cash_future_adapter_does_not_change_state_when_atomic_execution_rejects():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter
    from app.backtesting.execution import AtomicExecutionResult

    adapter = CashFutureUniversalMultiLegAdapter(quantity=10)
    context = _universal_cf_context(
        1,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert len(tuple(adapter(context))) == 2
    adapter.on_atomic_execution(AtomicExecutionResult((), (), True, "rejected"))

    retry_context = _universal_cf_context(
        2,
        {"bid": 100.0, "ask": 101.0, "bid_quantity": 20, "ask_quantity": 20},
        {"bid": 104.0, "ask": 105.0, "bid_quantity": 20, "ask_quantity": 20},
    )
    assert len(tuple(adapter(retry_context))) == 2


def test_universal_cash_future_trade_reporter_persists_two_exact_legs():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureTradeReporter
    from app.backtesting.contracts import AtomicTradeReportInput
    from app.backtesting.execution import AtomicExecutionResult, ExecutionResult, ExecutionSide, SimFill
    from app.backtesting.portfolio import TradeRecord

    class Writer:
        def __init__(self):
            self.trades = []

        def record_trades(self, trades):
            self.trades.extend(trades)
            return len(trades)

    writer = Writer()
    reporter = CashFutureTradeReporter(writer)

    open_fills = (
        SimFill("OPEN:CASH", "NSE:ABC", ExecutionSide.BUY, 10, 101.0, 1),
        SimFill("OPEN:FUTURE", "NFO:ABC-OLD", ExecutionSide.SELL, 10, 104.0, 1),
    )
    reporter.record_atomic_trade(AtomicTradeReportInput(
        AtomicExecutionResult(
            fills=open_fills,
            leg_results=(
                ExecutionResult((open_fills[0],), 0, reference_prices=(101.0,)),
                ExecutionResult((open_fills[1],), 0, reference_prices=(104.0,)),
            ),
        ),
        (
            TradeRecord("OPEN:CASH", "NSE:ABC", ExecutionSide.BUY, 10, 101.0, 1010.0, 1.0, 0.0, 989.0, 100000.0, 1),
            TradeRecord("OPEN:FUTURE", "NFO:ABC-OLD", ExecutionSide.SELL, 10, 104.0, 1040.0, 1.0, 0.0, 1090.0, 100000.0, 1),
        ),
    ))
    assert writer.trades == []

    close_fills = (
        SimFill("CLOSE:CASH", "NSE:ABC", ExecutionSide.SELL, 10, 106.0, 2),
        SimFill("CLOSE:FUTURE", "NFO:ABC-OLD", ExecutionSide.BUY, 10, 103.0, 2),
    )
    reporter.record_atomic_trade(AtomicTradeReportInput(
        AtomicExecutionResult(
            fills=close_fills,
            leg_results=(
                ExecutionResult((close_fills[0],), 0, reference_prices=(106.0,)),
                ExecutionResult((close_fills[1],), 0, reference_prices=(103.0,)),
            ),
        ),
        (
            TradeRecord("CLOSE:CASH", "NSE:ABC", ExecutionSide.SELL, 10, 106.0, 1060.0, 1.0, 50.0, 1100.0, 100050.0, 2),
            TradeRecord("CLOSE:FUTURE", "NFO:ABC-OLD", ExecutionSide.BUY, 10, 103.0, 1030.0, 1.0, 10.0, 70.0, 100060.0, 2),
        ),
    ))

    assert len(writer.trades) == 2
    by_leg = {trade.leg: trade for trade in writer.trades}
    assert by_leg["CASH"].instrument == "NSE:ABC"
    assert by_leg["CASH"].entry_price == 101.0
    assert by_leg["CASH"].exit_price == 106.0
    assert by_leg["CASH"].gross_pnl == 50.0
    assert by_leg["CASH"].fees == 2.0
    assert by_leg["CASH"].net_pnl == 48.0
    assert by_leg["CASH"].metadata["pricing_model"] == "EXECUTABLE_EDGE"
    assert by_leg["CASH"].metadata["entry_edge"] == 3.0
    assert by_leg["CASH"].metadata["exit_edge"] == 3.0
    assert by_leg["FUTURE"].metadata["entry_edge"] == 3.0
    assert by_leg["FUTURE"].metadata["exit_edge"] == 3.0
    assert by_leg["CASH"].metadata["lifecycle_trade_id"] == by_leg["FUTURE"].metadata["lifecycle_trade_id"]
    assert by_leg["CASH"].trade_id.endswith(":CASH")
    assert by_leg["FUTURE"].instrument == "NFO:ABC-OLD"
    assert by_leg["FUTURE"].entry_price == 104.0
    assert by_leg["FUTURE"].exit_price == 103.0
    assert by_leg["FUTURE"].gross_pnl == 10.0
    assert by_leg["FUTURE"].net_pnl == 8.0


def test_universal_cash_future_trade_reporter_persists_short_direction_edges():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureTradeReporter
    from app.backtesting.contracts import AtomicTradeReportInput
    from app.backtesting.execution import AtomicExecutionResult, ExecutionResult, ExecutionSide, SimFill
    from app.backtesting.portfolio import TradeRecord

    class Writer:
        def __init__(self):
            self.trades = []

        def record_trades(self, trades):
            self.trades.extend(trades)
            return len(trades)

    writer = Writer()
    reporter = CashFutureTradeReporter(writer)

    open_fills = (
        SimFill("OPEN:CASH", "NSE:ABC", ExecutionSide.SELL, 10, 105.0, 1),
        SimFill("OPEN:FUTURE", "NFO:ABC-OLD", ExecutionSide.BUY, 10, 101.0, 1),
    )
    reporter.record_atomic_trade(AtomicTradeReportInput(
        AtomicExecutionResult(
            fills=open_fills,
            leg_results=(
                ExecutionResult((open_fills[0],), 0, reference_prices=(105.0,)),
                ExecutionResult((open_fills[1],), 0, reference_prices=(101.0,)),
            ),
        ),
        (
            TradeRecord("OPEN:CASH", "NSE:ABC", ExecutionSide.SELL, 10, 105.0, 1050.0, 0.0, 0.0, 0.0, 100000.0, 1),
            TradeRecord("OPEN:FUTURE", "NFO:ABC-OLD", ExecutionSide.BUY, 10, 101.0, 1010.0, 0.0, 0.0, 0.0, 100000.0, 1),
        ),
    ))

    close_fills = (
        SimFill("CLOSE:CASH", "NSE:ABC", ExecutionSide.BUY, 10, 100.0, 2),
        SimFill("CLOSE:FUTURE", "NFO:ABC-OLD", ExecutionSide.SELL, 10, 106.0, 2),
    )
    reporter.record_atomic_trade(AtomicTradeReportInput(
        AtomicExecutionResult(
            fills=close_fills,
            leg_results=(
                ExecutionResult((close_fills[0],), 0, reference_prices=(100.0,)),
                ExecutionResult((close_fills[1],), 0, reference_prices=(106.0,)),
            ),
        ),
        (
            TradeRecord("CLOSE:CASH", "NSE:ABC", ExecutionSide.BUY, 10, 100.0, 1000.0, 0.0, 50.0, 0.0, 100000.0, 2),
            TradeRecord("CLOSE:FUTURE", "NFO:ABC-OLD", ExecutionSide.SELL, 10, 106.0, 1060.0, 0.0, 60.0, 0.0, 100000.0, 2),
        ),
    ))

    assert len(writer.trades) == 2
    assert {trade.metadata["entry_edge"] for trade in writer.trades} == {4.0}
    assert {trade.metadata["exit_edge"] for trade in writer.trades} == {6.0}

def test_universal_cash_future_trade_reporter_does_not_double_count_execution_slippage():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureTradeReporter
    from app.backtesting.contracts import AtomicTradeReportInput
    from app.backtesting.execution import AtomicExecutionResult, ExecutionResult, ExecutionSide, SimFill
    from app.backtesting.portfolio import TradeRecord

    class Writer:
        def __init__(self):
            self.trades = []

        def record_trades(self, trades):
            self.trades.extend(trades)
            return len(trades)

    writer = Writer()
    reporter = CashFutureTradeReporter(writer)

    open_fills = (
        SimFill("OPEN:CASH", "NSE:ABC", ExecutionSide.BUY, 10, 102.0, 1),
        SimFill("OPEN:FUTURE", "NFO:ABC-OLD", ExecutionSide.SELL, 10, 104.0, 1),
    )
    reporter.record_atomic_trade(AtomicTradeReportInput(
        AtomicExecutionResult(
            fills=open_fills,
            leg_results=(
                ExecutionResult((open_fills[0],), 0, reference_prices=(101.0,)),
                ExecutionResult((open_fills[1],), 0, reference_prices=(104.0,)),
            ),
        ),
        (
            TradeRecord("OPEN:CASH", "NSE:ABC", ExecutionSide.BUY, 10, 102.0, 1020.0, 1.0, 0.0, 0.0, 100000.0, 1),
            TradeRecord("OPEN:FUTURE", "NFO:ABC-OLD", ExecutionSide.SELL, 10, 104.0, 1040.0, 1.0, 0.0, 0.0, 100000.0, 1),
        ),
    ))

    close_fills = (
        SimFill("CLOSE:CASH", "NSE:ABC", ExecutionSide.SELL, 10, 106.0, 2),
        SimFill("CLOSE:FUTURE", "NFO:ABC-OLD", ExecutionSide.BUY, 10, 103.0, 2),
    )
    reporter.record_atomic_trade(AtomicTradeReportInput(
        AtomicExecutionResult(
            fills=close_fills,
            leg_results=(
                ExecutionResult((close_fills[0],), 0, reference_prices=(107.0,)),
                ExecutionResult((close_fills[1],), 0, reference_prices=(102.0,)),
            ),
        ),
        (
            TradeRecord("CLOSE:CASH", "NSE:ABC", ExecutionSide.SELL, 10, 106.0, 1060.0, 1.0, 40.0, 0.0, 100000.0, 2),
            TradeRecord("CLOSE:FUTURE", "NFO:ABC-OLD", ExecutionSide.BUY, 10, 103.0, 1030.0, 1.0, 10.0, 0.0, 100000.0, 2),
        ),
    ))

    by_leg = {trade.leg: trade for trade in writer.trades}
    assert by_leg["CASH"].slippage == 20.0
    assert by_leg["FUTURE"].slippage == 0.0
    assert by_leg["CASH"].gross_pnl == 40.0
    assert by_leg["FUTURE"].gross_pnl == 10.0
    assert by_leg["CASH"].fees == 2.0
    assert by_leg["FUTURE"].fees == 2.0
    assert by_leg["CASH"].net_pnl == 38.0
    assert by_leg["FUTURE"].net_pnl == 8.0

def test_universal_cash_future_strategy_state_round_trips_contract_identity():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter

    adapter = CashFutureUniversalMultiLegAdapter()
    adapter.set_state({
        "open": True,
        "pending_open": None,
        "open_cash_instrument": "CASH-OLD",
        "open_future_instrument": "FUT-OLD",
        "pending_cash_instrument": None,
        "pending_future_instrument": None,
    })

    state = adapter.get_state()
    assert state["open"] is True
    assert state["open_cash_instrument"] == "CASH-OLD"
    assert state["open_future_instrument"] == "FUT-OLD"

    restored = CashFutureUniversalMultiLegAdapter()
    restored.set_state(state)
    assert restored.get_state() == state


def test_universal_cash_future_strategy_rejects_open_state_without_contract_identity():
    from app.backtesting.arbitrage_strategy_adapters import CashFutureUniversalMultiLegAdapter

    adapter = CashFutureUniversalMultiLegAdapter()
    try:
        adapter.set_state({
            "open": True,
            "pending_open": None,
            "open_cash_instrument": None,
            "open_future_instrument": "FUT-OLD",
            "pending_cash_instrument": None,
            "pending_future_instrument": None,
        })
    except ValueError as exc:
        assert "requires both contract identities" in str(exc)
    else:
        raise AssertionError("expected invalid checkpoint state to be rejected")

