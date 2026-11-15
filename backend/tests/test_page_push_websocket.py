from types import SimpleNamespace

from app.scanner import page_push_websocket as push


def test_page_channels_are_explicit_and_strategy_scoped():
    assert "box-spread" in push.PAGE_CHANNELS
    assert "iv-skew-flip" in push.PAGE_CHANNELS
    assert "credit-butterfly-condor-weekly" in push.PAGE_CHANNELS
    assert "credit-butterfly-condor-monthly" in push.PAGE_CHANNELS
    assert "synthetic-arbitrage" not in push.PAGE_CHANNELS


def test_frames_have_monotonic_per_page_sequences():
    page = "box-spread"
    before = push._sequences.get(page, 0)
    first = push._frame(page, "SNAPSHOT", {"opportunities": []})
    second = push._frame(page, "PATCH", {"opportunities": [{"symbol": "NIFTY"}]})

    assert first["type"] == "page_update"
    assert first["page"] == page
    assert first["event"] == "SNAPSHOT"
    assert second["sequence"] == first["sequence"] + 1
    assert first["sequence"] == before + 1
    assert first["generated_at"]
    assert second["data"]["opportunities"][0]["symbol"] == "NIFTY"


def test_snapshot_provider_is_read_only_and_page_scoped():
    observed = []
    push.configure_snapshot_provider("credit-butterfly-condor-weekly", lambda: observed.append("read") or {"rows": []})
    provider = push._snapshot_providers["credit-butterfly-condor-weekly"]

    assert provider() == {"rows": []}
    assert observed == ["read"]
    try:
        push.configure_snapshot_provider("unknown-page", lambda: {})
    except ValueError as exc:
        assert "unknown page" in str(exc)
    else:
        raise AssertionError("unknown page must be rejected")


def test_origin_policy_rejects_untrusted_hosts():
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "https://algo-trading.vercel.app"}))
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "http://localhost:3000"}))
    assert not push._origin_allowed(SimpleNamespace(headers={"origin": "https://evil.example"}))
    assert push._origin_allowed(SimpleNamespace(headers={}))



def test_resync_snapshot_does_not_advance_sequence_for_other_clients():
    page = "iv-skew-flip"
    push._sequences[page] = 41
    snapshot = push._snapshot_frame(page, {"status": "RUNNING"})
    next_patch = push._frame(page, "PATCH", {"status": "RUNNING"})

    assert snapshot["sequence"] == 41
    assert next_patch["sequence"] == 42
