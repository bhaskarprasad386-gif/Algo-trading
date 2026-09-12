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
