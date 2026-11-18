from decimal import Decimal

from app.execution.emergency_recovery import (
    EmergencyRecoveryCoordinator,
    RecoveryOrder,
    RecoveryPolicy,
    RecoveryPosition,
    RecoveryQuote,
    RecoverySnapshot,
    RecoveryState,
)


class FakeJournal:
    def __init__(self):
        self.events = []
        self.states = []
        self.current_state = RecoveryState.RECOVERING
        self.kill_switches = []
        self.alerts = []
        self.released = []
        self.attempts = {}
        self.approve_close = True
        self.approved_closes = []
        self.frozen = []
        self.leases = True
        self.pending_authorizations = ()
        self.reconciled_authorizations = []

    def latch_entry_kill_switch(self, **kwargs):
        self.kill_switches.append(kwargs)

    def append_event(self, **kwargs):
        self.events.append(kwargs)

    def set_state(self, **kwargs):
        if self.current_state is RecoveryState.MANUAL_REVIEW and kwargs["state"] is RecoveryState.RECOVERING:
            raise ValueError("manual review cannot resume automated unwind without a new approved intent")
        self.current_state = kwargs["state"]
        self.states.append(kwargs)

    def release_risk_reservation(self, **kwargs):
        self.released.append(kwargs)

    def alert_operator(self, **kwargs):
        self.alerts.append(kwargs)

    def freeze_intent(self, **kwargs):
        self.frozen.append(kwargs)

    def assert_exclusive_instrument_lease(self, **kwargs):
        return self.leases

    def approve_emergency_close(self, **kwargs):
        self.approved_closes.append(kwargs)
        return self.approve_close

    def pending_close_authorizations(self, **kwargs):
        reconciled = set(self.reconciled_authorizations)
        return tuple(value for value in self.pending_authorizations if value not in reconciled)

    def mark_close_authorization_reconciled(self, **kwargs):
        self.reconciled_authorizations.append(kwargs["client_order_id"])

    def claim_recovery_attempt(self, *, account_id, intent_id, symbol):
        key = (account_id, intent_id, symbol)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        return self.attempts[key]


class FakeBroker:
    name = "angel_one"

    def __init__(self, quantity=50, *, unresolved_cancel=False, quote_age=0, partial_fills=()):
        self.account_id = "acct-1"
        self.position = quantity
        self.unrelated = 777
        self.entry_status = "WORKING"
        self.unresolved_cancel = unresolved_cancel
        self.quote_age = quote_age
        self.partial_fills = list(partial_fills)
        self.orders = [RecoveryOrder("entry-1", "entry-client-1", "NFO:CE", "ENTRY", "WORKING", 50)]
        self.closes = []
        self.cancelled = []

    def recovery_snapshot(self, *, account_id, intent_id):
        return RecoverySnapshot(
            account_id=account_id,
            broker=self.name,
            authoritative=True,
            positions_reconciled=True,
            position_attribution_complete=True,
            orders=tuple(self.orders),
            positions=(
                RecoveryPosition("NFO:CE", self.position),
                RecoveryPosition("UNRELATED", self.unrelated),
            ),
        )

    def cancel_order(self, *, account_id, broker_order_id):
        self.cancelled.append(broker_order_id)
        if not self.unresolved_cancel:
            self.entry_status = "CANCELLED"
            self.orders[0] = RecoveryOrder(
                "entry-1", "entry-client-1", "NFO:CE", "ENTRY", "CANCELLED", 0,
            )

    def recovery_quote(self, *, account_id, symbol):
        return RecoveryQuote(
            bid=Decimal("100.00"), ask=Decimal("100.10"),
            bid_depth=100, ask_depth=100, tick_size=Decimal("0.05"),
            lot_size=25, observed_at_ns=1_000_000_000 - self.quote_age,
        )

    def place_ioc_limit(self, *, account_id, order):
        self.closes.append(order)
        fill = self.partial_fills.pop(0) if self.partial_fills else order.quantity
        fill = min(fill, order.quantity)
        self.position += fill if order.side == "BUY" else -fill
        result = RecoveryOrder(
            broker_order_id=f"close-{len(self.closes)}",
            client_order_id=order.client_order_id,
            symbol=order.symbol, role="RECOVERY",
            status="FILLED" if fill == order.quantity else "CANCELLED",
            remaining_quantity=0,
        )
        self.orders.append(result)
        return result


def run(broker, journal, *, quantity_symbol=("NFO:CE",), baseline=None, policy=None):
    coordinator = EmergencyRecoveryCoordinator(broker, journal, policy=policy)
    return coordinator.recover(
        account_id="acct-1", intent_id="intent-1", strategy="BOX_SPREAD",
        symbols=quantity_symbol,
        baseline_positions=baseline or tuple(RecoveryPosition(symbol, 0) for symbol in quantity_symbol),
        now_ns=1_000_000_000,
    )


def test_recovery_cancels_reconciles_and_closes_long_position_with_bounded_ioc():
    broker, journal = FakeBroker(50), FakeJournal()
    result = run(broker, journal)

    assert result.state is RecoveryState.FLAT
    assert broker.cancelled == ["entry-1"]
    assert len(broker.closes) == 1
    close = broker.closes[0]
    assert (close.side, close.quantity, close.time_in_force) == ("SELL", 50, "IOC")
    assert close.limit_price == Decimal("99.50")
    assert broker.unrelated == 777
    assert journal.kill_switches
    assert journal.released == [{"account_id": "acct-1", "intent_id": "intent-1"}]


def test_recovery_closes_short_position_by_buying_at_ask_plus_collar():
    broker, journal = FakeBroker(-50), FakeJournal()
    result = run(broker, journal)

    assert result.state is RecoveryState.FLAT
    close = broker.closes[0]
    assert close.side == "BUY"
    assert close.limit_price == Decimal("100.65")


def test_partial_ioc_close_is_reconciled_before_bounded_retry():
    broker, journal = FakeBroker(50, partial_fills=(25, 25)), FakeJournal()
    result = run(broker, journal, policy=RecoveryPolicy(max_attempts=2))

    assert result.state is RecoveryState.FLAT
    assert [order.quantity for order in broker.closes] == [50, 25]
    assert len({order.client_order_id for order in broker.closes}) == 2


def test_non_terminal_entry_after_cancel_blocks_close_to_avoid_late_fill_race():
    broker, journal = FakeBroker(50, unresolved_cancel=True), FakeJournal()
    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert result.residual_positions == (RecoveryPosition("NFO:CE", 50),)
    assert journal.alerts
    assert not journal.released


def test_stale_recovery_quote_keeps_kill_switch_and_risk_reservation_latched():
    broker, journal = FakeBroker(50, quote_age=2_000_000_000), FakeJournal()
    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert result.residual_positions == (RecoveryPosition("NFO:CE", 50),)
    assert not journal.released


def test_wrong_broker_account_identity_fails_closed():
    broker, journal = FakeBroker(50), FakeJournal()
    broker.recovery_snapshot = lambda **kwargs: RecoverySnapshot(
        account_id="another-account", broker="zerodha", authoritative=True,
        positions_reconciled=True, position_attribution_complete=True,
        orders=(), positions=(RecoveryPosition("NFO:CE", 50),),
    )
    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert not journal.released


def test_non_authoritative_snapshot_is_never_treated_as_flat():
    broker, journal = FakeBroker(0), FakeJournal()
    broker.recovery_snapshot = lambda **kwargs: RecoverySnapshot(
        account_id="acct-1", broker="angel_one", authoritative=False,
        positions_reconciled=False, position_attribution_complete=False, orders=(), positions=(),
    )
    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not journal.released


def test_insufficient_depth_for_one_lot_fails_closed_without_market_order():
    broker, journal = FakeBroker(50), FakeJournal()
    broker.recovery_quote = lambda **kwargs: RecoveryQuote(
        bid=Decimal("100"), ask=Decimal("100.10"), bid_depth=10, ask_depth=10,
        tick_size=Decimal("0.05"), lot_size=25, observed_at_ns=1_000_000_000,
    )
    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert not journal.released


def test_recovery_closes_only_position_delta_above_pre_entry_baseline():
    broker, journal = FakeBroker(75), FakeJournal()
    result = run(
        broker, journal,
        baseline=(RecoveryPosition("NFO:CE", 25),),
    )

    assert result.state is RecoveryState.FLAT
    assert broker.closes[0].quantity == 50
    assert broker.position == 25


def test_reconciled_flag_is_required_even_when_snapshot_is_authoritative():
    broker, journal = FakeBroker(50), FakeJournal()
    original = broker.recovery_snapshot
    broker.recovery_snapshot = lambda **kwargs: RecoverySnapshot(
        account_id="acct-1", broker="angel_one", authoritative=True,
        positions_reconciled=False, position_attribution_complete=False,
        orders=(), positions=(RecoveryPosition("NFO:CE", 50),),
    )
    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert not journal.released


def test_duplicate_position_rows_are_aggregated_before_baseline_delta():
    broker, journal = FakeBroker(50), FakeJournal()
    broker.recovery_snapshot = lambda **kwargs: RecoverySnapshot(
        account_id="acct-1", broker="angel_one", authoritative=True,
        positions_reconciled=True, position_attribution_complete=True,
        orders=(RecoveryOrder("entry-1", "entry-client-1", "NFO:CE", "ENTRY", "CANCELLED", 0),),
        positions=(
            RecoveryPosition("NFO:CE", broker.position // 2),
            RecoveryPosition("NFO:CE", broker.position - broker.position // 2),
        ),
    )
    result = run(broker, journal)
    assert result.state is RecoveryState.FLAT
    assert broker.closes[0].quantity == 50


def test_recovery_requires_baseline_for_every_symbol_before_any_side_effect():
    broker, journal = FakeBroker(50), FakeJournal()
    coordinator = EmergencyRecoveryCoordinator(broker, journal)
    try:
        coordinator.recover(
            account_id="acct-1", intent_id="intent-1", strategy="BOX_SPREAD",
            symbols=("NFO:CE", "NFO:PE"), baseline_positions=(RecoveryPosition("NFO:CE", 0),),
        )
    except ValueError as exc:
        assert "durable pre-entry baseline" in str(exc)
    else:
        raise AssertionError("missing baseline must fail closed")
    assert not journal.kill_switches
    assert not broker.closes


def test_non_terminal_emergency_close_is_escalated_without_retry():
    broker, journal = FakeBroker(50), FakeJournal()
    original = broker.place_ioc_limit
    def working_close(*, account_id, order):
        broker.closes.append(order)
        return RecoveryOrder(
            broker_order_id="close-working", client_order_id=order.client_order_id,
            symbol=order.symbol, role="RECOVERY", status="WORKING", remaining_quantity=order.quantity,
        )
    broker.place_ioc_limit = working_close
    result = run(broker, journal)
    assert result.state is RecoveryState.MANUAL_REVIEW
    assert len(broker.closes) == 1
    assert not journal.released


def test_partial_fill_without_remaining_quantity_is_not_assumed_terminal():
    order = RecoveryOrder(
        broker_order_id="partial-unknown", client_order_id="partial-client",
        symbol="NFO:CE", role="ENTRY", status="PARTIALLY_FILLED",
    )
    assert not order.terminal


def test_duplicate_baseline_rows_are_rejected_before_recovery():
    broker, journal = FakeBroker(50), FakeJournal()
    coordinator = EmergencyRecoveryCoordinator(broker, journal)
    try:
        coordinator.recover(
            account_id="acct-1", intent_id="intent-1", strategy="BOX_SPREAD",
            symbols=("NFO:CE",),
            baseline_positions=(RecoveryPosition("NFO:CE", 0), RecoveryPosition("NFO:CE", 0)),
        )
    except ValueError as exc:
        assert "one row per instrument" in str(exc)
    else:
        raise AssertionError("duplicate baseline rows must fail closed")
    assert not broker.closes


def test_recovery_freezes_intent_before_cancellation_or_close():
    broker, journal = FakeBroker(50), FakeJournal()
    result = run(broker, journal)
    assert result.state is RecoveryState.FLAT
    assert journal.frozen == [{
        "account_id": "acct-1", "intent_id": "intent-1",
        "reason": "stop all unsent basket legs and retries",
    }]


def test_missing_exclusive_instrument_lease_blocks_all_recovery_orders():
    broker, journal = FakeBroker(50), FakeJournal()
    journal.leases = False
    result = run(broker, journal)
    assert result.state is RecoveryState.MANUAL_REVIEW
    assert broker.cancelled == ["entry-1"]
    assert not broker.closes
    assert result.residual_positions == (RecoveryPosition("NFO:CE", 50),)
    assert not journal.released


def test_incomplete_position_attribution_blocks_recovery():
    broker, journal = FakeBroker(50), FakeJournal()
    broker.recovery_snapshot = lambda **kwargs: RecoverySnapshot(
        account_id="acct-1", broker="angel_one", authoritative=True,
        positions_reconciled=True, position_attribution_complete=False,
        orders=(), positions=(RecoveryPosition("NFO:CE", 50),),
    )
    result = run(broker, journal)
    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert not journal.released


def test_cancel_error_does_not_prevent_cancelling_other_working_legs():
    broker, journal = FakeBroker(50), FakeJournal()
    broker.orders.append(RecoveryOrder("entry-2", "entry-client-2", "NFO:PE", "ENTRY", "WORKING", 50))
    def cancel_with_one_failure(*, account_id, broker_order_id):
        broker.cancelled.append(broker_order_id)
        if broker_order_id == "entry-1":
            raise RuntimeError("simulated cancel timeout")
        broker.orders[1] = RecoveryOrder(
            "entry-2", "entry-client-2", "NFO:PE", "ENTRY", "CANCELLED", 0,
        )
    broker.cancel_order = cancel_with_one_failure
    result = run(broker, journal)
    assert result.state is RecoveryState.MANUAL_REVIEW
    assert broker.cancelled == ["entry-1", "entry-2"]
    assert not broker.closes
    assert any(event["event"] == "ENTRY_CANCEL_ERROR" for event in journal.events)


def test_recovery_budget_must_approve_close_before_broker_submission():
    broker, journal = FakeBroker(50), FakeJournal()
    journal.approve_close = False
    result = run(broker, journal)
    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert journal.approved_closes
    assert not journal.released


def test_lost_close_ack_without_broker_order_visibility_blocks_resubmission():
    broker, journal = FakeBroker(50), FakeJournal()
    journal.pending_authorizations = ("REC-previously-authorized",)
    result = run(broker, journal)
    assert result.state is RecoveryState.MANUAL_REVIEW
    assert not broker.closes
    assert not journal.released
    assert "not yet reconciled" in result.reason


def test_terminal_prior_close_is_reconciled_before_new_bounded_attempt():
    broker, journal = FakeBroker(50), FakeJournal()
    journal.pending_authorizations = ("REC-previously-authorized",)
    broker.orders.append(RecoveryOrder(
        broker_order_id="close-previous", client_order_id="REC-previously-authorized",
        symbol="NFO:CE", role="RECOVERY", status="CANCELLED", remaining_quantity=0,
    ))
    result = run(broker, journal, policy=RecoveryPolicy(max_attempts=2))
    assert result.state is RecoveryState.FLAT
    assert journal.reconciled_authorizations == ["REC-previously-authorized"]
    assert len(broker.closes) == 1


def test_restart_manual_review_returns_controlled_result_without_broker_actions():
    broker, journal = FakeBroker(50), FakeJournal()
    journal.current_state = RecoveryState.MANUAL_REVIEW

    result = run(broker, journal)

    assert result.state is RecoveryState.MANUAL_REVIEW
    assert "manual review cannot resume" in result.reason
    assert not broker.cancelled
    assert not broker.closes
    assert journal.alerts
