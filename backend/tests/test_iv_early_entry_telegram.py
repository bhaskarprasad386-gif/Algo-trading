import json

from app.core.config import settings
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"ok": true, "result": {"message_id": 123}}'


def test_telegram_sender_posts_html_message_without_exposing_token(monkeypatch):
    captured = {}
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-secret-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "-100123")
    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()
    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen", fake_urlopen,
    )

    IVSkewFlipRuntime._telegram_sender({
        "event_id": 7, "strategy": "IV_VOLUME_MOMENTUM",
        "underlying": "NIFTY", "expiry": "2026-11-19",
        "strike": 25000, "direction": "BULLISH", "option_type": "CE",
        "state": "EARLY_ENTRY", "spot": 25001, "premium": 105.5,
        "iv_change_points": 0.25, "volume_ratio": 3.0,
        "reason": "IV + volume <confirmed>",
        "observed_at_ns": 1_794_000_000_000_000_000,
    })

    assert captured["url"].endswith("/sendMessage")
    assert "test-secret-token" in captured["url"]
    assert captured["timeout"] == 5
    assert captured["body"]["chat_id"] == "-100123"
    assert captured["body"]["parse_mode"] == "HTML"
    assert "&lt;confirmed&gt;" in captured["body"]["text"]
    assert "no order placed" in captured["body"]["text"]


def test_telegram_sender_fails_closed_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", False)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 1})
    except RuntimeError as exc:
        assert str(exc) == "TELEGRAM_CONFIGURATION_MISSING"
    else:
        raise AssertionError("Telegram sender must reject missing configuration")


def test_telegram_sender_never_exposes_bot_token_in_transport_error(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "sensitive-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")
    def failed_request(_request, timeout):
        raise OSError("request failed for https://api.telegram.org/bot/sensitive-token/sendMessage")
    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen", failed_request,
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 9})
    except RuntimeError as exc:
        assert str(exc) == "TELEGRAM_DELIVERY_OUTCOME_UNKNOWN"
        assert "sensitive-token" not in str(exc)
    else:
        raise AssertionError("transport failure must be reported safely")
