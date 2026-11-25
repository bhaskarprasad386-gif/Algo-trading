import logging

from logzero import logger as smartapi_logzero_logger

from app.core.credential_redaction import redact_sensitive_text
from app.core.logger import SensitiveCredentialLogFilter


def test_redacts_smartapi_headers_and_request_payload():
    message = (
        "SmartAPI request failed. Headers: "
        "{'X-PrivateKey': 'private-test', 'Authorization': 'Bearer jwt-test', "
        "'X-Feed-Token': 'feed-test', 'X-ClientLocalIP': '127.0.0.1'}, "
        "Request: {'refreshToken': 'refresh-test', 'password': 'pin-test', "
        "'totp': 'otp-test', 'clientcode': 'client-test'}"
    )
    redacted = redact_sensitive_text(message)

    for secret in (
        "private-test",
        "jwt-test",
        "feed-test",
        "refresh-test",
        "pin-test",
        "otp-test",
        "client-test",
    ):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 7
    assert "127.0.0.1" in redacted


def test_redacts_plain_authorization_and_api_key_assignments():
    message = (
        "Authorization: Bearer bearer-test api_key=api-test "
        "feed_token=feed-test password=pass-test"
    )
    redacted = redact_sensitive_text(message)
    for secret in ("bearer-test", "api-test", "feed-test", "pass-test"):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") == 4


def test_preserves_non_sensitive_diagnostic_values():
    message = "HTTP 504 host=apiconnect.angelone.in client_ip=127.0.0.1"
    assert redact_sensitive_text(message) == message


def test_sdk_log_filter_redacts_formatted_log_record():
    record = logging.LogRecord(
        "logzero_default",
        logging.ERROR,
        __file__,
        1,
        "SmartAPI request failed: Headers=%s Request=%s",
        (
            {"X-PrivateKey": "record-private-test", "X-ClientLocalIP": "127.0.0.1"},
            {"password": "record-pin-test", "totp": "record-otp-test"},
        ),
        None,
    )
    assert SensitiveCredentialLogFilter().filter(record)
    rendered = record.getMessage()
    for secret in ("record-private-test", "record-pin-test", "record-otp-test"):
        assert secret not in rendered
    assert "127.0.0.1" in rendered


def test_sdk_filter_fails_closed_on_formatting_error():
    record = logging.LogRecord(
        "logzero_default", logging.ERROR, __file__, 1, "bad %s %s", ("secret-arg",), None
    )
    assert SensitiveCredentialLogFilter().filter(record)
    rendered = record.getMessage()
    assert "secret-arg" not in rendered
    assert "formatting failed" in rendered


def test_filter_is_attached_to_smartapi_loggers():
    assert any(isinstance(item, SensitiveCredentialLogFilter) for item in smartapi_logzero_logger.filters)
    assert any(
        isinstance(item, SensitiveCredentialLogFilter)
        for item in logging.getLogger("SmartApi.smartConnect").filters
    )
