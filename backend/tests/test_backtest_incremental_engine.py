from datetime import datetime, timedelta

from app.algo.strategy import Strategy, StrategyRule
from app.backtesting.engine import BacktestConfig, BacktestEngine


def _always(value: bool):
    return lambda _context: value


def _strategy(name: str, value: bool) -> Strategy:
    return Strategy(name=name, rules=(StrategyRule(name=name, rule=_always(value)),))


def _candles(count: int):
    start = datetime(2026, 1, 1)
    for index in range(count):
        yield {"timestamp": start + timedelta(minutes=index), "close": 100.0 + index}


def test_incremental_engine_never_materializes_complete_trade_ledger():
    engine = BacktestEngine(BacktestConfig(quantity=1.0))
    chunks = []
    max_chunk = 0

    def persist(chunk, sequence):
        nonlocal max_chunk
        max_chunk = max(max_chunk, len(chunk))
        chunks.append((sequence, tuple(chunk)))

    result = engine.run_incremental(
        _candles(40),
        _strategy("entry", True),
        _strategy("exit", True),
        persist_chunk=persist,
        chunk_size=3,
    )

    # The first candle opens; every following candle closes immediately, so
    # 20 trades are produced and persisted in bounded chunks.
    assert len(chunks) == 7
    assert max_chunk <= 3
    assert result.trades == ()
    assert result.net_pnl > 0
    assert sum(len(chunk) for _, chunk in chunks) == 20
    assert [sequence for sequence, _ in chunks] == list(range(7))


def test_incremental_engine_rejects_unbounded_or_invalid_chunk_size():
    engine = BacktestEngine()
    entry = _strategy("entry", True)
    exit_ = _strategy("exit", True)

    try:
        engine.run_incremental(_candles(2), entry, exit_, persist_chunk=lambda *_: None, chunk_size=0)
    except ValueError as exc:
        assert str(exc) == "chunk_size must be positive"
    else:
        raise AssertionError("expected chunk_size validation")


def test_incremental_engine_consumes_generator_and_preserves_summary():
    engine = BacktestEngine(BacktestConfig(initial_capital=1_000.0, quantity=1.0))
    calls = []

    def candles():
        for index in range(6):
            calls.append(index)
            yield {"timestamp": datetime(2026, 1, 1) + timedelta(days=index), "close": 100 + index}

    result = engine.run_incremental(
        candles(),
        _strategy("entry", True),
        _strategy("exit", True),
        persist_chunk=lambda chunk, sequence: None,
        chunk_size=2,
    )

    assert calls == list(range(6))
    assert result.trades == ()
    assert result.net_pnl == 5.0
    assert result.win_rate == 1.0
    assert result.expectancy == 1.0
