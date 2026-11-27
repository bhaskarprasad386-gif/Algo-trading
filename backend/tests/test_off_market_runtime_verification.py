from app.market_data.common_strategy_feed import CommonStrategyMarketFeed, replay_cached_snapshots
from app.market_data.shared_cache import get_shared_market_data_cache


def test_offmarket_replay_accepts_only_snapshots_inside_freshness_and_future_skew(monkeypatch):
    import app.market_data.common_strategy_feed as feed_module

    now_ns = 1_800_000_000_000_000_000
    monkeypatch.setattr(feed_module, "time_ns", lambda: now_ns)
    cache = get_shared_market_data_cache()
    cache.clear()
    descriptors = [
        CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token="offmarket-fresh", symbol="FRESH-EQ",
            instrument_type="equity", segment="NSE",
        ),
        CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token="offmarket-stale", symbol="STALE-EQ",
            instrument_type="equity", segment="NSE",
        ),
        CommonStrategyMarketFeed.descriptor(
            exchange="NSE", token="offmarket-future", symbol="FUTURE-EQ",
            instrument_type="equity", segment="NSE",
        ),
    ]
    try:
        cache.put(descriptors[0].key.value, {"timestamp_ns": now_ns + 500_000_000, "ltp": 101.0})
        cache.put(descriptors[1].key.value, {"timestamp_ns": now_ns - 6_000_000_000, "ltp": 99.0})
        cache.put(descriptors[2].key.value, {"timestamp_ns": now_ns + 1_500_000_000, "ltp": 102.0})

        received = []
        count = replay_cached_snapshots(descriptors, received.append)

        assert count == 1
        assert len(received) == 1
        assert received[0].symbol == "FRESH-EQ"
        assert received[0].ltp == 101.0
    finally:
        cache.clear()


def test_offmarket_replay_rejects_invalid_freshness_bounds():
    import pytest

    descriptor = CommonStrategyMarketFeed.descriptor(
        exchange="NSE", token="offmarket-invalid-bounds", symbol="INVALID-EQ",
        instrument_type="equity", segment="NSE",
    )
    with pytest.raises(ValueError, match="freshness bounds"):
        replay_cached_snapshots([descriptor], lambda _record: None, max_age_seconds=0)
    with pytest.raises(ValueError, match="freshness bounds"):
        replay_cached_snapshots([descriptor], lambda _record: None, max_future_skew_seconds=-0.1)


def test_offmarket_runtime_workflow_uses_bounded_sqlite_quick_check():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "offmarket-runtime-verification.yml"
    ).read_text()

    assert 'timeout 15s sqlite3 -batch -noheader backend/algo_trading.db "PRAGMA quick_check(1);"' in workflow
    assert "PRAGMA integrity_check;" not in workflow
    assert "SQLITE_QUICK_CHECK=PASS" in workflow
    assert "SQLITE_QUICK_CHECK_FAILED" in workflow


def test_offmarket_runtime_workflow_does_not_replay_remote_diagnostics():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "offmarket-runtime-verification.yml"
    ).read_text()

    assert "timeout 150 ssh" in workflow
    assert "'timeout 120s bash -s'" in workflow
    assert "VPS_SSH_ATTEMPT=$attempt FAILED" not in workflow
    assert "remote diagnostics were not replayed" in workflow


def test_offmarket_runtime_workflow_bounds_informational_journal_scan():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "offmarket-runtime-verification.yml"
    ).read_text()

    assert "timeout 10s journalctl" in workflow
    assert "RECENT_SERVICE_FAULTS=SKIPPED" in workflow



def test_offmarket_vps_verification_is_intentionally_skipped_for_pull_requests():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "offmarket-runtime-verification.yml"
    ).read_text()

    assert "verify-vps-runtime:" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow
    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow


def test_offmarket_vps_verification_is_read_only_and_has_no_deploy_or_restart_steps():
    from pathlib import Path
    import re

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "offmarket-runtime-verification.yml"
    ).read_text()

    remote = workflow.split("REMOTE_SCRIPT=$(cat <<'REMOTE'", 1)[1].split("\n          REMOTE", 1)[0]
    mutating_commands = re.compile(
        r"(?im)^\s*(?:systemctl\s+(?:restart|stop|disable|enable)|"
        r"git\s+(?:pull|checkout|reset)|alembic\s+upgrade|"
        r"(?:DROP|DELETE|INSERT|UPDATE)\s+(?:TABLE|FROM|INTO|[A-Z_]+))"
    )
    assert mutating_commands.search(remote) is None

    assert "systemctl is-active algo-trading.service" in remote
    assert "PRAGMA quick_check(1);" in remote
    assert "journalctl" in remote


def test_offmarket_remote_verification_has_one_bounded_ssh_execution():
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "offmarket-runtime-verification.yml"
    ).read_text()

    assert workflow.count("timeout 150 ssh") == 1
    assert "'timeout 120s bash -s'" in workflow
    assert "VPS_SSH_ATTEMPT=" not in workflow
