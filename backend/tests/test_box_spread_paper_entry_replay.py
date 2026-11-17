"""Conformance tests for durable Box Paper entry receipt replay."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.scanner import box_spread_paper_routes as routes


_ROLES = ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE")
_LONG_ACTIONS = {
    "LOW_CE": "BUY",
    "HIGH_CE": "SELL",
    "HIGH_PE": "BUY",
    "LOW_PE": "SELL",
}
_SHORT_ACTIONS = {
    "LOW_CE": "SELL",
    "HIGH_CE": "BUY",
    "HIGH_PE": "SELL",
    "LOW_PE": "BUY",
}


def _request(direction="LONG"):
    return routes.CreatePaperTrade(
        underlying="NIFTY",
        instrument_class="INDEX",
        expiry="2026-10-29",
        low_strike=25000,
        high_strike=25100,
        direction=direction,
        lots=1,
        idempotency_key="durable-entry-replay-test",
        account_key="replay-account",
    )


def _persisted_fills(request, *, quantity=25, mutation=None):
    actions = _LONG_ACTIONS if request.direction == "LONG" else _SHORT_ACTIONS
    fills = [
        SimpleNamespace(
            fill_id=routes._ledger_fill_id(request.idempotency_key, role),
            role=role,
            action=actions[role],
            quantity=quantity,
            fill_price=Decimal("101.25") + index,
            notional=Decimal("2531.25") + index * Decimal("25"),
            charges=Decimal("1.25"),
            cash_delta=Decimal("-2532.50"),
            fee_schedule_version="paper-test-v1",
        )
        for index, role in enumerate(_ROLES)
    ]
    if mutation == "missing":
        fills.pop()
    elif mutation == "wrong_role":
        fills[0].role = "HIGH_CE"
    elif mutation == "wrong_action":
        fills[0].action = "SELL" if actions["LOW_CE"] == "BUY" else "BUY"
    elif mutation == "wrong_quantity":
        fills[0].quantity = quantity - 1
    return fills


def _install_replay_ledger(monkeypatch, request, *, claim_status="COMPLETED", mutation=None):
    calls = []
    monkeypatch.setattr(
        routes._ledger,
        "get_entry_claim",
        lambda **kwargs: calls.append(("claim", kwargs)) or {"status": claim_status},
    )
    monkeypatch.setattr(
        routes._ledger,
        "list_fills",
        lambda trade_id, account_key="default": (
            calls.append(("fills", {"trade_id": trade_id, "account_key": account_key}))
            or _persisted_fills(request, mutation=mutation)
        ),
    )
    monkeypatch.setattr(
        routes,
        "_load_trade_quantity",
        lambda trade_id, account_key: (
            calls.append(("quantity", {"trade_id": trade_id, "account_key": account_key}))
            or 25
        ),
    )
    monkeypatch.setattr(
        routes._ledger,
        "get_trade_summary",
        lambda trade_id, account_key="default": (
            calls.append(("summary", {"trade_id": trade_id, "account_key": account_key}))
            or {"status": "FILLED", "net_pnl": Decimal("12.50")}
        ),
    )
    return calls


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_completed_entry_replay_returns_original_four_receipts_without_execution(monkeypatch, direction):
    request = _request(direction)
    calls = _install_replay_ledger(monkeypatch, request)

    result = asyncio.run(
        routes._completed_entry_receipt_replay(71, "replay-account", request)
    )

    assert result is not None
    assert result["mode"] == "PAPER_ONLY"
    assert result["broker_orders_enabled"] is False
    assert result["replayed"] is True
    assert result["status"] == "FILLED"
    assert len(result["fills"]) == 4
    assert {fill["role"] for fill in result["fills"]} == set(_ROLES)
    assert {fill["action"] for fill in result["fills"]} == set(
        (_LONG_ACTIONS if direction == "LONG" else _SHORT_ACTIONS).values()
    )
    assert result["summary"]["net_pnl"] == "12.50"
    assert [item[0] for item in calls] == ["claim", "fills", "quantity", "summary"]
    assert all(
        item[1].get("account_key") == "replay-account"
        for item in calls
    )


@pytest.mark.parametrize("mutation", ["missing", "wrong_role", "wrong_action", "wrong_quantity"])
def test_completed_claim_with_nonconforming_receipts_fails_closed(monkeypatch, mutation):
    request = _request()
    _install_replay_ledger(monkeypatch, request, mutation=mutation)
    summary_calls = []
    monkeypatch.setattr(
        routes._ledger,
        "get_trade_summary",
        lambda *args, **kwargs: summary_calls.append(True) or {"status": "FILLED"},
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes._completed_entry_receipt_replay(72, "replay-account", request)
        )

    assert raised.value.status_code == 409
    assert "manual review" in raised.value.detail
    assert summary_calls == []


def test_incomplete_claim_blocks_before_fill_or_summary_reads(monkeypatch):
    request = _request()
    calls = []
    monkeypatch.setattr(
        routes._ledger, "get_entry_claim",
        lambda **kwargs: {"status": "CLAIMED"},
    )
    monkeypatch.setattr(
        routes._ledger, "list_fills",
        lambda *args, **kwargs: calls.append("fills") or [],
    )
    monkeypatch.setattr(
        routes._ledger, "get_trade_summary",
        lambda *args, **kwargs: calls.append("summary") or {},
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes._completed_entry_receipt_replay(73, "replay-account", request)
        )

    assert raised.value.status_code == 409
    assert "not completed" in raised.value.detail
    assert calls == []


def test_missing_claim_allows_caller_to_continue_new_entry_path(monkeypatch):
    request = _request()
    calls = []
    monkeypatch.setattr(
        routes._ledger, "get_entry_claim",
        lambda **kwargs: calls.append(kwargs) or None,
    )

    result = asyncio.run(
        routes._completed_entry_receipt_replay(74, "replay-account", request)
    )

    assert result is None
    assert calls == [{"trade_id": 74, "account_key": "replay-account"}]
