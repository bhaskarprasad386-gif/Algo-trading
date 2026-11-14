"""Isolated Shoonya Noren market-data WebSocket; existing feeds remain untouched."""
from __future__ import annotations
import json
import queue
import random
import ssl
import threading
import time
from time import monotonic_ns
from typing import Any, Callable, Iterable
import websocket
from app.core.logger import app_logger
from .registry import InstrumentDescriptor
from .shoonya_normalizer import ShoonyaTickNormalizer
from .contracts import MarketDataRecord

class ShoonyaWebSocket:
    URL = "wss://api.shoonya.com/NorenWSAPI/"

    def __init__(self, *, uid: str, actid: str, access_token: str, source: str = "API",
                 on_tick: Callable[[dict[str, Any]], None] | None = None,
                 on_order_update: Callable[[dict[str, Any]], None] | None = None,
                 on_state: Callable[[str, str | None], None] | None = None,
                 on_session_observation: Callable[[str, str | None, int, bool, bool, int], None] | None = None,
                 on_tick_observation: Callable[[MarketDataRecord, int, bool, int], None] | None = None,
                 websocket_factory: Callable[..., Any] | None = None, max_dispatch_queue: int = 10000,
                 reconnect_min_seconds: float = 1.0, reconnect_max_seconds: float = 30.0,
                 frame_timeout_seconds: float = 30.0, auth_timeout_seconds: float = 15.0):
        missing = [k for k, v in {"uid": uid, "actid": actid, "access_token": access_token}.items() if not str(v or "").strip()]
        if missing:
            raise ValueError(f"missing Shoonya session fields: {', '.join(missing)}")
        if (max_dispatch_queue < 1 or reconnect_min_seconds <= 0 or reconnect_max_seconds < reconnect_min_seconds
                or frame_timeout_seconds <= 3.0 or auth_timeout_seconds <= 0):
            raise ValueError("invalid queue, reconnect, or frame-timeout configuration")
        self._uid, self._actid, self._access_token = str(uid).strip(), str(actid).strip(), str(access_token).strip()
        self._secret_values = tuple(value for value in (self._uid, self._actid, self._access_token) if value)
        self._source = str(source).strip().upper()
        if self._source not in {"API", "WEB", "MOB"}:
            raise ValueError("source must be API, WEB or MOB")
        self._on_tick, self._on_order_update, self._on_state = on_tick, on_order_update, on_state
        if on_session_observation is not None and not callable(on_session_observation):
            raise TypeError("on_session_observation must be callable")
        self._on_session_observation = on_session_observation
        if on_tick_observation is not None and not callable(on_tick_observation):
            raise TypeError('on_tick_observation must be callable')
        self._on_tick_observation = on_tick_observation
        self._factory = websocket_factory or websocket.WebSocketApp
        self._backoff_min, self._backoff_max = reconnect_min_seconds, reconnect_max_seconds
        self._frame_timeout_seconds = float(frame_timeout_seconds)
        self._auth_timeout_seconds = float(auth_timeout_seconds)
        self._stop = threading.Event()
        self._lock = threading.RLock()
        # Preserve subscribe/unsubscribe wire ordering across caller threads.
        self._subscription_send_lock = threading.Lock()
        self._ws = self._thread = self._dispatch_thread = self._order_dispatch_thread = self._heartbeat_thread = None
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue(maxsize=max_dispatch_queue)
        # Order lifecycle events are isolated from high-volume market ticks.
        self._order_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=2048)
        self._descriptors: dict[tuple[str, str], InstrumentDescriptor] = {}
        self._snapshots: dict[tuple[str, str], dict[str, Any]] = {}
        self._initialized: set[tuple[str, str]] = set()
        # Exchange timestamps are tracked per instrument to reject replayed/out-of-order ticks.
        self._last_exchange_timestamp_ns: dict[tuple[str, str], int] = {}
        self._out_of_order_ticks = 0
        self._connected = self._authenticated = False
        self._auth_ack_received = False
        self._last_error = None
        self._reconnect_count = self._ticks_received = self._ticks_dispatched = self._dispatch_drops = self._order_update_drops = 0
        self._last_frame_at = self._last_tick_at = None
        self._connection_opened_at = None
        self._auth_started_at = None
        self._feed_epoch = 0
        self._shutdown_workers_alive: list[str] = []
        self._normalizer = ShoonyaTickNormalizer()

    def subscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        batch = tuple(descriptors)
        if not batch: return 0
        # Serialize desired-state mutation with its corresponding wire update.
        # Otherwise a subscribe can overtake an unsubscribe after the latter has
        # removed local state but before it sends the broker's unsubscribe frame.
        with self._subscription_send_lock:
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
            if ready and ws is not None:
                self._send_subscriptions_locked(ws)
        return len(batch)

    def unsubscribe(self, descriptors: Iterable[InstrumentDescriptor]) -> int:
        with self._subscription_send_lock:
            removed = []
            with self._lock:
                for d in tuple(descriptors):
                    key = (d.key.exchange.upper(), d.key.token)
                    if key in self._descriptors:
                        removed.append(key)
                        self._descriptors.pop(key)
                        self._snapshots.pop(key, None)
                        self._initialized.discard(key)
                        self._last_exchange_timestamp_ns.pop(key, None)
                ws, ready = self._ws, self._authenticated
            if removed and ready and ws is not None:
                try:
                    frame = json.dumps({"t": "u", "k": "#".join(f"{e}|{t}" for e, t in removed)}, separators=(",", ":"))
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
            self._thread = threading.Thread(target=self._run_loop, name="shoonya-ws-supervisor", daemon=True)
            started: list[threading.Thread] = []
            try:
                # Thread.start() can fail independently (resource exhaustion,
                # interpreter shutdown, or an injected runtime failure). Treat
                # startup as one transaction: never leave earlier workers
                # running when a later worker cannot be created.
                for worker in (
                    self._dispatch_thread,
                    self._order_dispatch_thread,
                    self._heartbeat_thread,
                    self._thread,
                ):
                    worker.start()
                    started.append(worker)
            except Exception:
                self._stop.set()
                deadline = time.monotonic() + 3.0
                for worker in started:
                    if worker is threading.current_thread():
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    worker.join(timeout=remaining)
                with self._lock:
                    alive = [worker for worker in started if worker.is_alive()]
                    self._shutdown_workers_alive = [worker.name for worker in alive]
                    self._connected = self._authenticated = False
                    if not alive:
                        self._thread = None
                        self._dispatch_thread = None
                        self._order_dispatch_thread = None
                        self._heartbeat_thread = None
                        self._ws = None
                raise

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
                    "reconnect_count": self._reconnect_count, "out_of_order_ticks": self._out_of_order_ticks,
                    "last_error": self._last_error,
                    "shutdown_workers_alive": list(self._shutdown_workers_alive),
                    "last_frame_at": self._last_frame_at, "last_frame_age_seconds": max(0.0, time.time() - self._last_frame_at) if self._last_frame_at else None,
                    "frame_timeout_seconds": self._frame_timeout_seconds,
                    "auth_timeout_seconds": self._auth_timeout_seconds,
                    "auth_age_seconds": max(0.0, time.monotonic() - self._auth_started_at) if self._auth_started_at else None,
                    "frame_stale": bool(self._last_frame_at and time.time() - self._last_frame_at >= self._frame_timeout_seconds),
                    "last_tick_at": self._last_tick_at,
                    "last_tick_age_seconds": max(0.0, time.time() - self._last_tick_at) if self._last_tick_at else None}

    def _emit_state(self, state: str, reason: str | None) -> None:
        if self._on_state:
            try: self._on_state(state, reason)
            except Exception: app_logger.exception("Shoonya state callback failed")
        if self._on_session_observation:
            try:
                with self._lock:
                    evidence = (self._feed_epoch, self._connected, self._authenticated)
                self._on_session_observation(
                    state, reason, evidence[0], evidence[1], evidence[2], monotonic_ns()
                )
            except Exception:
                app_logger.exception("Shoonya session evidence observer failed")

    def _run_loop(self) -> None:
        delay = self._backoff_min
        while not self._stop.is_set():
            try:
                self._reset_feed_epoch()
                with self._lock:
                    epoch = self._feed_epoch
                ws = self._factory(
                    self.URL,
                    on_open=self._guard_callback(self._on_open, epoch),
                    on_message=self._guard_callback(self._on_message, epoch),
                    on_error=self._guard_callback(self._on_error, epoch),
                    on_close=self._guard_callback(self._on_close, epoch),
                )
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
            self._stop.wait(self._jittered_backoff_delay(delay))
            delay = min(self._backoff_max, delay * 2)

    def _jittered_backoff_delay(self, delay: float) -> float:
        """Add bounded jitter so many clients do not reconnect in lockstep."""
        lower = min(self._backoff_max, max(self._backoff_min, delay * 0.8))
        upper = min(self._backoff_max, max(lower, delay * 1.2))
        return random.uniform(lower, upper)

    def _reset_feed_epoch(self) -> None:
        """Discard per-connection tick state before opening a replacement socket."""
        with self._lock:
            self._connected = self._authenticated = False
            self._auth_ack_received = False
            self._connection_opened_at = None
            self._auth_started_at = None
            self._last_frame_at = None
            self._feed_epoch += 1
            self._snapshots.clear()
            self._initialized.clear()
            self._last_exchange_timestamp_ns.clear()

    def _guard_callback(self, callback: Callable[..., None], epoch: int) -> Callable[..., None]:
        """Ignore late callbacks emitted by a superseded WebSocket instance."""
        def guarded(ws: Any, *args: Any) -> None:
            with self._lock:
                if self._stop.is_set() or epoch != self._feed_epoch or ws is not self._ws:
                    return
            callback(ws, *args)
        return guarded

    def _on_open(self, ws: Any) -> None:
        # Validate socket identity before writing, then release the lifecycle
        # lock around network I/O so stop() can close a stalled transport.
        try:
            with self._lock:
                if ws is not self._ws or self._stop.is_set():
                    return
                self._connected, self._authenticated = True, False
                self._last_error = None
                # A TCP/WebSocket open is not a healthy broker session until auth succeeds.
                self._connection_opened_at = None
                self._auth_started_at = time.monotonic()
            # Never hold the lifecycle lock across SDK/network I/O: stop() must
            # be able to acquire it, close this socket, and unblock a stalled send.
            ws.send(json.dumps({"t": "a", "uid": self._uid, "actid": self._actid,
                                "source": self._source, "accesstoken": self._access_token},
                               separators=(",", ":")))
            with self._lock:
                if ws is not self._ws or self._stop.is_set():
                    return
        except Exception as exc:
            self._on_error(ws, exc)
            try:
                ws.close()
            except Exception:
                pass
            return
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
            # Noren auth acknowledgement is a one-shot transition per socket.
            # Ignore duplicate acknowledgements so subscriptions are never
            # replayed twice on the same connection.
            with self._lock:
                # The callback may have passed its outer epoch guard just
                # before reconnect began. Re-check identity at the state
                # transition so an obsolete socket cannot mark the new feed LIVE.
                if ws is not self._ws or self._stop.is_set():
                    return
                if self._auth_ack_received:
                    return
                self._auth_ack_received = True
            if str(message.get("s") or "").strip().lower() != "ok":
                with self._lock: self._authenticated = False; self._last_error = "Shoonya authentication rejected"
                self._emit_state("AUTH_FAILED", self._last_error); ws.close(); return
            with self._lock:
                self._authenticated = True
                self._last_error = None
                self._connection_opened_at = time.monotonic()
                self._auth_started_at = None
            self._emit_state("LIVE", None)
            self._send_subscriptions(ws)
            self._send_order_subscription(ws)
            return
        # Do not accept market or order events until the broker has explicitly
        # acknowledged this socket's authentication. A transport-open socket is
        # not an authenticated feed and must never publish apparently-live data.
        with self._lock:
            authenticated = self._authenticated
        if not authenticated:
            return
        if kind in {"tk", "tf"}:
            self._handle_tick(message)
        elif kind == "om" and self._on_order_update:
            self._enqueue("order", message)

    def _send_order_subscription(self, ws: Any) -> None:
        """Subscribe to this account's order lifecycle feed after auth, if requested."""
        if self._on_order_update is None:
            return
        try:
            # Snapshot current ownership under the lifecycle lock, but never
            # hold that lock across network I/O; stop() must remain able to close.
            with self._lock:
                if ws is not self._ws or self._stop.is_set() or not self._authenticated:
                    return
                actid = self._actid
            ws.send(json.dumps({"t": "o", "actid": actid}, separators=(",", ":")))
        except Exception as exc:
            self._on_error(ws, exc)
            try:
                ws.close()
            except Exception:
                pass

    def _send_subscriptions(self, ws: Any) -> None:
        with self._subscription_send_lock:
            self._send_subscriptions_locked(ws)

    def _send_subscriptions_locked(self, ws: Any) -> None:
        """Send desired subscriptions only to the currently authenticated socket."""
        try:
            # The subscription lock preserves wire ordering; the lifecycle
            # lock is used only to snapshot current socket/auth/desired state.
            with self._lock:
                if ws is not self._ws or self._stop.is_set() or not self._authenticated:
                    return
                instruments = tuple(self._descriptors)
                if not instruments:
                    return
                keys = "#".join(f"{e}|{t}" for e, t in instruments)
            # _subscription_send_lock preserves subscribe/unsubscribe ordering;
            # the lifecycle lock is intentionally released before socket.send.
            ws.send(json.dumps({"t": "t", "k": keys}, separators=(",", ":")))
        except Exception as exc:
            self._on_error(ws, exc)
            try: ws.close()
            except Exception: pass

    def _handle_tick(self, message: dict[str, Any]) -> None:
        received_at_monotonic_ns = monotonic_ns()
        exchange, token = str(message.get("e") or "").upper(), str(message.get("tk") or "")
        key = (exchange, token)
        if not exchange or not token: return
        with self._lock:
            descriptor = self._descriptors.get(key)
            if descriptor is None or not self._authenticated: return
            epoch = self._feed_epoch
            is_snapshot = str(message.get("t") or "").lower() == "tk"
            if not is_snapshot and key not in self._initialized:
                return
            merged = {**self._snapshots.get(key, {}), **message}
        try: record = self._normalizer.normalize(descriptor, merged)
        except (TypeError, ValueError) as exc:
            with self._lock: self._last_error = f"tick normalization: {exc}"
            return
        if record.timestamp_ns <= 0: return
        with self._lock:
            previous_timestamp = self._last_exchange_timestamp_ns.get(key, 0)
            # Equal-second frames can carry legitimate quote changes; reject only
            # strictly older exchange timestamps, not equal timestamps.
            if record.timestamp_ns < previous_timestamp:
                self._out_of_order_ticks += 1
                return
            # Recheck session identity before mutating any snapshot state;
            # reconnect may have superseded this tick during normalization.
            if epoch != self._feed_epoch or not self._authenticated:
                return
            # Commit only after normalization, ordering, and epoch checks so
            # an old socket cannot poison the replacement session's snapshot.
            self._snapshots[key] = merged
            if is_snapshot:
                self._initialized.add(key)
            self._last_exchange_timestamp_ns[key] = record.timestamp_ns
            self._ticks_received += 1
            self._last_tick_at = time.time()
        self._enqueue("tick", record.as_dict(), observation=(record, epoch, True, received_at_monotonic_ns))

    def _enqueue(self, kind: str, payload: dict[str, Any], *, observation: tuple[MarketDataRecord, int, bool, int] | None = None) -> None:
        target = self._order_queue if kind == "order" else self._queue
        try:
            item = (kind, payload) if observation is None else (kind, payload, observation)
            target.put_nowait(item)
        except queue.Full:
            with self._lock:
                ws = self._ws
                self._connected = self._authenticated = False
                if kind == "order":
                    self._order_update_drops += 1
                    state = "ORDER_FEED_OVERFLOW"
                    reason = "order update queue full; reconcile broker order book"
                else:
                    self._dispatch_drops += 1
                    state = "DEGRADED"
                    reason = "tick dispatch queue full: reconnecting to avoid silently stale market data"
                self._last_error = reason
            # Queue loss is a feed-integrity fault. Force the existing reconnect
            # path instead of claiming LIVE while market/order events are lost.
            self._emit_state(state, reason)
            if ws is not None:
                try: ws.close()
                except Exception: pass

    def _dispatch_loop(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try: item = self._queue.get(timeout=0.2)
            except queue.Empty: continue
            try:
                _, payload, *metadata = item
                try:
                    if self._on_tick: self._on_tick(payload)
                except Exception:
                    app_logger.exception("Shoonya tick callback failed")
                if metadata and self._on_tick_observation:
                    record, epoch, authenticated, received_at_monotonic_ns = metadata[0]
                    with self._lock:
                        current = epoch == self._feed_epoch and self._authenticated and authenticated
                    if current:
                        try:
                            self._on_tick_observation(record, epoch, authenticated, received_at_monotonic_ns)
                        except Exception:
                            app_logger.exception('Shoonya read-only tick observation callback failed')
                with self._lock: self._ticks_dispatched += 1
            except Exception: app_logger.exception("Shoonya tick dispatch failed")
            finally: self._queue.task_done()

    def _order_dispatch_loop(self) -> None:
        while not self._stop.is_set() or not self._order_queue.empty():
            try: _, payload = self._order_queue.get(timeout=0.2)
            except queue.Empty: continue
            try:
                if self._on_order_update: self._on_order_update(payload)
            except Exception: app_logger.exception("Shoonya order callback failed")
            finally: self._order_queue.task_done()

    def _check_frame_freshness(self, ws: Any) -> bool:
        """Close an authenticated socket that stops delivering broker frames."""
        now = time.time()
        with self._lock:
            if ws is not self._ws or not self._authenticated or self._last_frame_at is None:
                return False
            age = max(0.0, now - self._last_frame_at)
            if age < self._frame_timeout_seconds:
                return False
            reason = (
                f"broker frame timeout: no inbound Shoonya frame for {age:.1f}s "
                f"(limit {self._frame_timeout_seconds:.1f}s)"
            )
            self._connected = self._authenticated = False
            self._last_error = reason
        self._emit_state("DEGRADED", reason)
        try:
            ws.close()
        except Exception as exc:
            app_logger.warning("Shoonya stale socket close failed: {}", self._safe_error_text(exc))
        return True

    def _check_authentication_freshness(self, ws: Any) -> bool:
        """Close a transport-open socket that never receives an auth acknowledgement."""
        now = time.monotonic()
        with self._lock:
            if ws is not self._ws or self._authenticated or self._auth_started_at is None:
                return False
            age = max(0.0, now - self._auth_started_at)
            if age < self._auth_timeout_seconds:
                return False
            reason = (
                f"authentication acknowledgement timeout: no Shoonya ak frame for {age:.1f}s "
                f"(limit {self._auth_timeout_seconds:.1f}s)"
            )
            self._connected = False
            self._last_error = reason
        self._emit_state("AUTH_TIMEOUT", reason)
        try:
            ws.close()
        except Exception as exc:
            app_logger.warning("Shoonya auth-timeout socket close failed: {}", self._safe_error_text(exc))
        return True

    def _send_heartbeat(self, ws: Any) -> bool:
        """Send heartbeat only while this socket remains the authenticated owner."""
        try:
            with self._lock:
                if ws is not self._ws or self._stop.is_set() or not self._authenticated:
                    return False
            ws.send(json.dumps({"t": "h"}, separators=(",", ":")))
            return True
        except Exception as exc:
            self._on_error(ws, exc)
            try:
                ws.close()
            except Exception:
                pass
            return False

    def _heartbeat_loop(self) -> None:
        next_heartbeat = time.monotonic() + 3.0
        while not self._stop.wait(1.0):
            with self._lock:
                ws, ready = self._ws, self._authenticated
            if ws is None:
                continue
            if not ready:
                self._check_authentication_freshness(ws)
                continue
            if self._check_frame_freshness(ws):
                continue
            if time.monotonic() < next_heartbeat:
                continue
            if self._send_heartbeat(ws):
                next_heartbeat = time.monotonic() + 3.0

    def _safe_error_text(self, value: Any) -> str:
        safe = str(value)
        for secret in self._secret_values:
            safe = safe.replace(secret, "[REDACTED]")
        return safe[:240]

    def _on_error(self, ws: Any, error: Any) -> None:
        safe_error = f"{type(error).__name__}: {self._safe_error_text(error)}"
        with self._lock:
            # Internal send/watchdog paths can race a reconnect even though
            # transport callbacks are epoch-guarded. An error from an obsolete
            # socket must never mark the replacement session unhealthy.
            if ws is not None and ws is not self._ws:
                return
            self._last_error = safe_error
            self._connected = self._authenticated = False
        self._emit_state("ERROR", safe_error)
        app_logger.warning("Shoonya WebSocket error: {}", safe_error)

    def _on_close(self, ws: Any, status_code: Any, message: Any) -> None:
        with self._lock:
            # A callback can pass the epoch guard and then lose a race to
            # reconnect before acquiring this lock. Recheck socket identity
            # atomically here so a retired socket cannot close its replacement.
            if ws is not None and ws is not self._ws:
                return
            self._connected = self._authenticated = False
            preserved_faults = (
                ("Shoonya authentication rejected", "AUTH_FAILED"),
                ("broker frame timeout:", "DEGRADED"),
                ("authentication acknowledgement timeout:", "AUTH_TIMEOUT"),
                ("tick dispatch queue full:", "DEGRADED"),
                ("order update queue full;", "ORDER_FEED_OVERFLOW"),
            )
            preserved = next(
                ((prefix, state) for prefix, state in preserved_faults
                 if self._last_error and self._last_error.startswith(prefix)),
                None,
            )
            fault_reason = self._last_error if preserved else None
            fault_state = preserved[1] if preserved else "DISCONNECTED"
        if not self._stop.is_set():
            reason = self._safe_error_text(fault_reason or message or status_code or "socket closed")
            self._emit_state(fault_state, reason)