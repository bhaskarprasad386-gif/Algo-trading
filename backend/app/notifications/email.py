"""SMTP email alert delivery with safe no-op behavior when not configured."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class EmailConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_address: str = ""
    enabled: bool = False
    use_starttls: bool = True
    use_ssl: bool = False


class EmailNotifier:
    """Small SMTP adapter; credentials remain server-side and delivery is opt-in."""

    def __init__(self, config: EmailConfig, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if config.use_ssl and config.use_starttls:
            raise ValueError("use_ssl and use_starttls cannot both be enabled")
        self.config = config
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(
            self.config.enabled
            and self.config.host.strip()
            and self.config.from_address.strip()
            and (self.config.port > 0)
        )

    def send_text(self, recipient: str | None, subject: str, message: str) -> bool:
        recipient = (recipient or "").strip()
        subject = (subject or "").strip()
        message = (message or "").strip()
        if not recipient or not subject or not message or not self.configured:
            return False

        mail = EmailMessage()
        mail["From"] = self.config.from_address.strip()
        mail["To"] = recipient
        mail["Subject"] = subject
        mail.set_content(message)

        try:
            if self.config.use_ssl:
                with smtplib.SMTP_SSL(
                    self.config.host.strip(),
                    self.config.port,
                    timeout=self.timeout_seconds,
                ) as smtp:
                    if self.config.username.strip():
                        smtp.login(self.config.username.strip(), self.config.password)
                    smtp.send_message(mail)
            else:
                with smtplib.SMTP(
                    self.config.host.strip(),
                    self.config.port,
                    timeout=self.timeout_seconds,
                ) as smtp:
                    smtp.ehlo()
                    if self.config.use_starttls:
                        smtp.starttls()
                        smtp.ehlo()
                    if self.config.username.strip():
                        smtp.login(self.config.username.strip(), self.config.password)
                    smtp.send_message(mail)
            return True
        except (OSError, smtplib.SMTPException, ValueError):
            return False
