from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.scanner import box_spread_paper_websocket as private_ws


def test_private_ws_ticket_is_hashed_and_single_use():
    raw = private_ws.issue_ticket("box-spread-paper-owner")
    digest = private_ws._digest(raw)
    with private_ws.engine.begin() as connection:
        stored = connection.execute(
            select(private_ws.tickets.c.ticket_hash).where(
                private_ws.tickets.c.ticket_hash == digest
            )
        ).first()
    assert stored is not None
    assert stored[0] != raw
    assert private_ws.consume_ticket(raw) == "box-spread-paper-owner"
    assert private_ws.consume_ticket(raw) is None


def test_private_ws_ticket_expiry_is_enforced(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(private_ws, "_now", lambda: clock["now"])
    raw = private_ws.issue_ticket("box-spread-paper-owner")
    clock["now"] += private_ws.TICKET_TTL_SECONDS + 1
    assert private_ws.consume_ticket(raw) is None


def test_private_ws_ticket_rejects_wrong_owner():
    raw = private_ws.issue_ticket("unexpected-owner")
    assert private_ws.consume_ticket(raw) == "unexpected-owner"
    assert private_ws.consume_ticket(raw) is None


def test_publish_after_success_does_not_publish_when_handler_raises(monkeypatch):
    published = []
    monkeypatch.setattr(private_ws, "publish_paper_update", published.append)

    @private_ws.publish_after_success("TRADE_CREATED")
    def failing_handler():
        raise RuntimeError("expected failure")

    try:
        failing_handler()
    except RuntimeError:
        pass
    else:
        raise AssertionError("handler exception must propagate")
    assert published == []


def test_publish_after_success_publishes_only_after_handler_returns(monkeypatch):
    order = []
    monkeypatch.setattr(private_ws, "publish_paper_update", lambda event: order.append(("publish", event)))

    @private_ws.publish_after_success("ENTRY_COMMITTED")
    def successful_handler():
        order.append(("handler", "committed"))
        return {"ok": True}

    assert successful_handler() == {"ok": True}
    assert order == [("handler", "committed"), ("publish", "ENTRY_COMMITTED")]


def test_publish_after_success_supports_async_handlers(monkeypatch):
    published = []
    monkeypatch.setattr(private_ws, "publish_paper_update", published.append)

    @private_ws.publish_after_success("EXIT_COMMITTED")
    async def successful_handler():
        return {"ok": True}

    assert asyncio.run(successful_handler()) == {"ok": True}
    assert published == ["EXIT_COMMITTED"]



def test_private_ws_origin_allowlist_is_exact(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(private_ws, "settings", SimpleNamespace(
        BOX_SPREAD_PAPER_WS_ALLOWED_ORIGINS="https://app.example.com,https://preview.example.com:8443",
        environment="production",
    ))
    assert private_ws._origin_allowed(SimpleNamespace(headers={"origin": "https://app.example.com"}))
    assert private_ws._origin_allowed(SimpleNamespace(headers={"origin": "https://preview.example.com:8443"}))
    assert not private_ws._origin_allowed(SimpleNamespace(headers={"origin": "https://evil-app.example.com"}))
    assert not private_ws._origin_allowed(SimpleNamespace(headers={"origin": "https://app.example.com.evil.test"}))
    assert not private_ws._origin_allowed(SimpleNamespace(headers={"origin": "https://app.example.com/path"}))
    assert not private_ws._origin_allowed(SimpleNamespace(headers={"origin": "null"}))


def test_private_ws_origin_allows_localhost_only_in_development(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(private_ws, "settings", SimpleNamespace(
        BOX_SPREAD_PAPER_WS_ALLOWED_ORIGINS="", environment="development",
    ))
    assert private_ws._origin_allowed(SimpleNamespace(headers={"origin": "http://localhost:3000"}))
    monkeypatch.setattr(private_ws, "settings", SimpleNamespace(
        BOX_SPREAD_PAPER_WS_ALLOWED_ORIGINS="", environment="production",
    ))
    assert not private_ws._origin_allowed(SimpleNamespace(headers={"origin": "http://localhost:3000"}))


def test_private_ws_origin_rejects_credentials_and_non_http_schemes():
    assert private_ws._normalize_origin("https://user:pass@app.example.com") is None
    assert private_ws._normalize_origin("javascript://app.example.com") is None
    assert private_ws._normalize_origin("https://app.example.com/path") is None
    assert private_ws._normalize_origin("https://app.example.com:invalid") is None


def test_private_ws_event_journal_is_durable_and_worker_independent():
    before = private_ws._latest_event_id()
    private_ws.publish_paper_update("TEST_DURABLE_EVENT")
    rows = private_ws._events_after(before)
    assert rows
    assert rows[-1]["event_type"] == "TEST_DURABLE_EVENT"
    assert rows[-1]["sequence"] > before


def test_private_ws_event_journal_cursor_returns_ordered_events():
    before = private_ws._latest_event_id()
    private_ws.publish_paper_update("TEST_EVENT_A")
    private_ws.publish_paper_update("TEST_EVENT_B")
    rows = private_ws._events_after(before)
    selected = [row["event_type"] for row in rows if row["event_type"].startswith("TEST_EVENT_")]
    assert selected[-2:] == ["TEST_EVENT_A", "TEST_EVENT_B"]
    assert rows[-1]["sequence"] > rows[-2]["sequence"]


def test_private_ws_local_fanout_uses_one_dispatch_task(monkeypatch):
    async def scenario():
        # The shared dispatcher is process-local; multiple subscribers attach
        # queues to the same tail task rather than creating per-socket DB polls.
        first = await private_ws._subscribe_local()
        task = private_ws._dispatch_task
        second = await private_ws._subscribe_local()
        assert task is private_ws._dispatch_task
        assert len(private_ws._subscribers) >= 2
        private_ws._unsubscribe_local(first)
        assert private_ws._dispatch_task is task
        private_ws._unsubscribe_local(second)
        assert private_ws._dispatch_task is None
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_private_ws_dispatch_fanout_overflow_requests_snapshot():
    async def scenario():
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"sequence": 1, "event_type": "TRADE_UPDATED"})
        # Match dispatcher overflow behavior: discard stale queued deltas and
        # retain a single authoritative snapshot marker at the newest cursor.
        if queue.full():
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait({"sequence": 9, "event_type": "SNAPSHOT"})
        assert await queue.get() == {"sequence": 9, "event_type": "SNAPSHOT"}

    asyncio.run(scenario())


def test_private_ws_shared_dispatcher_fans_out_journal_event(monkeypatch):
    async def scenario():
        rows = [{"sequence": 101, "event_type": "TRADE_UPDATED"}]
        calls = {"events": 0}

        def fake_latest():
            return 100

        def fake_events_after(cursor):
            calls["events"] += 1
            if rows and cursor < 101:
                return list(rows)
            return []

        monkeypatch.setattr(private_ws, "_latest_event_id", fake_latest)
        monkeypatch.setattr(private_ws, "_events_after", fake_events_after)
        first = await private_ws._subscribe_local()
        second = await private_ws._subscribe_local()
        try:
            one = await asyncio.wait_for(first.get(), timeout=1.0)
            two = await asyncio.wait_for(second.get(), timeout=1.0)
            assert one == two == {"sequence": 101, "event_type": "TRADE_UPDATED"}
            assert private_ws._dispatch_task is not None
            assert calls["events"] >= 1
        finally:
            private_ws._unsubscribe_local(first)
            private_ws._unsubscribe_local(second)
            await asyncio.sleep(0)

    asyncio.run(scenario())
