from app.backtesting.cash_future_strategy_routes import router


STRATEGY_PREFIX = "/api/v1/backtesting/cash-future"


def _flatten_routes(routes):
    flattened = []
    for route in routes:
        # Normal APIRoute that already has a path
        if hasattr(route, "path") and getattr(route, "path", None) is not None:
            flattened.append(route)
            continue

        # FastAPI ≥0.141 _IncludedRouter exposes original_router
        original = getattr(route, "original_router", None)
        if original is not None and hasattr(original, "routes"):
            flattened.extend(_flatten_routes(original.routes))
            continue

        # Older nested / Mount styles
        nested = getattr(route, "routes", None)
        if nested is None:
            nested_router = getattr(route, "router", None)
            nested = getattr(nested_router, "routes", None)
        if nested is not None:
            flattened.extend(_flatten_routes(nested))
    return flattened


def _strategy_routes():
    return _flatten_routes(router.routes)


def _normalized_paths():
    """Collect full paths without double-prefixing."""
    paths = set()
    for route in _strategy_routes():
        path = route.path or ""
        if path.startswith(STRATEGY_PREFIX):
            paths.add(path)
        else:
            paths.add(f"{STRATEGY_PREFIX}{path}")
    return paths


def test_cash_future_replay_route_is_wired_under_strategy_api():
    paths = _normalized_paths()
    assert f"{STRATEGY_PREFIX}/replay" in paths


def test_cash_future_replay_route_accepts_real_resolution_controls():
    route = next(
        (
            r
            for r in _strategy_routes()
            if (r.path or "").endswith("/replay") or r.path == "/replay"
        ),
        None,
    )
    assert route is not None, "replay route not found under strategy router"
    query_names = {parameter.name for parameter in route.dependant.query_params}
    assert {
        "trading_date",
        "symbol",
        "contract_month",
        "timeframe",
        "mode",
        "source",
        "spot_instrument",
        "exchange",
    } <= query_names



def test_cash_future_replay_returns_full_paired_series(monkeypatch):
    from datetime import date, datetime, timezone
    from app.backtesting import cash_future_replay_routes as module
    from app.scanner.cash_future_history import CashFutureHistoryPoint

    class FakeCatalog:
        def __init__(self, path):
            self.path = path
        def close(self):
            pass

    class FakeContracts:
        def __init__(self, path):
            self.path = path
        def close(self):
            pass

    points = [
        CashFutureHistoryPoint(
            timestamp=datetime(2026, 9, 10, 9, 15, tzinfo=timezone.utc),
            symbol="SBIN",
            contract_month="2026-09",
            cash_price=800.0,
            future_price=812.0,
            gap=12.0,
            gap_pct=1.5,
            lot_size=750,
            margin_required=150000.0,
            volume=12500.0,
            oi=98765.0,
            cash_bid=799.5,
            cash_ask=800.5,
            future_bid=811.5,
            future_ask=812.5,
            charges=37.5,
            funding_cost=12.25,
        ),
        CashFutureHistoryPoint(
            timestamp=datetime(2026, 9, 10, 9, 16, tzinfo=timezone.utc),
            symbol="SBIN",
            contract_month="2026-09",
            cash_price=801.0,
            future_price=814.0,
            gap=13.0,
            gap_pct=13.0 / 801.0 * 100.0,
            lot_size=750,
            margin_required=150000.0,
            volume=13000.0,
            oi=99000.0,
            cash_bid=800.5,
            cash_ask=801.5,
            future_bid=813.5,
            future_ask=814.5,
            charges=37.5,
            funding_cost=12.25,
        ),
    ]

    class FakeLoader:
        def __init__(self, catalog, contracts):
            pass
        def iter_points(self, selection):
            assert selection.start_date == date(2026, 9, 10)
            assert selection.end_date == date(2026, 9, 10)
            assert selection.contract_month == "2026-09"
            assert selection.timeframe == "1m"
            assert selection.mode == "CURRENT"
            yield from points

    monkeypatch.setattr(module, "HistoricalCatalog", FakeCatalog)
    monkeypatch.setattr(module, "ContractMasterCatalog", FakeContracts)
    monkeypatch.setattr(module, "CashFutureHistoricalLoader", FakeLoader)

    result = module.cash_future_replay(
        trading_date=date(2026, 9, 10),
        symbol="sbin",
        contract_month="2026-09",
        timeframe="1m",
        mode="CURRENT",
        source="angelone",
        spot_instrument="NSE:3045:SBIN",
        exchange="NSE",
    )

    assert result["status"] == "success"
    assert result["symbol"] == "SBIN"
    assert result["count"] == 2
    assert result["contracts_seen"] == ["2026-09"]
    assert result["source_min_interval_seconds"] == 60.0
    assert result["available_replay_intervals"] == ["1m", "5m", "15m", "30m"]

    first, second = result["series"]
    assert first["timestamp"] < second["timestamp"]
    assert first["cash_price"] == 800.0
    assert first["future_price"] == 812.0
    assert first["gap"] == 12.0
    assert first["lot_size"] == 750
    assert first["volume"] == 12500.0
    assert first["oi"] == 98765.0
    assert first["cash_bid"] == 799.5
    assert first["cash_ask"] == 800.5
    assert first["future_bid"] == 811.5
    assert first["future_ask"] == 812.5
    assert first["charges"] == 37.5
    assert first["funding_cost"] == 12.25
