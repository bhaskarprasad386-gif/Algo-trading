from app.core.config import settings
from app.scanner.iv_skew_flip.runtime import IVSkewFlipRuntime


def test_iv_telegram_prefers_explicit_chat_id(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", "67890,24680")
    assert IVSkewFlipRuntime._telegram_chat_id() == "12345"


def test_iv_telegram_falls_back_to_existing_admin_chat_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", " 67890, 24680 ")
    assert IVSkewFlipRuntime._telegram_chat_id() == "67890"


def test_iv_telegram_missing_recipient_is_empty(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_CHAT_IDS", " , ")
    assert IVSkewFlipRuntime._telegram_chat_id() == ""
