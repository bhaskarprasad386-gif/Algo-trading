from app.market_data import common_strategy_feed


def test_process_shared_manager_uses_bounded_async_callback_dispatch(monkeypatch):
    monkeypatch.setattr(common_strategy_feed, "_SHARED_MANAGER", None)
    monkeypatch.setattr(common_strategy_feed, "_SHARED_AUTH", None)

    manager = common_strategy_feed.shared_common_manager(
        auth=object(),
        socket_factory=lambda: object(),
    )

    assert manager._async_callback_dispatch is True
    assert manager._callback_dispatch_capacity == 2048
    assert manager.snapshot()["async_callback_dispatch"] is True

    manager.close()
    monkeypatch.setattr(common_strategy_feed, "_SHARED_MANAGER", None)
    monkeypatch.setattr(common_strategy_feed, "_SHARED_AUTH", None)
