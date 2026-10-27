from dataclasses import replace
import asyncio

import pytest

from app.market_data.live_synthetic_stream import SyntheticSubscription
from app.scanner.box_spread_contract_resolver import resolve_box_spread_contracts
from app.scanner.box_spread_paper_engine import (
    BoxSpreadPaperEngine,
    BoxSpreadPaperEngineError,
)


def _contracts(direction="LONG"):
    expiry = "29OCT2026"
    subscriptions = [
        SyntheticSubscription(2, "1001", f"NIFTY{expiry}25000CE", "NIFTY", "INDEX", expiry, "CE", 25000, 25),
        SyntheticSubscription(2, "1002", f"NIFTY{expiry}25100CE", "NIFTY", "INDEX", expiry, "CE", 25100, 25),
        SyntheticSubscription(2, "1003", f"NIFTY{expiry}25100PE", "NIFTY", "INDEX", expiry, "PE", 25100, 25),
        SyntheticSubscription(2, "1004", f"NIFTY{expiry}25000PE", "NIFTY", "INDEX", expiry, "PE", 25000, 25),
    ]
    return resolve_box_spread_contracts(
        subscriptions=subscriptions, underlying="NIFTY", instrument_class="INDEX",
        expiry="2026-10-29", low_strike=25000, high_strike=25100,
        direction=direction, expected_lot_size=25,
    )


def test_creates_four_leg_long_paper_intent_without_broker_execution():
    engine = BoxSpreadPaperEngine()
    intent = asyncio.run(engine.create_intent(
        contracts=_contracts(), lots=2, idempotency_key="opportunity-1"
    ))

    assert intent.status == "CREATED"
    assert intent.quantity == 50
    assert [leg.role for leg in intent.legs] == ["LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"]
    assert [leg.action for leg in intent.legs] == ["BUY", "SELL", "BUY", "SELL"]
    assert all(leg.quantity == 50 for leg in intent.legs)
    assert [leg.token for leg in intent.legs] == ["1001", "1002", "1003", "1004"]


def test_short_direction_reverses_each_leg_action():
    intent = asyncio.run(BoxSpreadPaperEngine().create_intent(
        contracts=_contracts("SHORT"), lots=1, idempotency_key="short-1"
    ))
    assert [leg.action for leg in intent.legs] == ["SELL", "BUY", "SELL", "BUY"]


def test_same_idempotency_key_and_same_request_returns_same_intent():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        first, second = await asyncio.gather(
            engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="same"),
            engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="same"),
        )
        assert first == second
        assert len(await engine.list_intents()) == 1
    asyncio.run(scenario())


def test_rejects_idempotency_key_reused_for_different_request():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="same")
        with pytest.raises(BoxSpreadPaperEngineError, match="different paper request"):
            await engine.create_intent(contracts=_contracts(), lots=2, idempotency_key="same")
    asyncio.run(scenario())


@pytest.mark.parametrize("lots", [0, -1, True, 1.5, "abc", None])
def test_rejects_invalid_lot_count(lots):
    with pytest.raises(BoxSpreadPaperEngineError, match="lots must be a positive integer"):
        asyncio.run(BoxSpreadPaperEngine().create_intent(
            contracts=_contracts(), lots=lots, idempotency_key="bad-lots"
        ))


@pytest.mark.parametrize("key", ["", "   ", None])
def test_rejects_missing_idempotency_key(key):
    with pytest.raises(BoxSpreadPaperEngineError, match="idempotency_key is required"):
        asyncio.run(BoxSpreadPaperEngine().create_intent(
            contracts=_contracts(), lots=1, idempotency_key=key
        ))


def test_get_and_list_return_only_created_paper_intents():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        assert await engine.get_intent("missing") is None
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="one")
        await engine.create_intent(contracts=_contracts("SHORT"), lots=1, idempotency_key="two")
        assert len(await engine.list_intents()) == 2
    asyncio.run(scenario())
