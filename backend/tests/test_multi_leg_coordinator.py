import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from app.execution.multi_leg_coordinator import (
    BasketIntent, BasketLeg, BasketStrategy, BasketResultState,
    BrokerOrderAck, BasketReconciliation, EdgeCheck, ExecutableQuote,
    PreflightEvidence, ReconciledLeg, MultiLegBasketCoordinator,
)


class FakeJournal:
    def __init__(self, *, claim=True, fail_started=False, fail_ack=False):
        self.claim = claim
        self.fail_started = fail_started
        self.fail_ack = fail_ack
        self.claims = []
        self.started = []
        self.acks = []
        self.frozen = []
        self.completed = []
        self.rejected = []
        self.manual = []

    async def claim_submission(self, **kwargs):
        self.claims.append(kwargs)
        return self.claim

    async def record_submission_started(self, **kwargs):
        self.started.append(kwargs)
        if self.fail_started:
            raise RuntimeError("journal unavailable")

    async def record_order_ack(self, **kwargs):
        self.acks.append(kwargs)
        if self.fail_ack:
            raise RuntimeError("ack persistence unavailable")

    async def freeze_intent(self, **kwargs):
        self.frozen.append(kwargs)

    async def complete_filled_basket(self, **kwargs):
        self.completed.append(kwargs)

    async def reject_preflight(self, **kwargs):
        self.rejected.append(kwargs)

    async def require_manual_review(self, **kwargs):
        self.manual.append(kwargs)


class FakeBroker:
    def __init__(self, intent, *, reject_index=None, fill_overrides=None,
                 timeout_index=None, barrier=False):
        self.intent = intent
        self.reject_index = reject_index
        self.fill_overrides = fill_overrides or {}
        self.timeout_index = timeout_index
        self.barrier = barrier
        self.calls = []
        self.reconciliations = []
        self._ready = asyncio.Event()

    async def place_limit(self, *, account_id, client_order_id, symbol, side, quantity, limit_price):
        index = len(self.calls)
        self.calls.append((client_order_id, symbol, side, quantity, limit_price))
        if self.barrier:
            if len(self.calls) == len(self.intent.legs):
                self._ready.set()
            await asyncio.wait_for(self._ready.wait(), timeout=1)
        if index == self.timeout_index:
            raise asyncio.TimeoutError()
        accepted = index != self.reject_index
        return BrokerOrderAck(
            client_order_id=client_order_id,
            broker_order_id=f"broker-{index}" if accepted else None,
            status="OPEN" if accepted else "REJECTED",
            accepted=accepted,
        )

    async def reconcile_intent(self, *, account_id, intent_id, client_order_ids):
        self.reconciliations.append(client_order_ids)
        rows = []
        for leg, client_id in zip(self.intent.legs, client_order_ids, strict=True):
            quantity = self.fill_overrides.get(leg.leg_id, self.intent.quantity_for(leg))
            rows.append(ReconciledLeg(
                client_order_id=client_id,
                broker_order_id=f"broker-{leg.leg_id}",
                filled_quantity=quantity,
                order_terminal=True,
            ))
        return BasketReconciliation(
            authoritative=True, orders_complete=True, trades_complete=True,
            positions_reconciled=True, legs=tuple(rows),
        )


def make_intent(strategy=BasketStrategy.BOX_SPREAD):
    if strategy is BasketStrategy.BOX_SPREAD:
        specs = [
            ("low-ce", "LOW_CE", "CE", "BUY", 1),
            ("high-ce", "HIGH_CE", "CE", "SELL", 1),
            ("high-pe", "HIGH_PE", "PE", "BUY", 1),
            ("low-pe", "LOW_PE", "PE", "SELL", 1),
        ]
    elif strategy is BasketStrategy.CREDIT_BUTTERFLY:
        specs = [
            ("lower", "LOWER_WING", "CE", "BUY", 1),
            ("body", "BODY", "CE", "SELL", 2),
            ("upper", "UPPER_WING", "CE", "BUY", 1),
        ]
    else:
        specs = [
            ("put-short", "PUT_SHORT", "PE", "SELL", 1),
            ("put-long", "PUT_LONG", "PE", "BUY", 1),
            ("call-short", "CALL_SHORT", "CE", "SELL", 1),
            ("call-long", "CALL_LONG", "CE", "BUY", 1),
        ]
    legs = tuple(
        BasketLeg(
            leg_id=leg_id, role=role, symbol=f"OPT:{leg_id}", side=side,
            ratio=ratio, expiry="2026-12-31", option_type=kind,
            limit_price=Decimal("100.10") if side == "BUY" else Decimal("100.00"),
        )
        for leg_id, role, kind, side, ratio in specs
    )
    return BasketIntent(
        account_id="acct-1", intent_id=f"intent-{strategy.value}",
        strategy=strategy, lots=1, lot_size=25, legs=legs,
        baseline_positions=tuple((leg.symbol, 0) for leg in legs),
        direction="LONG" if strategy is BasketStrategy.BOX_SPREAD else "CREDIT",
    )


def make_evidence(intent, *, stale_symbol=None, insufficient_symbol=None, edge=Decimal("2")):
    quotes = {}
    for index, leg in enumerate(intent.legs):
        quotes[leg.symbol] = ExecutableQuote(
            bid=Decimal("100.00"), ask=Decimal("100.10"),
            bid_depth=10 if leg.symbol == insufficient_symbol else 1000,
            ask_depth=10 if leg.symbol == insufficient_symbol else 1000,
            tick_size=Decimal("0.05"), lot_size=25,
            observed_at_ns=0 if leg.symbol == stale_symbol else 1_000_000_000-index*10_000_000,
        )
    return PreflightEvidence(
        quotes=quotes, edge=EdgeCheck(edge, Decimal("1"), "snapshot-1"),
        risk_approved=True, margin_approved=True, snapshot_observed_at_ns=1_000_000_000,
    )


def execute(intent, broker=None, journal=None, recovery=None, evidence=None):
    broker = broker or FakeBroker(intent)
    journal = journal or FakeJournal()
    recovery_calls = []
    if recovery is None:
        def recovery(**kwargs):
            recovery_calls.append(kwargs)
            return SimpleNamespace(state="FLAT_AFTER_UNWIND")
    active_evidence = evidence or make_evidence(intent)
    coordinator = MultiLegBasketCoordinator(
        broker, journal, recovery, max_quote_age_ns=1_000_000_000,
        max_cross_leg_skew_ns=250_000_000,
        edge_evaluator=lambda basket, quotes: active_evidence.edge,
    )
    result = asyncio.run(coordinator.execute(
        intent, active_evidence, now_ns=1_000_000_000,
    ))
    return result, broker, journal, recovery_calls


def test_box_spread_submits_all_four_legs_concurrently_and_requires_reconciled_full_fills():
    intent = make_intent()
    broker = FakeBroker(intent, barrier=True)
    result, broker, journal, _ = execute(intent, broker=broker)

    assert result.state is BasketResultState.FILLED
    assert len(broker.calls) == 4
    assert len(journal.completed) == 1
    assert len(broker.reconciliations) == 1


def test_credit_butterfly_body_leg_uses_double_quantity():
    intent = make_intent(BasketStrategy.CREDIT_BUTTERFLY)
    result, broker, _, _ = execute(intent)

    assert result.state is BasketResultState.FILLED
    quantities = {call[1]: call[3] for call in broker.calls}
    assert sorted(quantities.values()) == [25, 25, 50]


def test_condor_requires_all_four_legs_to_reconcile_fully():
    intent = make_intent(BasketStrategy.CONDOR)
    result, broker, journal, _ = execute(intent)

    assert result.state is BasketResultState.FILLED
    assert len(broker.calls) == 4
    assert len(journal.completed) == 1


def test_one_rejected_leg_freezes_basket_and_invokes_recovery():
    intent = make_intent()
    broker = FakeBroker(intent, reject_index=2)
    result, broker, journal, recovery_calls = execute(intent, broker=broker)

    assert result.state is BasketResultState.RECOVERED_FLAT
    assert len(broker.calls) == 4
    assert journal.frozen
    assert len(recovery_calls) == 1
    assert not journal.completed


def test_one_partial_fill_never_marks_basket_filled():
    intent = make_intent()
    broker = FakeBroker(intent, fill_overrides={"high-ce": 25})
    result, _, journal, recovery_calls = execute(intent, broker=broker)

    assert result.state is BasketResultState.RECOVERED_FLAT
    assert journal.frozen
    assert recovery_calls
    assert not journal.completed


def test_unknown_submission_timeout_uses_deterministic_recovery_path():
    intent = make_intent()
    broker = FakeBroker(intent, timeout_index=1)
    result, _, journal, recovery_calls = execute(intent, broker=broker)

    assert result.state is BasketResultState.RECOVERED_FLAT
    assert journal.frozen
    assert recovery_calls
    assert not journal.completed


def test_duplicate_submission_claim_blocks_all_broker_calls():
    intent = make_intent()
    journal = FakeJournal(claim=False)
    result, broker, _, recovery_calls = execute(intent, journal=journal)

    assert result.state is BasketResultState.DUPLICATE_BLOCKED
    assert not broker.calls
    assert not recovery_calls


def test_stale_leg_quote_rejects_entire_basket_before_any_submission():
    intent = make_intent()
    evidence = make_evidence(intent, stale_symbol=intent.legs[0].symbol)
    result, broker, journal, recovery_calls = execute(intent, evidence=evidence)

    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected
    assert not recovery_calls


def test_insufficient_depth_for_one_leg_rejects_entire_basket():
    intent = make_intent()
    evidence = make_evidence(intent, insufficient_symbol=intent.legs[2].symbol)
    result, broker, journal, _ = execute(intent, evidence=evidence)

    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_net_edge_below_fee_adjusted_threshold_rejects_entire_basket():
    intent = make_intent()
    evidence = make_evidence(intent, edge=Decimal("0.5"))
    result, broker, journal, _ = execute(intent, evidence=evidence)

    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_invalid_butterfly_ratio_is_rejected_before_broker_submission():
    intent = make_intent(BasketStrategy.CREDIT_BUTTERFLY)
    bad_legs = tuple(replace(leg, ratio=1) for leg in intent.legs)
    intent = replace(intent, legs=bad_legs)
    result, broker, journal, _ = execute(intent)

    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_missing_pre_entry_baseline_for_one_leg_is_rejected_at_intent_creation():
    intent = make_intent()
    try:
        replace(intent, baseline_positions=intent.baseline_positions[:-1])
    except ValueError as exc:
        assert "baseline" in str(exc)
    else:
        raise AssertionError("missing baseline must fail closed")


def test_submission_journal_failure_prevents_any_broker_order():
    intent = make_intent()
    journal = FakeJournal(fail_started=True)
    result, broker, journal, _ = execute(intent, journal=journal)

    assert result.state is BasketResultState.MANUAL_REVIEW
    assert not broker.calls
    assert journal.frozen
    assert journal.manual


def test_ack_persistence_failure_triggers_recovery_not_success():
    intent = make_intent()
    journal = FakeJournal(fail_ack=True)
    result, broker, journal, recovery_calls = execute(intent, journal=journal)

    assert result.state is BasketResultState.RECOVERED_FLAT
    assert len(broker.calls) == 4
    assert journal.frozen
    assert recovery_calls
    assert not journal.completed


def test_failed_recovery_latches_manual_review():
    intent = make_intent()
    broker = FakeBroker(intent, reject_index=0)
    def failed_recovery(**kwargs):
        return SimpleNamespace(state="MANUAL_REVIEW_RESIDUAL_EXPOSURE")
    result, _, journal, _ = execute(intent, broker=broker, recovery=failed_recovery)

    assert result.state is BasketResultState.MANUAL_REVIEW
    assert journal.manual
    assert not journal.completed


def test_quote_cross_leg_skew_rejects_entire_basket():
    intent = make_intent()
    quotes = dict(make_evidence(intent).quotes)
    quotes[intent.legs[0].symbol] = replace(
        quotes[intent.legs[0].symbol], observed_at_ns=700_000_000,
    )
    evidence = replace(make_evidence(intent), quotes=quotes)
    result, broker, journal, _ = execute(intent, evidence=evidence)

    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_missing_strategy_edge_evaluator_fails_closed_before_submission():
    intent = make_intent()
    broker, journal = FakeBroker(intent), FakeJournal()
    coordinator = MultiLegBasketCoordinator(broker, journal, lambda **kwargs: None)
    result = asyncio.run(coordinator.execute(
        intent, make_evidence(intent), now_ns=1_000_000_000,
    ))
    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_current_quote_edge_recomputation_can_reject_alert_that_was_profitable():
    intent = make_intent()
    broker, journal = FakeBroker(intent), FakeJournal()
    coordinator = MultiLegBasketCoordinator(
        broker, journal, lambda **kwargs: None,
        edge_evaluator=lambda basket, quotes: EdgeCheck(
            Decimal("0.25"), Decimal("1"), "snapshot-1",
        ),
    )
    result = asyncio.run(coordinator.execute(
        intent, make_evidence(intent, edge=Decimal("5")), now_ns=1_000_000_000,
    ))
    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_butterfly_body_ratio_cannot_be_moved_to_a_wing():
    intent = make_intent(BasketStrategy.CREDIT_BUTTERFLY)
    wrong = tuple(replace(leg, ratio=2 if leg.role == "LOWER_WING" else 1) for leg in intent.legs)
    intent = replace(intent, legs=wrong)
    result, broker, journal, _ = execute(intent)
    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_box_spread_role_to_option_type_mapping_is_enforced():
    intent = make_intent()
    wrong = tuple(
        replace(leg, option_type="PE") if leg.role == "LOW_CE" else leg
        for leg in intent.legs
    )
    intent = replace(intent, legs=wrong)
    result, broker, journal, _ = execute(intent)
    assert result.state is BasketResultState.PREFLIGHT_REJECTED
    assert not broker.calls
    assert journal.rejected


def test_short_box_spread_requires_all_four_sides_to_be_reversed():
    intent = make_intent()
    short_legs = tuple(
        replace(
            leg,
            side="SELL" if leg.side == "BUY" else "BUY",
            limit_price=Decimal("100.00") if leg.side == "BUY" else Decimal("100.10"),
        )
        for leg in intent.legs
    )
    intent = replace(intent, legs=short_legs, direction="SHORT")
    result, broker, journal, _ = execute(intent)
    assert result.state is BasketResultState.FILLED
    assert len(broker.calls) == 4
    assert not journal.rejected


def test_recovery_receives_typed_pre_entry_baseline_records():
    intent = make_intent()
    broker = FakeBroker(intent, reject_index=0)
    result, _, _, recovery_calls = execute(intent, broker=broker)
    assert result.state is BasketResultState.RECOVERED_FLAT
    baseline = recovery_calls[0]["baseline_positions"]
    assert all(hasattr(row, "symbol") and hasattr(row, "quantity") for row in baseline)
    assert {row.symbol for row in baseline} == {leg.symbol for leg in intent.legs}
