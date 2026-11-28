from __future__ import annotations

import pytest

import app.market_data.common_strategy_feed as shared_feed


class _Manager:
    def __init__(self, snapshot):
        self.state = dict(snapshot)
        self.close_calls = 0
        self._closed = False

    def close(self):
        self.close_calls += 1
        self._closed = True

    def snapshot(self):
        return dict(self.state)


@pytest.mark.parametrize(
    "retirement_state",
    [
        {"retired_transport_count": 1},
        {"retired_transport_close_start_pending_count": 1},
        {"retired_transport_count": 1, "retired_transport_watch_pending_count": 1},
        {"socket_operation_worker_count": 1},
    ],
)
def test_shared_manager_is_not_released_while_transport_retirement_is_unresolved(
    monkeypatch, retirement_state
):
    manager = _Manager(retirement_state)
    monkeypatch.setattr(shared_feed, "_SHARED_MANAGER", manager)
    monkeypatch.setattr(shared_feed, "_SHARED_AUTH", object())
    monkeypatch.setattr(shared_feed, "_SHARED_MANAGER_CLOSING", False)

    with pytest.raises(RuntimeError, match="shutdown is incomplete"):
        shared_feed.close_shared_common_manager()

    assert manager.close_calls == 1
    assert shared_feed._SHARED_MANAGER is manager
    assert shared_feed._SHARED_MANAGER_CLOSING is False
    # The getter must neither reuse the closed manager nor create a second one.
    with pytest.raises(RuntimeError, match="not fully retired"):
        shared_feed.shared_common_manager()
    assert shared_feed._SHARED_MANAGER is manager


def test_shared_manager_is_released_after_all_retired_transports_are_gone(monkeypatch):
    manager = _Manager({
        "retired_transport_count": 0,
        "retired_transport_close_start_pending_count": 0,
    })
    monkeypatch.setattr(shared_feed, "_SHARED_MANAGER", manager)
    monkeypatch.setattr(shared_feed, "_SHARED_AUTH", object())
    monkeypatch.setattr(shared_feed, "_SHARED_MANAGER_CLOSING", False)

    shared_feed.close_shared_common_manager()

    assert manager.close_calls == 1
    assert shared_feed._SHARED_MANAGER is None
    assert shared_feed._SHARED_AUTH is None
    assert shared_feed._SHARED_MANAGER_CLOSING is False
