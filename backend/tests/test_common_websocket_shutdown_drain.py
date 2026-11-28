from __future__ import annotations

import time

from app.market_data.common_websocket import CommonWebSocketManager


def test_shutdown_drain_waits_for_near_boundary_retirement_to_finish(monkeypatch):
    manager = CommonWebSocketManager()
    snapshots = iter([
        {"retired_transport_count": 1},
        {"retired_transport_count": 1},
        {"retired_transport_count": 0},
        {"retired_transport_count": 0},
    ])
    retries = {"close": 0, "watch": 0}

    monkeypatch.setattr(manager, "snapshot", lambda: next(snapshots))
    monkeypatch.setattr(
        manager,
        "_retry_pending_retired_transport_closes",
        lambda **_kwargs: retries.__setitem__("close", retries["close"] + 1),
    )
    monkeypatch.setattr(
        manager,
        "_retry_pending_retired_transport_watchers",
        lambda: retries.__setitem__("watch", retries["watch"] + 1),
    )

    assert manager._drain_shutdown_retirements(time.monotonic() + 1.0) is True
    assert retries == {"close": 3, "watch": 3}


def test_shutdown_drain_times_out_without_releasing_retirement_fence(monkeypatch):
    manager = CommonWebSocketManager()
    monkeypatch.setattr(
        manager,
        "snapshot",
        lambda: {"retired_transport_count": 1},
    )
    monkeypatch.setattr(manager, "_retry_pending_retired_transport_closes", lambda **_kwargs: None)
    monkeypatch.setattr(manager, "_retry_pending_retired_transport_watchers", lambda: None)

    assert manager._drain_shutdown_retirements(time.monotonic() + 0.02) is False
    assert manager.snapshot()["retired_transport_count"] == 1
