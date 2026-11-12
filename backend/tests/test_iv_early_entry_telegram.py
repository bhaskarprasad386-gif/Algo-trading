import json
from urllib.error import HTTPError

from app.core.config import settings
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime
from app.scanner.iv_skew_flip.alerts.notifications import TelegramRejectedError


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



def test_telegram_sender_rejects_http_200_with_provider_ok_false(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")

    class RejectedResponse(FakeResponse):
        def read(self):
            return b'{"ok": false, "description": "chat not found"}'

    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen",
        lambda _request, timeout: RejectedResponse(),
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 12})
    except TelegramRejectedError as exc:
        assert str(exc) == "TELEGRAM_API_REJECTED"
        assert "chat not found" not in str(exc)
    else:
        raise AssertionError("Telegram ok=false must not be marked as sent")


def test_telegram_sender_classifies_client_rejection_as_permanent(monkeypatch):
    from app.scanner.iv_skew_flip.alerts.notifications import TelegramRejectedError

    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen",
        lambda _request, timeout: (_ for _ in ()).throw(HTTPError(
            "https://api.telegram.org/bot/test-token/sendMessage",
            400, "Bad Request", {}, None,
        )),
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 15})
    except TelegramRejectedError as exc:
        assert str(exc) == "TELEGRAM_API_REJECTED"
        assert "test-token" not in str(exc)
    else:
        raise AssertionError("Telegram 4xx client error must be terminal")


def test_telegram_sender_classifies_provider_server_error_as_retryable(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen",
        lambda _request, timeout: (_ for _ in ()).throw(HTTPError(
            "https://api.telegram.org/bot/test-token/sendMessage",
            500, "Internal Server Error", {}, None,
        )),
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 16})
    except RuntimeError as exc:
        assert str(exc) == "TELEGRAM_PROVIDER_TEMPORARY_FAILURE"
        assert "test-token" not in str(exc)
    else:
        raise AssertionError("Telegram 5xx must remain retryable")


def test_telegram_sender_classifies_429_and_preserves_retry_after(monkeypatch):
    from app.scanner.iv_skew_flip.alerts.notifications import TelegramRateLimitError

    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")

    def rate_limited(_request, timeout):
        raise HTTPError(
            "https://api.telegram.org/bot/test-token/sendMessage",
            429, "Too Many Requests", {"Retry-After": "30"}, None,
        )

    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen", rate_limited,
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 13})
    except TelegramRateLimitError as exc:
        assert exc.retry_after_seconds == 30
        assert "test-token" not in str(exc)
    else:
        raise AssertionError("HTTP 429 must be classified as retryable rate limit")



def test_telegram_sender_reads_retry_after_from_bot_api_error_parameters(monkeypatch):
    from app.scanner.iv_skew_flip.alerts.notifications import TelegramRateLimitError

    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")

    class ErrorBody:
        def read(self):
            return b'{"ok": false, "parameters": {"retry_after": 42}}'

        def close(self):
            pass

    def rate_limited(_request, timeout):
        raise HTTPError(
            "https://api.telegram.org/bot/test-token/sendMessage",
            429, "Too Many Requests", {}, ErrorBody(),
        )

    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen", rate_limited,
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 14})
    except TelegramRateLimitError as exc:
        assert exc.retry_after_seconds == 42
    else:
        raise AssertionError("Telegram retry_after response parameter must be honored")



def test_telegram_sender_classifies_http_4xx_as_permanent_rejection(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")

    def rejected(_request, timeout):
        raise HTTPError(
            "https://api.telegram.org/bot/test-token/sendMessage",
            403, "Forbidden", {}, None,
        )

    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen", rejected,
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 15})
    except TelegramRejectedError as exc:
        assert str(exc) == "TELEGRAM_API_REJECTED"
        assert "test-token" not in str(exc)
    else:
        raise AssertionError("HTTP 403 must be a permanent Telegram rejection")


def test_telegram_sender_keeps_http_5xx_retryable(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ENABLED", True)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "123")

    def temporary_failure(_request, timeout):
        raise HTTPError(
            "https://api.telegram.org/bot/test-token/sendMessage",
            503, "Service Unavailable", {}, None,
        )

    monkeypatch.setattr(
        "app.scanner.iv_skew_flip.runtime.urlopen", temporary_failure,
    )
    try:
        IVSkewFlipRuntime._telegram_sender({"event_id": 16})
    except RuntimeError as exc:
        assert str(exc) == "TELEGRAM_PROVIDER_TEMPORARY_FAILURE"
        assert "test-token" not in str(exc)
    else:
        raise AssertionError("HTTP 503 must remain retryable")
