from app.backtesting.cash_future_strategy_routes import router


def test_cash_future_replay_route_is_wired_under_strategy_api():
    paths = {route.path for route in router.routes}
    assert "/api/v1/backtesting/cash-future/replay" in paths


def test_cash_future_replay_route_accepts_real_resolution_controls():
    route = next(route for route in router.routes if route.path == "/api/v1/backtesting/cash-future/replay")
    query_names = {parameter.name for parameter in route.dependant.query_params}
    assert {"trading_date", "symbol", "contract_month", "timeframe", "mode", "source", "spot_instrument", "exchange"} <= query_names
