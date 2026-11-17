from types import SimpleNamespace

from app.execution.broker_reconciliation import BrokerSnapshot
from app.execution.recovery_review import RecoveryDisposition
from app.execution import recovery_snapshot as module


def _candidate():
    return SimpleNamespace(
        persisted=SimpleNamespace(
            intent=SimpleNamespace(account_id="acct-1", intent_id="intent-1")
        )
    )


class _Adapter:
    name = "zerodha"
    connected = True
    account_id = "acct-1"

    def __init__(self, history=None, error=None):
        self.history = history
        self.error = error
        self.accounts = []

    def fetch_today_broker_snapshot(self, *, account_id):
        self.accounts.append(account_id)
        if self.error:
            raise self.error
        return self.history


def test_orchestrator_calls_read_only_history_for_candidate_account(monkeypatch):
    snapshot = BrokerSnapshot("acct-1", "zerodha", (), (), True)
    adapter = _Adapter(SimpleNamespace(
        scope="CURRENT_TRADING_DAY", trading_date="2026-11-16",
        snapshot=snapshot,
    ))
    evidence = SimpleNamespace(local_orders=(), local_fill_quantities={})
    monkeypatch.setattr(module, "load_recovery_ledger_evidence", lambda *a, **k: evidence)
    review = SimpleNamespace(
        disposition=RecoveryDisposition.BLOCKED, is_consistent=False,
    )
    monkeypatch.setattr(module, "review_recovery_candidate", lambda **kwargs: review)

    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker="ZERODHA", adapter=adapter,
    )

    assert adapter.accounts == ["acct-1"]
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.reason == "RECONCILIATION_REQUIRES_REVIEW"
    assert result.trading_date == "2026-11-16"


def test_adapter_failure_fails_closed_without_ledger_or_review(monkeypatch):
    def must_not_load(*args, **kwargs):
        raise AssertionError("ledger must not be consulted after history failure")

    monkeypatch.setattr(module, "load_recovery_ledger_evidence", must_not_load)
    adapter = _Adapter(error=RuntimeError("provider response contains secrets"))
    adapter.name = "fyers"
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker="fyers", adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.review is None
    assert result.reason == "BROKER_HISTORY_FETCH_FAILED"


def test_unsupported_history_scope_fails_closed(monkeypatch):
    def must_not_load(*args, **kwargs):
        raise AssertionError("unsupported scope must not be reconciled")

    monkeypatch.setattr(module, "load_recovery_ledger_evidence", must_not_load)
    adapter = _Adapter(SimpleNamespace(
        scope="MULTI_DAY_UNVERIFIED", trading_date="2026-11-16",
        snapshot=BrokerSnapshot("acct-1", "mstock", (), (), True),
    ))
    adapter.name = "mstock"
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker="mstock",
        adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.reason == "BROKER_HISTORY_SCOPE_UNSUPPORTED"


def test_incomplete_current_day_snapshot_remains_blocked(monkeypatch):
    snapshot = BrokerSnapshot("acct-1", "shoonya", (), (), False)
    evidence = SimpleNamespace(local_orders=(), local_fill_quantities={})
    monkeypatch.setattr(
        module, "load_recovery_ledger_evidence",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("incomplete snapshot must not read ledger")),
    )
    adapter = _Adapter(SimpleNamespace(
        scope="CURRENT_TRADING_DAY", trading_date="2026-11-16",
        snapshot=snapshot,
    ))
    adapter.name = "shoonya"
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker="shoonya", adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.review is None
    assert result.reason == "BROKER_HISTORY_INCOMPLETE_OR_SCOPE_MISMATCH"



def test_wrong_authenticated_session_identity_blocks_before_provider_or_ledger(monkeypatch):
    adapter = _Adapter(SimpleNamespace(scope="CURRENT_TRADING_DAY", snapshot=None))
    adapter.name = "fyers"
    monkeypatch.setattr(
        module, "load_recovery_ledger_evidence",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not read ledger")),
    )
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker="zerodha", adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.reason == "BROKER_SESSION_IDENTITY_MISMATCH"
    assert adapter.accounts == []


def test_disconnected_or_wrong_account_session_blocks_before_provider(monkeypatch):
    for attr, value, expected in (
        ("connected", False, "BROKER_SESSION_NOT_CONNECTED"),
        ("account_id", "other-account", "BROKER_SESSION_ACCOUNT_MISMATCH"),
    ):
        adapter = _Adapter(SimpleNamespace(scope="CURRENT_TRADING_DAY", snapshot=None))
        setattr(adapter, attr, value)
        result = module.review_with_broker_history(
            object(), candidate=_candidate(), broker="zerodha", adapter=adapter,
        )
        assert result.disposition is RecoveryDisposition.BLOCKED
        assert result.reason == expected
        assert adapter.accounts == []


def test_wrong_snapshot_scope_blocks_before_ledger(monkeypatch):
    adapter = _Adapter(SimpleNamespace(
        scope="CURRENT_TRADING_DAY", trading_date="2026-11-16",
        snapshot=BrokerSnapshot("acct-2", "zerodha", (), (), True),
    ))
    monkeypatch.setattr(
        module, "load_recovery_ledger_evidence",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not read ledger")),
    )
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker="zerodha", adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.reason == "BROKER_HISTORY_ACCOUNT_SCOPE_MISMATCH"


@pytest.mark.parametrize("broker", ["angel_one", "fyers", "mstock", "shoonya", "zerodha"])
def test_wrong_snapshot_broker_blocks_before_ledger_for_every_broker(monkeypatch, broker):
    adapter = _Adapter(SimpleNamespace(
        scope="CURRENT_TRADING_DAY", trading_date="2026-11-16",
        snapshot=BrokerSnapshot("acct-1", "different-broker", (), (), True),
    ))
    adapter.name = broker
    monkeypatch.setattr(
        module, "load_recovery_ledger_evidence",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("wrong broker snapshot must not read ledger")),
    )
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker=broker, adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.review is None
    assert result.reason == "BROKER_HISTORY_ACCOUNT_SCOPE_MISMATCH"


@pytest.mark.parametrize("broker", ["angel_one", "fyers", "mstock", "shoonya", "zerodha"])
def test_disconnected_authenticated_session_blocks_for_every_broker(monkeypatch, broker):
    adapter = _Adapter(SimpleNamespace(
        scope="CURRENT_TRADING_DAY", trading_date="2026-11-16",
        snapshot=BrokerSnapshot("acct-1", broker, (), (), True),
    ))
    adapter.name = broker
    adapter.connected = False
    monkeypatch.setattr(
        module, "load_recovery_ledger_evidence",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disconnected session must not read ledger")),
    )
    result = module.review_with_broker_history(
        object(), candidate=_candidate(), broker=broker, adapter=adapter,
    )
    assert result.disposition is RecoveryDisposition.BLOCKED
    assert result.review is None
    assert result.reason == "BROKER_SESSION_NOT_CONNECTED"
    assert adapter.accounts == []
