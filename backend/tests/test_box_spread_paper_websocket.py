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


def test_private_ws_origin_requires_exact_cloudflare_pages_allowlist(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        private_ws.settings,
        "BOX_SPREAD_PAPER_WS_ALLOWED_ORIGINS",
        "https://paper.example.pages.dev,https://paper.example.com",
    )
    allowed = SimpleNamespace(headers={"origin": "https://paper.example.pages.dev"})
    unrelated_pages_tenant = SimpleNamespace(headers={"origin": "https://attacker.pages.dev"})
    assert private_ws._origin_allowed(allowed) is True
    assert private_ws._origin_allowed(unrelated_pages_tenant) is False


def test_private_ws_snapshot_provider_receives_account_and_event_scope(monkeypatch):
    received = []
    private_ws.configure_snapshot_provider(
        lambda account_key, event_type: received.append((account_key, event_type)) or {"ok": True}
    )
    try:
        frame = private_ws._snapshot_frame("RULE_EVALUATION_CHANGED", "paper-account-1")
    finally:
        private_ws.configure_snapshot_provider(None)
    assert received == [("paper-account-1", "RULE_EVALUATION_CHANGED")]
    assert frame["snapshot"] is True
    assert frame["payload"] == {"ok": True}
