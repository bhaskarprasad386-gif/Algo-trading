"""Isolated Shoonya Noren market-data WebSocket; existing feeds remain untouched."""
from __future__ import annotations
import json
import queue
import ssl
import threading
import time
from typing import Any, Callable, Iterable
import websocket
from app.core.logger import app_logger
from .registry import InstrumentDescriptor
from .shoonya_normalizer import ShoonyaTickNormalizer

class ShoonyaWebSocket:
    URL = "wss://api.shoonya.com/NorenWSAPI/"

    def __init__(self, *, uid: str, actid: str, access_token: str, source: str = "API",
                 on_tick: Callable[[dict[str, Any]], None] | None = None,
                 on_order_update: Callable[[dict[str, Any]], None] | None = None,
                 on_state: Callable[[str, str | None], None] | None = None,
                 websocket_factory: Callable[..., Any] | None = None, max_dispatch_queue: int = 10000,
                 reconnect_min_seconds: float = 1.0, reconnect_max_seconds: float = 30.0):
        missing = [k for k, v in {"uid": uid, "actid": actid, "access_token": access_token}.items() if not str(v or "").strip()]
        if missing:
            raise ValueError(f"missing Shoonya session fields: {', '.join(missing)}")
        if max_dispatch_queue < 1 or reconnect_min_seconds <= 0 or reconnect_max_seconds < reconnect_min_seconds:
            raise ValueError("invalid queue or reconnect configuration")
        self._uid, self._actid, self._access_token = str(uid).strip(), str(actid).strip(), str(access_token).strip()
        self._source = str(source).strip().upper()
        if self._source not in {"API", "WEB", "MOB"}:
            raise ValueError("source must be API, WEB or MOB")
        self._on_tick, self._on_order_update, self._on_state = on_tick, on_order_update, on_state
        self._factory = websocket_factory or websocket.WebSocketApp
        self._backoff_min, self._backoff_max = reconnect_min_seconds, reconnect_max_seconds
        self._stop = threading.Event()
        self._lock = threading.RLock()
        # Preserve subscribe/unsubscribe wire ordering across caller threads.
        self._subscription_send_lock = threading.Lock()
        self._ws = self._thread = self._dispatch_thread = self._order_dispatch_thread = self._heartbeat_thread = None
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=max_dispatch_queue)
        # Order lifecycle events are isolated from high-volume market ticks.
        self._order_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=2048)
        self._descriptors: dict[tuple[str, str], InstrumentDescriptor] = {}
        self._snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self._initialized: set[tuple[str, str]] = set()
        self._connected = self._authenticated = False
        self._last_error = None
        self._reconnect_count = self._ticks_received = self._ticks_dispatched = self._dispatch_drops = self._order_update_drops = 0
        self._last_frame_at = self._last_tick_at = None
        self._connection_opened_at = None
        self._shutdown_workers_alive: list[str] = []
        self._normalizer = ShoonyaTickNormalizer()

    def subscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch: return 0
        with self._lock:
            staged = dict(self._descriptors)
            for d in batch:
                key = (d.key.exchange.upper(), d.key.token)
                if key in staged and staged[key] != d:
                    raise ValueError(f"conflicting Shoonya instrument descriptor: {key}")
                staged[key] = d
            if len(staged) > 500:
                raise ValueError("Shoonya allows at most 500 subscribed tokens per WebSocket connection")
            self._descriptors = staged
            ws, ready = self._ws, self._authenticated
        if ready and ws is not None: self._send_subscriptions(ws)
        return len(batch)

    def unsubscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        removed = []
        with self._lock:
            for d in tuple(descriptors):
                key = (d.key.exchange.upper(), d.key.token)
                if key in self._descriptors:
                    removed.append(key); self._descriptors.pop(key); self._snapshots.pop(key, None); self._initialized.discard(key)
            ws, ready = self._ws, self._authenticated
        if removed and ready and ws is not None:
            try:
                frame = json.dumps({"t": "u", "k": "#".join(f"{e}|{t}" for e, t in removed)}, separators=(",", ":"))
                with self._subscription_send_lock:
                    ws.send(frame)
            except Exception as exc:
                self._on_error(ws, exc)
                try: ws.close()
                except Exception: pass
        return len(removed)

    def start(self) -> None:
        with self._lock:
            workers = (self._thread, self._dispatch_thread, self._order_dispatch_thread, self._heartbeat_thread)
            alive = [thread.name for thread in workers if thread and thread.is_alive()]
            if alive:
                if self._thread and self._thread.is_alive() and not self._stop.is_set():
                    return
                raise RuntimeError(f"previous Shoonya workers are still active: {', '.join(alive)}")
            self._stop.clear()
            self._shutdown_workers_alive = []
            self._dispatch_thread = threading.Thread(target=self._dispatch_loop, name="shoonya-tick-dispatch", daemon=True)
            self._order_dispatch_thread = threading.Thread(target=self._order_dispatch_loop, name="shoonya-order-dispatch", daemon=True)
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="shoonya-heartbeat", daemon=True)
            self._dispatch_thread.start()
            self._order_dispatch_thread.start()
            self._heartbeat_thread.start()
            self._thread = threading.Thread(target=self._run_loop, name="shoonya-ws-supervisor", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            ws = self._ws
            workers = (self._thread, self._dispatch_thread, self._order_dispatch_thread, self._heartbeat_thread)
        if ws is not None:
            try: ws.close()
            except Exception: pass
        # Use one shared shutdown budget; four sequential 3s joins could block
        # an API shutdown for 12 seconds when several callbacks are stuck.
        deadline = time.monotonic() + 3.0
        for thread in workers:
            if thread and thread is not threading.current_thread():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(timeout=remaining)
        with self._lock:
            self._connected = self._authenticated = False
            self._shutdown_workers_alive = [
                thread.name for thread in workers if thread and thread.is_alive()
            ]
            # Retain the socket reference while its supervisor is still alive:
            # late callbacks must not look like callbacks from an unowned socket.
            if not (self._thread and self._thread.is_alive()):
                self._ws = None
            alive = tuple(self._shutdown_workers_alive)
        if alive:
            reason = "workers did not stop before timeout: " + ", ".join(alive)
            with self._lock: self._last_error = reason
            self._emit_state("STOP_TIMEOUT", reason)
        else:
            self._emit_state("STOPPED", None)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"broker": "shoonya", "connected": self._connected, "authenticated": self._authenticated,
                    "subscriptions": len(self._descriptors), "ticks_received": self._ticks_received,
                    "ticks_dispatched": self._ticks_dispatched, "dispatch_queue_depth": self._queue.qsize(),
                    "dispatch_queue_capacity": self._queue.maxsize, "dispatch_drops": self._dispatch_drops,
                    "order_queue_depth": self._order_queue.qsize(), "order_update_drops": self._order_update_drops,
                    "reconnect_count": self._reconnect_count, "last_error": self._last_error,
                    "shutdown_workers_alive": list(self._shutdown_workers_alive),
                    "last_frame_at": self._last_frame_at, "last_tick_at": self._last_tick_at,
                    "last_tick_age_seconds": max(0.0, time.time() - self._last_tick_at) if self._last_tick_at else None}

    def _emit_state(self, state: str, reason: str | None) -> None:
        if self._on_state:
            try: self._on_state(state, reason)
            except Exception: app_logger.exception("Shoonya state callback failed")

    def _run_loop(self) -> None:
        delay = self._backoff_min
        while not self._stop.is_set():
            try:
                self._reset_feed_epoch()
                ws = self._factory(self.URL, on_open=self._on_open, on_message=self._on_message,
                                   on_error=self._on_error, on_close=self._on_close)
                with self._lock: self._ws = ws
                ws.run_forever(sslopt={"cert_reqs": ssl.CERT_REQUIRED}, ping_interval=20, ping_timeout=10)
                with self._lock:
                    opened_at = self._connection_opened_at
                    self._connection_opened_at = None
                # Reset exponential backoff only after a genuinely stable session.
                if opened_at is not None and time.monotonic() - opened_at >= 30.0:
                    delay = self._backoff_min
            except Exception as exc: self._on_error(None, exc)
            if self._stop.is_set(): break
            with self._lock:
                self._connected = self._authenticated = False
                self._reconnect_count += 1
            self._emit_state("RECONNECTING", self._last_error)
            self._stop.wait(delay)
            delay = min(self._backoff_max, delay * 2)

    def _reset_feed_epoch(self) -> None:
        """Discard per-connection tick state before opening a replacement socket."""
        with self._lock:
            self._connected = self._authenticated = False
            self._connection_opened_at = None
            self._snapshots.clear()
            self._initialized.clear()

    def _on_open(self, ws: Any) -> None:
        ws.send(json.dumps({"t": "a", "uid": self._uid, "actid": self._actid,
                            "source": self._source, "accesstoken": self._access_token}, separators=(",", ":")))
        with self._lock:
            self._connected, self._authenticated = True, False
            # A TCP/WebSocket open is not a healthy broker session until auth succeeds.
            self._connection_opened_at = None
        self._emit_state("AUTHENTICATING", None)

    def _on_message(self, ws: Any, raw: str) -> None:
        try: message = json.loads(raw)
        except (TypeError, ValueError):
            with self._lock: self._last_error = "invalid JSON frame"
            return
        if not isinstance(message, dict): return
        with self._lock: self._last_frame_at = time.time()
        kind = str(message.get("t") or "").lower()
        if kind == "h":
            try: ws.send(json.dumps({"t": "h"}, separators=(",", ":")))
            except Exception as exc: self._on_error(ws, exc)
            return
        if kind == "ak":
            if str(message.get("s") or "").strip().lower() != "ok":
                with self._lock: self._authenticated = False; self._last_error = "Shoonya authentication rejected"
                self._emit_state("AUTH_FAILED", self._last_error); ws.close(); return
            with self._lock:
                self._authenticated = True
                self._last_error = None
                self._connection_opened_at = time.monotonic()
            self._emit_state("LIVE", None); self._send_subscriptions(ws); return
        if kind in {"tk", "tf"}: self._handle_tick(message)
        elif kind == "om" and self._on_order_update: self._enqueue("order", message)

    def _send_subscriptions(self, ws: Any) -> None:
        # Take the latest descriptor snapshot only after acquiring the wire lock.
        # This prevents a delayed subscribe from overtaking a concurrent unsubscribe.
        with self._subscription_send_lock:
            with self._lock: instruments = tuple(self._descriptors)
            if not instruments:
                return
            keys = "#".join(f"{e}|{t}" for e, t in instruments)
            try:
                ws.send(json.dumps({"t": "t", "k": keys}, separators=(",", ":")))
            except Exception as exc:
                self._on_error(ws, exc)
                try: ws.close()
                except Exception: pass

    def _handle_tick(self, message: dict[str, Any]) -> None:
        exchange, token = str(message.get("e") or "").upper(), str(message.get("tk") or "")
        key = (exchange, token)
        if not exchange or not token: return
        with self._lock:
            descriptor = self._descriptors.get(key)
            if descriptor is None: return
            if str(message.get("t") or "").lower() == "tf" and key not in self._initialized:
                return
            merged = {**self._snapshots.get(key, {}), **message}
            self._snapshots[key] = merged
            if str(message.get("t") or "").lower() == "tk":
                self._initialized.add(key)
        try: record = self._normalizer.normalize(descriptor, merged)
        except (TypeError, ValueError) as exc:
            with self._lock: self._last_error = f"tick normalization: {exc}"
            return
        if record.timestamp_ns <= 0: return
        with self._lock: self._ticks_received += 1; self._last_tick_at = time.time()
        self._enqueue("tick", record.as_dict())

    def _enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        target = self._order_queue if kind == "order" else self._queue
        try:
            target.put_nowait((kind, payload))
        except queue.Full:
            if kind == "order":
                with self._lock:
                    self._order_update_drops += 1
                    ws = self._ws
                # Never silently continue after losing an order lifecycle event.
                self._emit_state("ORDER_FEED_OVERFLOW", "order update queue full; reconcile broker order book")
                if ws is not None:
                    try: ws.close()
                    except Exception: pass
            else:
                with self._lock: self._dispatch_drops += 1

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try: _, payload = self._queue.get(timeout=0.2)
            except queue.Empty: continue
            try:
                if self._on_tick: self._on_tick(payload)
                with self._lock: self._ticks_dispatched += 1
            except Exception: app_logger.exception("Shoonya tick callback failed")
            finally: self._queue.task_done()

    def _order_dispatch_loop(self) -> None:
        while not self._stop.is_set() or not self._order_queue.empty():
            try: _, payload = self._order_queue.get(timeout=0.2)
            except queue.Empty: continue
            try:
                if self._on_order_update: self._on_order_update(payload)
            except Exception: app_logger.exception("Shoonya order callback failed")
            finally: self._order_queue.task_done()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(3.0):
            with self._lock:
                ws, ready = self._ws, self._authenticated
            if not ready or ws is None:
                continue
            try:
                ws.send(json.dumps({"t": "h"}, separators=(",", ":")))
            except Exception as exc:
                self._on_error(ws, exc)
                try: ws.close()
                except Exception: pass

    def _on_error(self, ws: Any, error: Any) -> None:
        with self._lock:
            self._last_error = f"{type(error).__name__}: {error}"
            self._connected = self._authenticated = False
        self._emit_state("ERROR", self._last_error)
        app_logger.warning("Shoonya WebSocket error: %s", self._last_error)

    def _on_close(self, ws: Any, status_code: Any, message: Any) -> None:
        with self._lock: self._connected = self._authenticated = False
        if not self._stop.is_set(): self._emit_state("DISCONNECTED", str(message or status_code or "socket closed"))
