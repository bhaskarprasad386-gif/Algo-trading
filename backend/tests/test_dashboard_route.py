from fastapi import HTTPException
import pytest

from app.main import dashboard


def test_legacy_dashboard_route_is_retired():
    with pytest.raises(HTTPException) as exc:
        dashboard()
    assert exc.value.status_code == 404
    assert exc.value.detail == "Dashboard page retired"
