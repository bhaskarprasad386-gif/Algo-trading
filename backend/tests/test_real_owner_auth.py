from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.brokers import real_owner_auth as auth
from app.core.security import create_access_token


TEST_SECRET = "test-secret-key-that-is-long-enough-for-token-tests"
TEST_PASSWORD = "a-dedicated-owner-password-123"


def setup_owner(monkeypatch):
    monkeypatch.setenv("BOX_SPREAD_REAL_OWNER_PASSWORD", TEST_PASSWORD)
    monkeypatch.setattr(auth.settings, "SECRET_KEY", TEST_SECRET)
    auth._attempts.clear()


def test_owner_login_fails_closed_when_password_not_configured(monkeypatch):
    monkeypatch.delenv("BOX_SPREAD_REAL_OWNER_PASSWORD", raising=False)
    monkeypatch.setattr(auth.settings, "SECRET_KEY", TEST_SECRET)
    with pytest.raises(HTTPException) as raised:
        auth.login_real_owner(auth.RealOwnerLogin(password="anything"))
    assert raised.value.status_code == 503


def test_owner_login_rejects_weak_configured_password(monkeypatch):
    monkeypatch.setenv("BOX_SPREAD_REAL_OWNER_PASSWORD", "too-short")
    monkeypatch.setattr(auth.settings, "SECRET_KEY", TEST_SECRET)
    with pytest.raises(HTTPException) as raised:
        auth.login_real_owner(auth.RealOwnerLogin(password="too-short"))
    assert raised.value.status_code == 503


def test_owner_login_rejects_wrong_password(monkeypatch):
    setup_owner(monkeypatch)
    with pytest.raises(HTTPException) as raised:
        auth.login_real_owner(auth.RealOwnerLogin(password="wrong-password"))
    assert raised.value.status_code == 401


def test_owner_login_issues_short_lived_owner_scoped_token(monkeypatch):
    setup_owner(monkeypatch)
    result = auth.login_real_owner(auth.RealOwnerLogin(password=TEST_PASSWORD))
    assert result["token_type"] == "bearer"
    assert result["expires_in"] == 1800
    owner_id = auth.require_real_owner_access("Bearer " + result["access_token"])
    assert owner_id == auth.OWNER_NAMESPACE_ID


def test_owner_guard_rejects_missing_and_invalid_tokens(monkeypatch):
    setup_owner(monkeypatch)
    for value in (None, "Bearer invalid-token"):
        with pytest.raises(HTTPException) as raised:
            auth.require_real_owner_access(value)
        assert raised.value.status_code == 401


@pytest.mark.parametrize("claims", [
    {"sub": "box-spread-paper-owner", "scope": "box-spread-paper"},
    {"sub": "another-user", "scope": "box-spread-real-owner"},
])
def test_owner_guard_rejects_other_subjects_and_scopes(monkeypatch, claims):
    setup_owner(monkeypatch)
    token = create_access_token(claims)
    with pytest.raises(HTTPException) as raised:
        auth.require_real_owner_access("Bearer " + token)
    assert raised.value.status_code == 401


def test_owner_guard_rejects_expired_token(monkeypatch):
    setup_owner(monkeypatch)
    token = create_access_token(
        {"sub": auth.OWNER_SUBJECT, "scope": auth.OWNER_SCOPE},
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(HTTPException) as raised:
        auth.require_real_owner_access("Bearer " + token)
    assert raised.value.status_code == 401


def test_owner_login_rate_limits_repeated_failures(monkeypatch):
    setup_owner(monkeypatch)
    class Peer:
        host = "198.51.100.7"
    class Request:
        client = Peer()
    for _ in range(auth._LOGIN_MAX_ATTEMPTS):
        with pytest.raises(HTTPException):
            auth.login_real_owner(auth.RealOwnerLogin(password="wrong-password"), Request())
    with pytest.raises(HTTPException) as raised:
        auth.login_real_owner(auth.RealOwnerLogin(password="wrong-password"), Request())
    assert raised.value.status_code == 429
