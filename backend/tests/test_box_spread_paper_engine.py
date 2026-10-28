import asyncio

import pytest
from decimal import Decimal

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



def test_paper_execution_applies_200ms_async_latency_and_adverse_slippage():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        intent = await engine.create_intent(
            contracts=_contracts(), lots=1, idempotency_key="execute-1"
        )
        # Keep CI fast while asserting the production default is exactly 200ms.
        assert BoxSpreadPaperEngine.execution_latency_seconds == 0.2
        engine.execution_latency_seconds = 0
        execution = await engine.execute_intent(
            idempotency_key=intent.idempotency_key,
            reference_prices={
                "LOW_CE": "100.00", "HIGH_CE": "50.00",
                "HIGH_PE": "55.00", "LOW_PE": "95.00",
            },
        )
        assert execution.latency_seconds == 0
        assert str(execution.slippage_rate) == "0.0005"
        assert [fill.role for fill in execution.fills] == [
            "LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"
        ]
        assert [fill.action for fill in execution.fills] == ["BUY", "SELL", "BUY", "SELL"]
        assert [fill.fill_price for fill in execution.fills] == [
            Decimal("100.050000"), Decimal("49.975000"),
            Decimal("55.027500"), Decimal("94.952500"),
        ]
        assert all(fill.quantity == 25 for fill in execution.fills)
    asyncio.run(scenario())


def test_paper_execution_is_idempotent_and_rejects_changed_prices():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="execute-2")
        engine.execution_latency_seconds = 0
        prices = {"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95}
        first, second = await asyncio.gather(
            engine.execute_intent(idempotency_key="execute-2", reference_prices=prices),
            engine.execute_intent(idempotency_key="execute-2", reference_prices=prices),
        )
        assert first == second
        with pytest.raises(BoxSpreadPaperEngineError, match="different reference prices"):
            await engine.execute_intent(
                idempotency_key="execute-2",
                reference_prices={**prices, "LOW_CE": 101},
            )
    asyncio.run(scenario())


@pytest.mark.parametrize("prices", [
    {"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55},
    {"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95, "EXTRA": 1},
    {"LOW_CE": 0, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95},
    {"LOW_CE": "NaN", "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95},
])
def test_paper_execution_rejects_incomplete_or_invalid_reference_prices(prices):
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="bad-prices")
        engine.execution_latency_seconds = 0
        with pytest.raises(BoxSpreadPaperEngineError):
            await engine.execute_intent(idempotency_key="bad-prices", reference_prices=prices)
    asyncio.run(scenario())


def test_paper_execution_requires_existing_intent():
    with pytest.raises(BoxSpreadPaperEngineError, match="paper intent not found"):
        asyncio.run(BoxSpreadPaperEngine().execute_intent(
            idempotency_key="missing",
            reference_prices={"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95},
        ))



def test_paper_execution_uses_nonblocking_configured_latency(monkeypatch):
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="latency-check")
        waits = []
        original_sleep = asyncio.sleep

        async def fake_sleep(delay):
            waits.append(delay)
            await original_sleep(0)

        monkeypatch.setattr("app.scanner.box_spread_paper_engine.asyncio.sleep", fake_sleep)
        execution = await engine.execute_intent(
            idempotency_key="latency-check",
            reference_prices={"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95},
        )
        assert waits == [0.2]
        assert execution.latency_seconds == 0.2

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_price", ["not-a-price", "", None])
def test_paper_execution_rejects_unparseable_reference_prices(bad_price):
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="malformed-price")
        with pytest.raises(BoxSpreadPaperEngineError, match="reference price must be positive"):
            await engine.execute_intent(
                idempotency_key="malformed-price",
                reference_prices={
                    "LOW_CE": bad_price, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95,
                },
            )
    asyncio.run(scenario())

def test_partial_fills_track_remaining_and_residual_exposure():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=2, idempotency_key="partial-1")
        first = await engine.record_leg_fill(
            idempotency_key="partial-1", role="LOW_CE", fill_id="fill-a",
            quantity=20, fill_price="100.25",
        )
        assert first.quantity == 20
        progress = await engine.get_fill_progress("partial-1")
        assert progress.status == "PARTIALLY_FILLED"
        assert progress.matched_quantity == 0
        assert progress.legs[0].filled_quantity == 20
        assert progress.legs[0].remaining_quantity == 30
        assert progress.residual_exposure[0].role == "LOW_CE"
        assert progress.residual_exposure[0].signed_quantity == 20
    asyncio.run(scenario())


def test_partial_fill_idempotency_and_overfill_protection():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="partial-2")
        kwargs = dict(idempotency_key="partial-2", role="LOW_CE", fill_id="same-fill",
                      quantity=10, fill_price="100")
        first, second = await asyncio.gather(
            engine.record_leg_fill(**kwargs), engine.record_leg_fill(**kwargs)
        )
        assert first == second
        progress = await engine.get_fill_progress("partial-2")
        assert progress.legs[0].filled_quantity == 10
        with pytest.raises(BoxSpreadPaperEngineError, match="exceeds"):
            await engine.record_leg_fill(
                idempotency_key="partial-2", role="LOW_CE", fill_id="overfill",
                quantity=16, fill_price="100",
            )
        with pytest.raises(BoxSpreadPaperEngineError, match="different fill data"):
            await engine.record_leg_fill(
                idempotency_key="partial-2", role="LOW_CE", fill_id="same-fill",
                quantity=11, fill_price="100",
            )
    asyncio.run(scenario())


def test_partial_fills_match_only_equal_quantity_across_all_legs():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="matched-1")
        for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"):
            await engine.record_leg_fill(
                idempotency_key="matched-1", role=role, fill_id=f"{role}-1",
                quantity=10, fill_price="100",
            )
        progress = await engine.get_fill_progress("matched-1")
        assert progress.matched_quantity == 10
        assert progress.residual_exposure == ()
        assert progress.status == "PARTIALLY_FILLED"
        for role in ("LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"):
            await engine.record_leg_fill(
                idempotency_key="matched-1", role=role, fill_id=f"{role}-2",
                quantity=15, fill_price="101",
            )
        progress = await engine.get_fill_progress("matched-1")
        assert progress.matched_quantity == 25
        assert progress.status == "FILLED"
        assert progress.residual_exposure == ()
    asyncio.run(scenario())


def test_retry_and_cancel_preserve_residual_and_prevent_late_fill():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="cancel-1")
        await engine.record_leg_fill(
            idempotency_key="cancel-1", role="HIGH_CE", fill_id="ce-part",
            quantity=5, fill_price="50",
        )
        retried = await engine.retry_leg(idempotency_key="cancel-1", role="HIGH_CE")
        assert retried.retry_count == 1
        cancelled = await engine.cancel_leg(idempotency_key="cancel-1", role="HIGH_CE")
        assert cancelled.status == "CANCELLED_WITH_RESIDUAL"
        assert cancelled.remaining_quantity == 20
        progress = await engine.get_fill_progress("cancel-1")
        assert progress.status == "CANCELLED_WITH_RESIDUAL"
        assert progress.residual_exposure[0].signed_quantity == -5
        with pytest.raises(BoxSpreadPaperEngineError, match="cancelled leg"):
            await engine.record_leg_fill(
                idempotency_key="cancel-1", role="HIGH_CE", fill_id="late",
                quantity=1, fill_price="50",
            )
        with pytest.raises(BoxSpreadPaperEngineError, match="cancelled leg"):
            await engine.retry_leg(idempotency_key="cancel-1", role="HIGH_CE")
    asyncio.run(scenario())


def test_partial_fill_validation_and_full_execution_isolation():
    async def scenario():
        engine = BoxSpreadPaperEngine()
        await engine.create_intent(contracts=_contracts(), lots=1, idempotency_key="partial-3")
        with pytest.raises(BoxSpreadPaperEngineError, match="positive integer"):
            await engine.record_leg_fill(
                idempotency_key="partial-3", role="LOW_CE", fill_id="bad-qty",
                quantity=0, fill_price=100,
            )
        with pytest.raises(BoxSpreadPaperEngineError, match="unknown paper leg role"):
            await engine.record_leg_fill(
                idempotency_key="partial-3", role="UNKNOWN", fill_id="unknown",
                quantity=1, fill_price=100,
            )
        await engine.record_leg_fill(
            idempotency_key="partial-3", role="LOW_CE", fill_id="partial",
            quantity=1, fill_price=100,
        )
        with pytest.raises(BoxSpreadPaperEngineError, match="partial fill records"):
            await engine.execute_intent(
                idempotency_key="partial-3",
                reference_prices={"LOW_CE": 100, "HIGH_CE": 50, "HIGH_PE": 55, "LOW_PE": 95},
            )
    asyncio.run(scenario())
