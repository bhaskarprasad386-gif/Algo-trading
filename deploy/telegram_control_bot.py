#!/usr/bin/env python3
"""Minimal, dependency-free Telegram control bot for the Algo-Trading VPS.

Security:
- Bot token is read only from TELEGRAM_BOT_TOKEN.
- Commands are accepted only from TELEGRAM_ADMIN_CHAT_IDS.
- No shell commands are accepted from Telegram.
- Optional VNC control is restricted to one configured systemd unit.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from urllib import error, parse, request


API_TIMEOUT = 35
POLL_TIMEOUT = 25
BACKEND_HEALTH_URL = os.getenv("TELEGRAM_BACKEND_HEALTH_URL", "http://127.0.0.1:8000/health")
FEED_HEALTH_URL = os.getenv(
    "TELEGRAM_FEED_HEALTH_URL",
    "http://127.0.0.1:8000/api/v1/market-data/common-feed-health",
)
VNC_SERVICE = os.getenv("TELEGRAM_VNC_SERVICE", "").strip()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_IDS = {
    item.strip()
    for item in os.getenv("TELEGRAM_ADMIN_CHAT_IDS", "").split(",")
    if item.strip()
}
ENABLED = os.getenv("TELEGRAM_CONTROL_ENABLED", "false").strip().lower() == "true"


def telegram(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    body = parse.urlencode(payload).encode()
    req = request.Request(url, data=body, method="POST")
    with request.urlopen(req, timeout=API_TIMEOUT) as response:
        return json.loads(response.read().decode())


def send(chat_id: str, text: str) -> None:
    telegram("sendMessage", {"chat_id": chat_id, "text": text[:3900]})


def run_systemctl(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode, output


def http_get(url: str) -> tuple[bool, str]:
    try:
        with request.urlopen(url, timeout=8) as response:
            return True, response.read().decode()[:3500]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def backend_status() -> str:
    ok, body = http_get(BACKEND_HEALTH_URL)
    rc, svc = run_systemctl("is-active", "algo-trading.service")
    return f"Backend API: {'OK' if ok else 'ERROR'}\nService: {svc or ('active' if rc == 0 else 'inactive')}"


def feed_status() -> str:
    ok, body = http_get(FEED_HEALTH_URL)
    if not ok:
        return f"Feed API ERROR: {body}"
    try:
        data = json.loads(body)
        feed = data.get("feed", {})
        return (
            "COMMON FEED\n"
            f"subscriptions={feed.get('subscriptions')}\n"
            f"active_instruments={feed.get('active_instruments')}\n"
            f"ticks={feed.get('ticks_received')}\n"
            f"socket_groups={feed.get('socket_groups')}\n"
            f"connected_groups={feed.get('connected_groups')}\n"
            f"connect_failures={feed.get('connect_failures')}\n"
            f"delivery_errors={feed.get('delivery_errors')}\n"
            f"normalizer_errors={feed.get('normalizer_errors')}"
        )
    except (TypeError, ValueError):
        return body


def handle(chat_id: str, command: str) -> str:
    if command in {"/start", "/help"}:
        return (
            "Algo Trading VPS Control\n\n"
            "/status - backend/service status\n"
            "/health - backend health JSON\n"
            "/feed - common live-feed health\n"
            "/restart - restart algo-trading.service\n"
            "/vnc_status - VNC service status\n"
            "/vnc_start - start configured VNC service\n"
            "/vnc_stop - stop configured VNC service"
        )
    if command == "/status":
        return backend_status()
    if command == "/health":
        ok, body = http_get(BACKEND_HEALTH_URL)
        return body if ok else f"Health ERROR: {body}"
    if command == "/feed":
        return feed_status()
    if command == "/restart":
        send(chat_id, "Restart requested. Restarting algo-trading.service...")
        rc, out = run_systemctl("restart", "algo-trading.service")
        time.sleep(2)
        rc2, active = run_systemctl("is-active", "algo-trading.service")
        return f"Restart rc={rc}; active={active or rc2}"
    if command.startswith("/vnc_"):
        if not VNC_SERVICE:
            return "VNC control is not configured yet. Set TELEGRAM_VNC_SERVICE in backend/.env."
        if command == "/vnc_status":
            _, out = run_systemctl("is-active", VNC_SERVICE)
            return f"VNC service {VNC_SERVICE}: {out or 'unknown'}"
        if command == "/vnc_start":
            rc, out = run_systemctl("start", VNC_SERVICE)
            return f"VNC start rc={rc}; {out or 'started'}"
        if command == "/vnc_stop":
            rc, out = run_systemctl("stop", VNC_SERVICE)
            return f"VNC stop rc={rc}; {out or 'stopped'}"
    return "Unknown command. Use /help."


def main() -> None:
    if not ENABLED:
        raise SystemExit("TELEGRAM_CONTROL_ENABLED is not true")
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not configured")
    if not ADMIN_IDS:
        raise SystemExit("TELEGRAM_ADMIN_CHAT_IDS is not configured")

    offset = 0
    while True:
        try:
            result = telegram(
                "getUpdates",
                {"timeout": POLL_TIMEOUT, "offset": offset, "allowed_updates": json.dumps(["message"])},
            )
            for update in result.get("result", []):
                offset = max(offset, int(update["update_id"]) + 1)
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                if chat_id not in ADMIN_IDS:
                    continue
                text = (message.get("text") or "").strip().split()[0] if message.get("text") else ""
                if not text.startswith("/"):
                    continue
                try:
                    reply = handle(chat_id, text.split("@", 1)[0].lower())
                except Exception as exc:
                    reply = f"Command error: {type(exc).__name__}: {exc}"
                send(chat_id, reply)
        except (error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            time.sleep(3)
        except Exception:
            time.sleep(5)


if __name__ == "__main__":
    main()
