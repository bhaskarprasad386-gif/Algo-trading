"""Telegram Bot API alert delivery with safe no-op behavior when not configured."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    enabled: bool = False


class TelegramNotifier:
    """Small provider adapter; credentials stay server-side and delivery is opt-in."""

    def __init__(self, config: TelegramConfig, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.config = config
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.config.enabled and self.config.bot_token.strip())

    def send_text(self, chat_id: str | None, message: str) -> bool:
        chat_id = (chat_id or "").strip()
        message = (message or "").strip()
        if not chat_id or not message or not self.configured:
            return False
        payload = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        url = f"https://api.telegram.org/bot{self.config.bot_token.strip()}/sendMessage"
        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return 200 <= int(response.status) < 300
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return False
