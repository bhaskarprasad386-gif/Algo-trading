from types import SimpleNamespace

from app.execution import recovery_coordinator as coordinator


class _Adapter:
    name = "zerodha"
    connected = True
    account_id = "acct-1"

    def __init__(self, history):
        self.history = history
        self.fetch_count = 0

    def fetch_today_broker_snapshot(self, *, account_id):
        assert account_id == "acct-1"
        self.fetch_count += 1
        return self.history


def _candidate(intent_id):
    return SimpleNamespace(
        persisted=SimpleNamespace(
            intent=SimpleNamespace(intent_id=intent_id, account_id="acct-1")
        )
    )


def _history(*, account_id="acct-1", broker="zerodha", scope="CURRENT_TRADING_DAY", complete=True):
    snapshot = SimpleNamespace(
        account_id=account_id,
        broker=broker,
        complete=complete,
        orders=(),
        trades=(),
    )
    return SimpleNamespace(
        scope=scope,
        trading_date="2026-11-16",
        snapshot=snapshot,
    )


def test_recovery_coordinator_does_not_fetch_when_no_intents(monkeypatch):
    adapter = _Adapter(_history())
    monkeypatch.setattr(coordinator, "list_recovery_candidates", lambda *a, **k: ())
    result = coordinator.review_account_recovery(
        object(), account_id="acct-1", broker="zerodha", adapter=adapter,
    )
    assert result["status"] == "NO_PENDING_INTENTS"
    assert result["trading_authorized"] is False
    assert adapter.fetch_count == 0


def test_recovery_coordinator_fetches_one_snapshot_for_all_candidates(monkeypatch):
    adapter = _Adapter(_history())
    candidates = (_candidate("intent-a"), _candidate("intent-b"))
    monkeypatch.setattr(
        coordinator, "list_recovery_candidates", lambda *a, **k: candidates,
    )
    monkeypatch.setattr(
        coordinator, "load_recovery_ledger_evidence",
        lambda *a, **k: SimpleNamespace(local_orders=(), local_fill_quantities={}),
    )
    monkeypatch.setattr(
        coordinator, "review_recovery_candidate",
        lambda **kwargs: SimpleNamespace(
            disposition=SimpleNamespace(value="CONSISTENT_READ_ONLY"),
            matched_order_count=2,
            issues=(),
        ),
    )

    result = coordinator.review_account_recovery(
        object(), account_id="acct-1", broker="zerodha", adapter=adapter,
    )
    assert adapter.fetch_count == 1
    assert result["status"] == "REVIEWED"
    assert result["candidate_count"] == 2
    assert result["consistent_read_only_count"] == 2
    assert result["trading_authorized"] is False


def test_recovery_coordinator_blocks_wrong_adapter_identity(monkeypatch):
    adapter = _Adapter(_history())
    adapter.name = "fyers"
    monkeypatch.setattr(
        coordinator, "list_recovery_candidates",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not query ledger")),
    )
    result = coordinator.review_account_recovery(
        object(), account_id="acct-1", broker="zerodha", adapter=adapter,
    )
    assert result["reason"] == "BROKER_SESSION_IDENTITY_MISMATCH"
    assert result["trading_authorized"] is False
    assert adapter.fetch_count == 0


def test_recovery_coordinator_blocks_snapshot_account_scope_mismatch(monkeypatch):
    adapter = _Adapter(_history(account_id="different-account"))
    monkeypatch.setattr(
        coordinator, "list_recovery_candidates", lambda *a, **k: (_candidate("intent-a"),),
    )
    result = coordinator.review_account_recovery(
        object(), account_id="acct-1", broker="zerodha", adapter=adapter,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "BROKER_HISTORY_ACCOUNT_SCOPE_MISMATCH"
    assert result["trading_authorized"] is False


def test_recovery_coordinator_blocks_unsupported_history_scope(monkeypatch):
    adapter = _Adapter(_history(scope="UNKNOWN"))
    monkeypatch.setattr(
        coordinator, "list_recovery_candidates", lambda *a, **k: (_candidate("intent-a"),),
    )
    result = coordinator.review_account_recovery(
        object(), account_id="acct-1", broker="zerodha", adapter=adapter,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "BROKER_HISTORY_SCOPE_UNSUPPORTED"
    assert result["trading_authorized"] is False


def test_recovery_coordinator_blocks_incomplete_broker_snapshot(monkeypatch):
    adapter = _Adapter(_history(complete=False))
    monkeypatch.setattr(
        coordinator, "list_recovery_candidates", lambda *a, **k: (_candidate("intent-a"),),
    )
    result = coordinator.review_account_recovery(
        object(), account_id="acct-1", broker="zerodha", adapter=adapter,
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "BROKER_HISTORY_SNAPSHOT_INCOMPLETE"
    assert result["trading_authorized"] is False
    assert result["reviews"] == []
