"""WhatsApp alert delivery with safe no-op behavior when not configured."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error, request


@dataclass(frozen=True)
class WhatsAppConfig:
    access_token: str = ""
    phone_number_id: str = ""
    graph_api_version: str = "v23.0"
    enabled: bool = False


class WhatsAppNotifier:
    def __init__(self, config: WhatsAppConfig, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.config = config
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(
            self.config.enabled
            and self.config.access_token.strip()
            and self.config.phone_number_id.strip()
        )

    def send_text(self, recipient: str | None, message: str) -> bool:
        recipient = (recipient or "").strip()
        message = (message or "").strip()
        if not recipient or not message or not self.configured:
            return False
        payload = json.dumps({
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }).encode("utf-8")
        url = (
            f"https://graph.facebook.com/{self.config.graph_api_version.strip()}/"
            f"{self.config.phone_number_id.strip()}/messages"
        )
        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.access_token.strip()}",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return 200 <= int(response.status) < 300
        except (error.HTTPError, error.URLError, TimeoutError, ValueError):
            return False
