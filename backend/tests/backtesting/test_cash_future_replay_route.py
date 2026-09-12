from app.backtesting.cash_future_strategy_routes import router


STRATEGY_PREFIX = "/api/v1/backtesting/cash-future"


def _flatten_routes(routes):
    flattened = []
    for route in routes:
        nested = getattr(route, "routes", None)
        if nested is not None:
            flattened.extend(_flatten_routes(nested))
        else:
            flattened.append(route)
    return flattened


def _strategy_routes():
    return _flatten_routes(router.routes)


def test_cash_future_replay_route_is_wired_under_strategy_api():
    paths = {f"{STRATEGY_PREFIX}{route.path}" for route in _strategy_routes()}
    assert f"{STRATEGY_PREFIX}/replay" in paths


def test_cash_future_replay_route_accepts_real_resolution_controls():
    route = next(route for route in _strategy_routes() if route.path == "/replay")
    query_names = {parameter.name for parameter in route.dependant.query_params}
    assert {"trading_date", "symbol", "contract_month", "timeframe", "mode", "source", "spot_instrument", "exchange"} <= query_names
