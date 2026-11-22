import logging

from logzero import logger as smartapi_logzero_logger

from app.core.logger import SensitiveCredentialLogFilter


def _filter_instance():
    return next(
        item for item in smartapi_logzero_logger.filters
        if isinstance(item, SensitiveCredentialLogFilter)
    )


def test_sdk_error_log_redacts_credential_headers_and_request_fields():
    headers = {
        "X-PrivateKey": "test-private-key",
        "Authorization": "Bearer test-jwt",
        "X-Feed-Token": "test-feed-token",
        "X-ClientLocalIP": "127.0.0.1",
    }
    request = {
        "password": "test-pin",
        "totp": "test-totp",
        "clientcode": "test-client-code",
    }
    record = logging.LogRecord(
        "logzero",
        logging.ERROR,
        __file__,
        1,
        "SmartAPI request failed. Headers: %s, Request: %s",
        (headers, request),
        None,
    )

    _filter_instance().filter(record)
    message = record.getMessage()

    for secret in (
        "test-private-key",
        "test-jwt",
        "test-feed-token",
        "test-pin",
        "test-totp",
        "test-client-code",
    ):
        assert secret not in message
    assert message.count("[REDACTED]") >= 6
    assert "127.0.0.1" in message


def test_sdk_filter_redacts_bare_authorization_and_api_key_assignments():
    record = logging.LogRecord(
        "SmartApi.smartConnect",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer test-bearer api_key=test-api-key feed_token=test-feed password=test-password",
        (),
        None,
    )

    _filter_instance().filter(record)
    message = record.getMessage()

    for secret in ("test-bearer", "test-api-key", "test-feed", "test-password"):
        assert secret not in message
    assert message.count("[REDACTED]") == 4


def test_filter_is_attached_to_both_smartapi_loggers():
    assert any(
        isinstance(item, SensitiveCredentialLogFilter)
        for item in smartapi_logzero_logger.filters
    )
    assert any(
        isinstance(item, SensitiveCredentialLogFilter)
        for item in logging.getLogger("SmartApi.smartConnect").filters
    )
