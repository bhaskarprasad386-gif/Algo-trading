from datetime import datetime, timezone

from app.auth.routes import _utc_now, _valid_email


def test_email_validation_rejects_malformed_addresses():
    invalid = (
        "plainaddress",
        "missing-domain@",
        "@missing-local.com",
        "user@domain",
        "user@.com",
        "user@domain..com",
        "user name@example.com",
    )
    assert all(not _valid_email(value) for value in invalid)


def test_email_validation_accepts_normal_address():
    assert _valid_email("user@example.com")
    assert _valid_email("first.last+algo@example.co.in")


def test_auth_utc_now_returns_naive_utc_for_existing_sqlite_datetime_columns():
    now = _utc_now()
    assert now.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds()) < 2
