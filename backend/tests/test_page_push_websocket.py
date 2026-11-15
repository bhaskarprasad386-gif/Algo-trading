from types import SimpleNamespace

from app.scanner import page_push_websocket as push


def test_page_channels_are_explicit_and_strategy_scoped():
    assert "box-spread" in push.PAGE_CHANNELS
    assert "iv-skew-flip" in push.PAGE_CHANNELS
    assert "credit-butterfly-condor-weekly" in push.PAGE_CHANNELS
    assert "credit-butterfly-condor-monthly" in push.PAGE_CHANNELS
    assert "synthetic-arbitrage" not in push.PAGE_CHANNELS


def test_frames_have_monotonic_per_page_sequences():
    page = "box-spread"
    before = push._sequences.get(page, 0)
    first = push._frame(page, "SNAPSHOT", {"opportunities": []})
    second = push._frame(page, "PATCH", {"opportunities": [{"symbol": "NIFTY"}]})

    assert first["type"] == "page_update"
    assert first["page"] == page
    assert first["event"] == "SNAPSHOT"
    assert second["sequence"] == first["sequence"] + 1
    assert first["sequence"] == before + 1
    assert first["generated_at"]
    assert second["data"]["opportunities"][0]["symbol"] == "NIFTY"


def test_snapshot_provider_is_read_only_and_page_scoped():
    observed = []
    push.configure_snapshot_provider("credit-butterfly-condor-weekly", lambda: observed.append("read") or {"rows": []})
    provider = push._snapshot_providers["credit-butterfly-condor-weekly"]

    assert provider() == {"rows": []}
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



def test_resync_snapshot_does_not_advance_sequence_for_other_clients():
    page = "iv-skew-flip"
    push._sequences[page] = 41
    snapshot = push._snapshot_frame(page, {"status": "RUNNING"})
    next_patch = push._frame(page, "PATCH", {"status": "RUNNING"})

    assert snapshot["sequence"] == 41
    assert next_patch["sequence"] == 42

def test_websocket_subscribe_publish_and_resync_round_trip(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(push.router)
    page = "box-spread"
    provider_calls = []
    push.configure_snapshot_provider(page, lambda: provider_calls.append("snapshot") or {"opportunities": [{"id": "initial"}]})
    monkeypatch.setattr(push, "_subscribers", set())
    monkeypatch.setattr(push, "_sequences", {})
    monkeypatch.setattr(push, "_loop", None)

    try:
        with TestClient(app).websocket_connect(
            "/ws/page-updates",
            headers={"origin": "https://algo-trading.vercel.app"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "connected"
            websocket.send_json({"action": "subscribe", "page": page})
            initial = websocket.receive_json()
            assert initial["event"] == "SNAPSHOT"
            assert initial["data"]["opportunities"][0]["id"] == "initial"

            push.publish_page_update(page, "PATCH", {"opportunities": [{"id": "fresh"}]})
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


def test_websocket_rejects_untrusted_origin():
    import pytest
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = FastAPI()
    app.include_router(push.router)
    with pytest.raises(WebSocketDisconnect) as disconnected:
        with TestClient(app).websocket_connect(
            "/ws/page-updates",
            headers={"origin": "https://untrusted.example"},
        ):
            pass
    assert disconnected.value.code == 1008


def test_publish_queue_overflow_replaces_backlog_with_fresh_snapshot():
    import asyncio

    async def scenario():
        page = "credit-butterfly-condor-weekly"
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=1)
        push._loop = loop
        push._subscribers = {(queue, frozenset({page}))}
        push._sequences = {page: 0}
        push.configure_snapshot_provider(page, lambda: {"rows": [{"id": "latest"}]})

        push.publish_page_update(page, "PATCH", {"rows": [{"id": "old"}]})
        push.publish_page_update(page, "PATCH", {"rows": [{"id": "new"}]})
        await asyncio.sleep(0)

        frame = queue.get_nowait()
        assert frame["event"] == "SNAPSHOT"
        assert frame["data"]["rows"][0]["id"] == "latest"
        push._subscribers.clear()
        push._loop = None
        push.configure_snapshot_provider(page, None)

    asyncio.run(scenario())

