from datetime import datetime, timezone

import pytest

from app.execution.confirmation import ConfirmationGateway


def test_confirmation_requires_explicit_approval_and_is_single_use():
    gateway = ConfirmationGateway(ttl_seconds=30)
    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    request = gateway.create("order-1", now)

    assert gateway.confirm("order-1", now) is True
    assert gateway.confirm("order-1", now) is False
    assert request.expires_at > request.created_at


def test_confirmation_expires_at_ttl_boundary():
    gateway = ConfirmationGateway(ttl_seconds=30)
    now = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    gateway.create("order-2", now)

    assert gateway.confirm("order-2", datetime(2026, 9, 2, 10, 0, 30, tzinfo=timezone.utc)) is False


def test_confirmation_validates_inputs():
    with pytest.raises(ValueError):
        ConfirmationGateway(ttl_seconds=0)
    gateway = ConfirmationGateway()
    with pytest.raises(ValueError):
        gateway.create("")
