from types import SimpleNamespace
import asyncio

from app.scanner import page_push_websocket as push


def test_page_channels_cover_all_scanner_pages():
    expected = {
        "cash-future", "box-spread", "calendar-spread",
        "synthetic-arbitrage", "iv-skew-flip",
        "credit-butterfly-condor-weekly",
        "credit-butterfly-condor-monthly", "weekly-swing",
    }
    assert expected.issubset(push.PAGE_CHANNELS)


def test_page_snapshot_frame_uses_explicit_durable_sequence():
    frame = push._snapshot_frame("box-spread", {"rows": []}, sequence=17)
    assert frame["type"] == "page_update"
    assert frame["page"] == "box-spread"
    assert frame["sequence"] == 17
    assert frame["event"] == "SNAPSHOT"
    assert frame["data"] == {"rows": []}


def test_snapshot_provider_is_read_only_and_page_scoped():
    observed = []
    push.configure_snapshot_provider(
        "credit-butterfly-condor-weekly",
        lambda: observed.append("read") or {"rows": []},
    )
    assert push._snapshot("credit-butterfly-condor-weekly") == {"rows": []}
    assert observed == ["read"]
    push.configure_snapshot_provider("credit-butterfly-condor-weekly", None)
    try:
        push.configure_snapshot_provider("unknown-page", lambda: {})
    except ValueError as exc:
        assert "unknown page" in str(exc)
    else:
        raise AssertionError("unknown page must be rejected")


def test_origin_policy_rejects_untrusted_hosts():
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "https://algo-trading.vercel.app"}))
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "http://localhost:3000"}))
    assert not push._origin_allowed(SimpleNamespace(headers={"origin": "https://evil.example"}))
    assert push._origin_allowed(SimpleNamespace(headers={}))


def test_page_event_and_sequence_journals_are_durable_tables():
    assert push.events.name == "algo_scanner_page_ws_events"
    assert "page_sequence" in push.events.c
    assert push.sequences.name == "algo_scanner_page_ws_sequences"
    assert "sequence" in push.sequences.c


def test_websocket_subscribe_publish_and_resync_round_trip(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    journal = []
    page_sequences = {"box-spread": 0}
    provider_calls = []

    def fake_latest_event_id():
        return journal[-1]["id"] if journal else 0

    def fake_events_after(cursor, limit=100):
        return [row for row in journal if row["id"] > cursor][:limit + 1]

    def fake_latest_page_sequence(page):
        return page_sequences.get(page, 0)

    def fake_publish(page, event, data=None):
        page_sequences[page] = page_sequences.get(page, 0) + 1
        journal.append({
            "id": len(journal) + 1, "page": page,
            "sequence": page_sequences[page], "event": event, "data": data or {},
        })

    monkeypatch.setattr(push, "_latest_event_id", fake_latest_event_id)
    monkeypatch.setattr(push, "_events_after", fake_events_after)
    monkeypatch.setattr(push, "_latest_page_sequence", fake_latest_page_sequence)
    monkeypatch.setattr(push, "publish_page_update", fake_publish)
    monkeypatch.setattr(push, "_subscribers", set())
    monkeypatch.setattr(push, "_dispatch_task", None)
    monkeypatch.setattr(push, "_dispatch_loop", None)
    monkeypatch.setattr(push, "_dispatch_ready", None)

    app = FastAPI()
    app.include_router(push.router)
    page = "box-spread"
    push.configure_snapshot_provider(
        page,
        lambda: provider_calls.append("snapshot") or {"opportunities": [{"id": "initial"}]},
    )
    try:
        with TestClient(app).websocket_connect(
            "/ws/page-updates",
            headers={"origin": "https://algo-trading.vercel.app"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "connected"
            websocket.send_json({"action": "subscribe", "page": page})
            initial = websocket.receive_json()
            assert initial["event"] == "SNAPSHOT"
            assert initial["sequence"] == 0
            assert initial["data"]["opportunities"][0]["id"] == "initial"

            push.publish_page_update(page, "PATCH", {"opportunity_updates": [{"id": "fresh"}]})
            patch = websocket.receive_json()
            assert patch["event"] == "PATCH"
            assert patch["sequence"] == initial["sequence"] + 1

            websocket.send_json({"action": "resync", "page": page})
            resynced = websocket.receive_json()
            assert resynced["event"] == "SNAPSHOT"
            assert resynced["sequence"] == patch["sequence"]
            assert len(provider_calls) == 2
    finally:
        push.configure_snapshot_provider(page, None)


def test_page_resync_preserves_other_subscribed_channel_events():
    async def scenario():
        queue = asyncio.Queue(maxsize=4)
        queue.put_nowait({"page": "box-spread", "event": "PATCH", "sequence": 2})
        queue.put_nowait({"page": "iv-skew-flip", "event": "PATCH", "sequence": 8})
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        assert waiter.done()
        next_waiter = await push._restart_event_waiter(queue, waiter, "box-spread")
        preserved = await asyncio.wait_for(next_waiter, timeout=0.1)
        assert preserved["page"] == "iv-skew-flip"
        next_waiter.cancel()
        await asyncio.gather(next_waiter, return_exceptions=True)
    asyncio.run(scenario())


def test_page_push_dispatcher_marks_queue_overflow_for_snapshot(monkeypatch):
    async def scenario():
        page = "cash-future"
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"page": page, "event": "PATCH", "sequence": 1})
        monkeypatch.setattr(push, "_latest_event_id", lambda: 0)
        monkeypatch.setattr(push, "_events_after", lambda cursor: [
            {"id": 1, "page": page, "sequence": 2, "event": "PATCH", "data": {"rows": []}}
        ] if cursor == 0 else [])
        monkeypatch.setattr(push, "_subscribers", {(queue, frozenset({page}))})
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        task = asyncio.create_task(push._journal_dispatch_loop())
        try:
            await asyncio.wait_for(push._dispatch_ready.wait(), timeout=0.5)
            await asyncio.sleep(0.05)
            assert queue.qsize() == 1
            item = queue.get_nowait()
            assert item["event"] == "SNAPSHOT_REQUIRED"
            assert item["sequence"] == 2
        finally:
            monkeypatch.setattr(push, "_subscribers", set())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
