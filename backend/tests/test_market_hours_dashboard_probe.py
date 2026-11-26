import importlib.util
from pathlib import Path


_PROBE_PATH = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "market-hours-dashboard-probe.py"
)
_SPEC = importlib.util.spec_from_file_location("market_hours_dashboard_probe", _PROBE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def test_percentile_uses_linear_interpolation():
    assert probe.percentile([0.0, 10.0, 20.0, 30.0], 50) == 15.0
    assert probe.percentile([], 95) is None


def test_safe_health_fields_only_keeps_bounded_diagnostic_values():
    payload = {
        "database_pool": {
            "checkedout": 2,
            "capacity": 10,
            "checkout_wait_count": 5,
            "password": "must-not-escape",
        },
        "event_loop": {"p95_lag_bucket": "le_0.05s", "max_lag_seconds": 0.03, "secret": "must-not-escape"},
        "runtime_feed": {
            "status": "STALE",
            "age_seconds": 7.0,
            "exchange_freshness": {"NSE": {"status": "STALE"}},
            "raw_tick": "must-not-escape",
        },
    }
    result = probe._safe_health_fields("/api/v1/market-data/live-health", payload)

    assert result["database_pool"] == {
        "checkedout": 2, "capacity": 10, "checkout_wait_count": 5
    }
    assert result["event_loop"] == {
        "max_lag_seconds": 0.03, "p95_lag_bucket": "le_0.05s"
    }
    assert result["feed_freshness"]["status"] == "STALE"
    assert "password" not in str(result)
    assert "must-not-escape" not in str(result)


def test_run_probe_uses_bounded_endpoint_batch_and_summarizes(monkeypatch):
    def fake_probe(base_url, path, timeout, token):
        return {
            "endpoint": path,
            "latency_ms": 12.5,
            "status_code": 200,
            "error": None,
            "health": {},
        }

    monkeypatch.setattr(probe, "probe_one", fake_probe)
    result = probe.run_probe(
        "https://example.invalid",
        duration_seconds=0.01,
        interval_seconds=2,
        timeout_seconds=0.1,
        concurrency=2,
    )

    assert result["batches"] == 1
    assert result["total_requests"] == len(probe.ENDPOINTS)
    assert result["total_errors"] == 0
    assert result["endpoints"]["/health/live"]["p50_ms"] == 12.5
    assert result["max_concurrency"] == 2


def test_run_probe_rejects_aggressive_concurrency():
    try:
        probe.run_probe(
            "https://example.invalid",
            duration_seconds=1,
            interval_seconds=2,
            timeout_seconds=1,
            concurrency=20,
        )
    except ValueError as exc:
        assert "between 1 and 4" in str(exc)
    else:
        raise AssertionError("aggressive concurrency must be rejected")
