import inspect

from fastapi import HTTPException

from app.scanner import auto_routes


def test_cash_future_auto_route_is_retired_without_query_or_db_parameters():
    parameters = inspect.signature(auto_routes.cash_future_live_auto_scanner).parameters
    assert not parameters
    try:
        auto_routes.cash_future_live_auto_scanner()
    except HTTPException as exc:
        assert exc.status_code == 410
    else:
        raise AssertionError("retired Cash-Future auto route must return HTTP 410")
