from app.core.dashboard_stream_telemetry import DashboardStreamTelemetry


def test_dashboard_stream_telemetry_tracks_build_fanout_and_client_health():
    telemetry = DashboardStreamTelemetry()

    telemetry.record_client_send(success=True, duration_seconds=0.12)
    telemetry.record_client_send(success=False, duration_seconds=0.81)
    telemetry.record_publish(
        client_count=3,
        sent_count=2,
        failed_count=1,
        build_seconds=0.25,
        fanout_seconds=0.76,
        build_dispatch_wait_seconds=0.18,
        serialization_seconds=0.03,
        payload_bytes=4096,
        cycle_seconds=1.2,
    )

    snapshot = telemetry.snapshot()
    assert snapshot["cycles"] == 1
    assert snapshot["publish_errors"] == 0
    assert snapshot["client_send_successes"] == 1
    assert snapshot["client_send_failures"] == 1
    assert snapshot["last_client_count"] == 3
    assert snapshot["last_sent_count"] == 2
    assert snapshot["last_failed_count"] == 1
    assert snapshot["last_build_seconds"] == 0.25
    assert snapshot["last_build_dispatch_wait_seconds"] == 0.18
    assert snapshot["max_build_dispatch_wait_seconds"] == 0.18
    assert snapshot["last_fanout_seconds"] == 0.76
    assert snapshot["last_serialization_seconds"] == 0.03
    assert snapshot["max_serialization_seconds"] == 0.03
    assert snapshot["last_payload_bytes"] == 4096
    assert snapshot["last_cycle_seconds"] == 1.2
    assert snapshot["max_cycle_seconds"] == 1.2
    assert snapshot["cycle_overrun_count"] == 1
    assert snapshot["max_client_send_seconds"] == 0.81
    assert snapshot["seconds_since_last_success"] is not None


def test_dashboard_stream_telemetry_records_publish_failures_without_payload_data():
    telemetry = DashboardStreamTelemetry()
    telemetry.record_publish(
        client_count=2,
        sent_count=0,
        failed_count=2,
        build_seconds=1.5,
        fanout_seconds=0.0,
        error_type="RuntimeError: private detail must not be stored",
    )

    snapshot = telemetry.snapshot()
    assert snapshot["publish_errors"] == 1
    assert snapshot["last_error_type"] == "RuntimeError"
    assert snapshot["last_client_count"] == 2
    assert snapshot["last_failed_count"] == 2
    assert "private detail" not in repr(snapshot)
    assert snapshot["seconds_since_last_success"] is None
