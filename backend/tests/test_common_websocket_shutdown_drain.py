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


def test_retired_transport_snapshot_includes_transport_identity(monkeypatch):
    from threading import Event, Thread
    from app.market_data.common_websocket import SocketGroup

    manager = CommonWebSocketManager()
    release = Event()
    transport = Thread(target=release.wait, name="test-angel-transport", daemon=True)
    transport.start()

    class Socket:
        _transport_thread = transport

    monkeypatch.setattr(manager, "_start_retired_transport_close", lambda _key, _record: True)
    manager._close_socket_tracked(
        Socket(), group=SocketGroup(mode=3, shard=1), generation=7,
        reason="test_retirement", timeout=0.1,
    )
    retired = manager.snapshot()["retired_transports"][0]
    assert retired["group"] == "3:1"
    assert retired["generation"] == 7
    assert retired["transport_thread_name"] == "test-angel-transport"
    assert retired["transport_thread_ident"] == transport.ident
    assert retired["transport_thread_alive_at_retirement"] is True

    release.set()
    transport.join(timeout=1.0)
