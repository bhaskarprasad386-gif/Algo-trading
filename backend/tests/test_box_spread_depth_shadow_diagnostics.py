from app.scanner.box_spread_depth_shadow_diagnostics import BoxDepthShadowDiagnostics


def test_depth_shadow_diagnostics_aggregate_complete_and_incomplete_results():
    diagnostics = BoxDepthShadowDiagnostics(sample_limit=3)
    diagnostics.record({"status": "COMPLETE", "difference_per_unit": 1.5})
    diagnostics.record({"status": "COMPLETE", "difference_per_unit": -0.5})
    diagnostics.record({"status": "INCOMPLETE", "reason": "STALE_PROVIDER_LOW_CE"})
    diagnostics.record({"status": "INCOMPLETE", "reason": "STALE_PROVIDER_LOW_CE"})

    snapshot = diagnostics.snapshot()
    assert snapshot["status_counts"] == {"COMPLETE": 2, "INCOMPLETE": 2}
    assert snapshot["incomplete_reasons"] == {"STALE_PROVIDER_LOW_CE": 2}
    assert snapshot["complete_difference_sample"] == {
        "window": "latest_bounded_samples",
        "limit": 3,
        "count": 2,
        "mean_per_unit": 0.5,
        "min_per_unit": -0.5,
        "max_per_unit": 1.5,
    }


def test_depth_shadow_diagnostics_bound_samples_and_ignore_non_finite_values():
    diagnostics = BoxDepthShadowDiagnostics(sample_limit=2)
    diagnostics.record({"status": "COMPLETE", "difference_per_unit": 1})
    diagnostics.record({"status": "COMPLETE", "difference_per_unit": 2})
    diagnostics.record({"status": "COMPLETE", "difference_per_unit": 3})
    diagnostics.record({"status": "COMPLETE", "difference_per_unit": float("nan")})

    sample = diagnostics.snapshot()["complete_difference_sample"]
    assert sample["count"] == 2
    assert sample["mean_per_unit"] == 2.5
    assert sample["min_per_unit"] == 2
    assert sample["max_per_unit"] == 3
