import pytest
import json
import threading
import time

from types import SimpleNamespace
from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.market_data.shoonya_normalizer import ShoonyaTickNormalizer
from app.market_data.shoonya_websocket import ShoonyaWebSocket
from app.market_data.live_provider import LiveMarketDataFeed

def descriptor():
    return InstrumentDescriptor(key=InstrumentKey("NFO", "NFO", "12345"),
        symbol="NIFTY26NOV25000CE", instrument_type="option", exchange="NFO", segment="NFO",
        expiry="2026-11-26", strike=25000, option_type="CE", lot_size=65,
        tick_size=0.05, underlying="NIFTY")

def test_shoonya_normalizer_uses_decimal_prices_and_source_metadata():
    record = ShoonyaTickNormalizer().normalize(descriptor(), {
        "t":"tk","e":"NFO","tk":"12345","ts":"NIFTY26NOV25000CE","lp":"125.50",
        "bp1":"125.45","sp1":"125.55","bq1":"100","sq1":"120","v":"5000",
        "oi":"120000","ft":"1794551794","pp":"2","ls":"65","ti":"0.05"})
    assert record.ltp == 125.50 and record.bid == 125.45 and record.ask == 125.55
    assert record.oi == 120000 and record.payload["broker"] == "shoonya"
    assert record.timestamp_ns == 1794551794000000000

def test_shoonya_normalizer_rejects_identity_mismatch():
    try: ShoonyaTickNormalizer().normalize(descriptor(), {"e":"NSE","tk":"12345","ft":"1794551794"})
    except ValueError as exc: assert "identity" in str(exc)
    else: raise AssertionError("wrong exchange must fail closed")

def test_shoonya_websocket_merges_incremental_tf_updates():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws, feed._authenticated = fake_ws, True
    feed._on_message(fake_ws, json.dumps({"t":"tk","e":"NFO","tk":"12345","ts":"NIFTY26NOV25000CE",
        "lp":"100.00","bp1":"99.95","sp1":"100.05","oi":"1000","ft":"1794551794"}))
    feed._on_message(fake_ws, json.dumps({"t":"tf","e":"NFO","tk":"12345","lp":"101.00","ft":"1794551795"}))
    assert feed._snapshots[("NFO","12345")]["oi"] == "1000"
    assert feed._snapshots[("NFO","12345")]["lp"] == "101.00"
    assert feed.snapshot()["ticks_received"] == 2
    assert feed.snapshot()["dispatch_queue_depth"] == 2

def test_shoonya_credentials_are_required():
    try: ShoonyaWebSocket(uid="", actid="U1", access_token="token")
    except ValueError as exc: assert "uid" in str(exc)
    else: raise AssertionError("empty uid must fail closed")

def test_tf_before_initial_snapshot_is_ignored():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._on_message(fake_ws, json.dumps({"t":"tf","e":"NFO","tk":"12345","lp":"101.00","ft":"1794551795"}))
    assert feed.snapshot()["ticks_received"] == 0
    assert ("NFO","12345") not in feed._snapshots


def test_application_heartbeat_is_answered():
    sent = []
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._on_message(fake_ws, json.dumps({"t":"h"}))
    assert json.loads(sent[0]) == {"t":"h"}


def test_order_updates_have_independent_queue():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token", on_order_update=lambda event: None)
    feed._enqueue("order", {"t":"om","status":"COMPLETE"})
    assert feed.snapshot()["order_queue_depth"] == 1
    assert feed.snapshot()["dispatch_queue_depth"] == 0


def test_shoonya_feed_implements_broker_neutral_live_feed_contract():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    assert isinstance(feed, LiveMarketDataFeed)


def test_subscription_limit_is_500_and_atomic():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    descriptors = [
        InstrumentDescriptor(key=InstrumentKey("NFO", "NFO", str(i)),
            symbol=f"OPT{i}", instrument_type="option", exchange="NFO", segment="NFO",
            expiry="2026-11-26", strike=25000+i, option_type="CE", lot_size=65,
            tick_size=0.05, underlying="NIFTY")
        for i in range(501)
    ]
    try:
        feed.subscribe(descriptors)
    except ValueError as exc:
        assert "500" in str(exc)
    else:
        raise AssertionError("501 subscriptions must be rejected")
    assert feed.snapshot()["subscriptions"] == 0


def test_authentication_ack_is_required_before_market_subscription():
    sent = []
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._on_open(fake_ws)
    assert json.loads(sent[-1])["t"] == "a"
    assert not feed.snapshot()["authenticated"]
    assert feed._connection_opened_at is None
    feed._on_message(fake_ws, json.dumps({"t": "ak", "s": "OK"}))
    assert feed.snapshot()["authenticated"]
    assert feed._connection_opened_at is not None
    assert json.loads(sent[-1]) == {"t": "t", "k": "NFO|12345"}


def test_duplicate_auth_ack_does_not_duplicate_market_or_order_subscriptions():
    sent = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_order_update=lambda event: None,
    )
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = fake_ws
    feed._on_open(fake_ws)
    ack = json.dumps({"t": "ak", "s": "OK"})
    feed._on_message(fake_ws, ack)
    feed._on_message(fake_ws, ack)

    frame_types = [json.loads(frame)["t"] for frame in sent]
    assert frame_types == ["a", "t", "o"]
    assert feed.snapshot()["authenticated"]


def test_rejected_authentication_never_subscribes_and_state_survives_close():
    sent, closed, states = [], [], []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="bad",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: closed.append(True))
    feed._on_open(fake_ws)
    feed._on_message(fake_ws, json.dumps({"t": "ak", "s": "Not_Ok"}))
    assert not feed.snapshot()["authenticated"]
    assert feed._connection_opened_at is None
    assert feed.snapshot()["last_error"] == "Shoonya authentication rejected"
    assert closed
    assert all(json.loads(frame)["t"] != "t" for frame in sent)
    feed._on_close(fake_ws, 1000, "closed after rejected authentication")
    assert states[-1][0] == "AUTH_FAILED"
    assert states[-1][1] == "Shoonya authentication rejected"


def test_uppercase_tick_frame_type_is_normalized():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws, feed._authenticated = fake_ws, True
    feed._on_message(fake_ws, json.dumps({"t": "TK", "e": "NFO", "tk": "12345",
        "lp": "100.00", "ft": "1794551794"}))
    feed._on_message(fake_ws, json.dumps({"t": "TF", "e": "NFO", "tk": "12345",
        "lp": "101.00", "ft": "1794551795"}))
    assert feed._snapshots[("NFO", "12345")]["lp"] == "101.00"
    assert feed.snapshot()["ticks_received"] == 2


def test_reconnect_clears_previous_snapshot_before_new_incremental_frames():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    # The first socket has completed authentication before market frames.
    feed._authenticated = True
    feed._on_message(fake_ws, json.dumps({"t": "tk", "e": "NFO", "tk": "12345",
        "lp": "100.00", "oi": "777", "ft": "1794551794"}))
    assert feed.snapshot()["ticks_received"] == 1
    # A previous authenticated session's stability timer must not leak into
    # the replacement socket epoch. The new socket authenticates, but its
    # incremental frame must still wait for a fresh initial tk snapshot.
    feed._connection_opened_at = 123.0
    feed._reset_feed_epoch()
    feed._authenticated = True
    assert feed._connection_opened_at is None
    feed._on_message(fake_ws, json.dumps({"t": "tf", "e": "NFO", "tk": "12345",
        "lp": "101.00", "ft": "1794551795"}))
    assert feed.snapshot()["ticks_received"] == 1
    assert ("NFO", "12345") not in feed._snapshots


def test_subscription_send_failure_marks_error_and_closes_socket():
    closed = []
    def fail_send(_frame):
        raise OSError("socket unavailable")
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    fake_ws = SimpleNamespace(send=fail_send, close=lambda: closed.append(True))
    feed.subscribe([descriptor()])
    feed._send_subscriptions(fake_ws)
    assert closed
    assert "socket unavailable" in feed.snapshot()["last_error"]


def test_unsubscribe_send_failure_marks_error_and_closes_socket():
    closed = []
    def fail_send(_frame):
        raise OSError("socket unavailable")
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    feed._ws, feed._authenticated = SimpleNamespace(send=fail_send, close=lambda: closed.append(True)), True
    assert feed.unsubscribe([descriptor()]) == 1
    assert closed
    assert "socket unavailable" in feed.snapshot()["last_error"]


def test_start_refuses_duplicate_workers_after_prior_shutdown_timeout():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    release = threading.Event()
    worker = threading.Thread(target=release.wait, name="blocked-old-dispatcher", daemon=True)
    worker.start()
    feed._dispatch_thread = worker
    try:
        feed.start()
    except RuntimeError as exc:
        assert "blocked-old-dispatcher" in str(exc)
    else:
        raise AssertionError("must not start duplicate workers while old dispatcher is alive")
    finally:
        release.set()
        worker.join(timeout=1)
    assert not worker.is_alive()


def test_concurrent_subscribe_then_unsubscribe_preserves_wire_order():
    entered_send = threading.Event()
    release_send = threading.Event()
    sent = []

    def send(frame):
        parsed = json.loads(frame)
        sent.append(parsed)
        if parsed["t"] == "t":
            entered_send.set()
            assert release_send.wait(timeout=2)

    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    instrument = descriptor()
    feed.subscribe([instrument])
    fake_ws = SimpleNamespace(send=send, close=lambda: None)
    feed._ws, feed._authenticated = fake_ws, True

    subscribe_thread = threading.Thread(target=feed._send_subscriptions, args=(fake_ws,))
    subscribe_thread.start()
    assert entered_send.wait(timeout=1)

    unsubscribe_thread = threading.Thread(target=feed.unsubscribe, args=([instrument],))
    unsubscribe_thread.start()
    # The shared lock protects desired state as well as wire writes: while
    # subscribe is sending, unsubscribe must not publish a premature local
    # removal that could diverge from the broker's eventual wire state.
    assert feed.snapshot()["subscriptions"] == 1
    assert unsubscribe_thread.is_alive()

    release_send.set()
    subscribe_thread.join(timeout=1)
    unsubscribe_thread.join(timeout=1)
    assert not subscribe_thread.is_alive() and not unsubscribe_thread.is_alive()
    assert feed.snapshot()["subscriptions"] == 0
    assert [frame["t"] for frame in sent] == ["t", "u"]


def test_stop_uses_one_shared_deadline_for_all_workers(monkeypatch):
    class SlowWorker:
        def __init__(self, name):
            self.name = name
            self.timeouts = []
        def is_alive(self):
            return True
        def join(self, timeout=None):
            self.timeouts.append(timeout)

    # Advance monotonic time deterministically on each read so the test proves
    # later workers receive only the remaining budget, without sleeping.
    clock = iter((0.0, 1.0, 2.0, 3.0))
    monkeypatch.setattr("app.market_data.shoonya_websocket.time.monotonic", lambda: next(clock))

    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    workers = [SlowWorker(name) for name in (
        "supervisor", "tick-dispatch", "order-dispatch", "heartbeat"
    )]
    feed._thread, feed._dispatch_thread, feed._order_dispatch_thread, feed._heartbeat_thread = workers
    feed.stop()
    assert [worker.timeouts for worker in workers] == [[2.0], [1.0], [], []]
    assert feed.snapshot()["shutdown_workers_alive"] == [
        "supervisor", "tick-dispatch", "order-dispatch", "heartbeat"
    ]
    assert feed.snapshot()["last_error"].startswith("workers did not stop")


def test_stale_socket_error_cannot_poison_replacement_connection():
    states = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    old_ws = SimpleNamespace()
    current_ws = SimpleNamespace()
    feed._ws = current_ws
    feed._connected = feed._authenticated = True
    feed._on_error(old_ws, OSError("old socket send failed"))

    assert feed.snapshot()["connected"] is True
    assert feed.snapshot()["authenticated"] is True
    assert feed.snapshot()["last_error"] is None
    assert states == []


def test_stale_socket_cannot_receive_heartbeat_after_reconnect():
    sent = []
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    old_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    new_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = new_ws
    feed._authenticated = True

    assert feed._send_heartbeat(old_ws) is False
    assert sent == []


def test_stale_socket_cannot_receive_subscription_replay_after_reconnect():
    sent = []
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    old_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    new_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = new_ws
    feed._authenticated = True

    feed._send_subscriptions(old_ws)
    assert sent == []


def test_stale_socket_cannot_receive_order_subscription_after_reconnect():
    sent = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_order_update=lambda event: None,
    )
    old_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    new_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = new_ws
    feed._authenticated = True

    feed._send_order_subscription(old_ws)
    assert sent == []


def test_stale_socket_callbacks_are_ignored_after_reconnect():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    old_ws = SimpleNamespace()
    new_ws = SimpleNamespace()
    feed._reset_feed_epoch()
    old_epoch = feed._feed_epoch
    feed._ws = old_ws
    received = []
    guarded = feed._guard_callback(lambda ws, raw: received.append((ws, raw)), old_epoch)

    feed._reset_feed_epoch()
    feed._ws = new_ws
    guarded(old_ws, '{"t":"tk","e":"NFO","tk":"12345"}')
    assert received == []
    guarded(new_ws, '{"t":"tk","e":"NFO","tk":"12345"}')
    assert received == []  # the callback is bound to the prior epoch even for a reused reference


def test_current_socket_callback_is_allowed_only_for_current_epoch():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    ws = SimpleNamespace()
    feed._reset_feed_epoch()
    epoch = feed._feed_epoch
    feed._ws = ws
    received = []
    guarded = feed._guard_callback(lambda socket, raw: received.append((socket, raw)), epoch)
    guarded(ws, "current")
    assert received == [(ws, "current")]


def test_market_and_order_events_are_ignored_before_authentication_ack():
    order_events = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_order_update=order_events.append,
    )
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws = fake_ws
    feed._on_open(fake_ws)

    feed._on_message(fake_ws, json.dumps({
        "t": "tk", "e": "NFO", "tk": "12345", "lp": "100.00",
        "ft": "1794551794",
    }))
    feed._on_message(fake_ws, json.dumps({
        "t": "om", "status": "COMPLETE", "norenordno": "ORDER1",
    }))

    assert feed.snapshot()["ticks_received"] == 0
    assert feed.snapshot()["dispatch_queue_depth"] == 0
    assert feed.snapshot()["order_queue_depth"] == 0
    assert order_events == []
    assert not feed.snapshot()["authenticated"]


def test_shoonya_configuration_is_default_off_and_fail_closed():
    from pydantic import ValidationError
    from app.core.config import Settings

    defaults = Settings(_env_file=None)
    assert defaults.SHOONYA_MARKET_DATA_ENABLED is False
    assert defaults.SHOONYA_MARKET_DATA_MAX_TOKENS == 500

    try:
        Settings(_env_file=None, SHOONYA_MARKET_DATA_ENABLED=True)
    except ValidationError as exc:
        assert "SHOONYA_UID" in str(exc)
        assert "SHOONYA_ACTID" in str(exc)
        assert "SHOONYA_ACCESS_TOKEN" in str(exc)
    else:
        raise AssertionError("enabled Shoonya feed must require all session credentials")


def test_shoonya_configuration_rejects_unsafe_limits():
    from pydantic import ValidationError
    from app.core.config import Settings

    try:
        Settings(_env_file=None, SHOONYA_MARKET_DATA_MAX_TOKENS=501)
    except ValidationError as exc:
        assert "SHOONYA_MARKET_DATA_MAX_TOKENS" in str(exc)
    else:
        raise AssertionError("Shoonya token limit must never exceed 500")


def test_adapter_redacts_credentials_from_error_logs_and_state():
    from io import StringIO

    from app.core.logger import app_logger
    from app.market_data.shoonya_websocket import ShoonyaWebSocket

    adapter = ShoonyaWebSocket(
        uid="client-secret-uid", actid="client-secret-act",
        access_token="sensitive-access-token",
    )
    captured_logs = StringIO()
    sink_id = app_logger.add(captured_logs, format="{message}", level="WARNING")
    try:
        adapter._on_error(None, RuntimeError("connection failed with sensitive-access-token"))
        logged_error = captured_logs.getvalue()
    finally:
        app_logger.remove(sink_id)

    stored_error = adapter.snapshot()["last_error"] or ""
    assert "sensitive-access-token" not in stored_error
    assert "sensitive-access-token" not in logged_error
    assert "[REDACTED]" in logged_error


def test_shoonya_owner_auth_uses_separate_scope_and_password(monkeypatch):
    from fastapi import HTTPException
    from app.core.security import create_access_token
    from app.market_data import shoonya_owner_auth as auth

    password = "shoonya-owner-password-123"
    monkeypatch.setenv("SHOONYA_MARKET_DATA_OWNER_PASSWORD", password)
    monkeypatch.setenv("BOX_SPREAD_REAL_OWNER_PASSWORD", "box-spread-owner-password-456")
    monkeypatch.setattr(auth.settings, "SECRET_KEY", "test-secret-key-that-is-long-enough-for-token-tests")
    auth._attempts.clear()
    result = auth.login_shoonya_owner(auth.ShoonyaOwnerLogin(password=password))
    assert result["expires_in"] == 1800
    assert auth.require_shoonya_owner_access("Bearer " + result["access_token"]) == 0

    box_token = create_access_token({
        "sub": "box-spread-real-owner", "scope": "box-spread-real-owner",
    })
    try:
        auth.require_shoonya_owner_access("Bearer " + box_token)
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Box Spread owner token must not authorize Shoonya controls")


def test_shoonya_owner_auth_fails_closed_without_dedicated_password(monkeypatch):
    from fastapi import HTTPException
    from app.market_data import shoonya_owner_auth as auth

    monkeypatch.delenv("SHOONYA_MARKET_DATA_OWNER_PASSWORD", raising=False)
    with pytest.raises(HTTPException) as raised:
        auth.require_shoonya_owner_access("Bearer anything")
    assert raised.value.status_code == 503

def test_supervisor_start_failure_rolls_back_only_new_batch_members(monkeypatch):
    from app.market_data.shoonya_supervisor import ShoonyaMarketDataSupervisor

    existing = descriptor()
    added = InstrumentDescriptor(
        key=InstrumentKey("NFO", "NFO", "67890"),
        symbol="NIFTY26NOV25100CE", instrument_type="option", exchange="NFO",
        segment="NFO", expiry="2026-11-26", strike=25100, option_type="CE",
        lot_size=65, tick_size=0.05, underlying="NIFTY",
    )
    supervisor = ShoonyaMarketDataSupervisor(
        uid="U1", actid="U1", access_token="token",
    )
    monkeypatch.setattr(supervisor._adapter, "start", lambda: None)
    assert supervisor.subscribe([existing]) == 1

    def fail_start():
        raise RuntimeError("simulated worker startup failure")

    monkeypatch.setattr(supervisor._adapter, "start", fail_start)
    try:
        supervisor.subscribe([existing, added])
    except RuntimeError as exc:
        assert "startup failure" in str(exc)
    else:
        raise AssertionError("simulated startup failure must propagate")

    assert supervisor.snapshot()["registry_instruments"] == 1
    assert supervisor._adapter.snapshot()["subscriptions"] == 1
    assert supervisor._adapter._descriptors[(existing.key.exchange, existing.key.token)] == existing
    assert (added.key.exchange, added.key.token) not in supervisor._adapter._descriptors

def test_concurrent_unsubscribe_then_resubscribe_preserves_final_wire_state():
    entered_send = threading.Event()
    release_send = threading.Event()
    sent = []

    def send(frame):
        parsed = json.loads(frame)
        sent.append(parsed)
        if parsed["t"] == "u":
            entered_send.set()
            assert release_send.wait(timeout=2)

    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    instrument = descriptor()
    feed.subscribe([instrument])
    fake_ws = SimpleNamespace(send=send, close=lambda: None)
    feed._ws, feed._authenticated = fake_ws, True

    unsubscribe_thread = threading.Thread(target=feed.unsubscribe, args=([instrument],))
    unsubscribe_thread.start()
    assert entered_send.wait(timeout=1)

    subscribe_thread = threading.Thread(target=feed.subscribe, args=([instrument],))
    subscribe_thread.start()
    # The subscribe must wait before mutating local desired state.
    assert feed.snapshot()["subscriptions"] == 0

    release_send.set()
    unsubscribe_thread.join(timeout=1)
    subscribe_thread.join(timeout=1)
    assert not unsubscribe_thread.is_alive() and not subscribe_thread.is_alive()
    assert [frame["t"] for frame in sent] == ["u", "t"]
    assert feed.snapshot()["subscriptions"] == 1



def test_frame_watchdog_marks_authenticated_silent_socket_degraded_and_closes():
    states = []
    closed = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
        frame_timeout_seconds=5.0,
    )
    fake_ws = SimpleNamespace(close=lambda: closed.append(True))
    feed._ws = fake_ws
    feed._connected = feed._authenticated = True
    feed._last_frame_at = __import__("time").time() - 6.0

    assert feed._check_frame_freshness(fake_ws) is True
    snapshot = feed.snapshot()
    assert closed == [True]
    assert states[-1][0] == "DEGRADED"
    assert "broker frame timeout" in states[-1][1]
    assert snapshot["authenticated"] is False
    assert snapshot["frame_timeout_seconds"] == 5.0
    assert snapshot["frame_stale"] is True


def test_frame_watchdog_keeps_fresh_authenticated_socket_open():
    closed = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        frame_timeout_seconds=5.0,
    )
    fake_ws = SimpleNamespace(close=lambda: closed.append(True))
    feed._ws = fake_ws
    feed._connected = feed._authenticated = True
    feed._last_frame_at = __import__("time").time()

    assert feed._check_frame_freshness(fake_ws) is False
    assert feed.snapshot()["authenticated"] is True
    assert closed == []


def test_frame_watchdog_ignores_unauthenticated_and_replaced_sockets():
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        frame_timeout_seconds=5.0,
    )
    stale_ws = SimpleNamespace(close=lambda: None)
    current_ws = SimpleNamespace(close=lambda: None)
    feed._ws = current_ws
    feed._last_frame_at = __import__("time").time() - 10.0

    assert feed._check_frame_freshness(stale_ws) is False
    assert feed.snapshot()["authenticated"] is False
    feed._authenticated = True
    assert feed._check_frame_freshness(stale_ws) is False
    assert feed.snapshot()["authenticated"] is True


def test_new_connection_epoch_resets_frame_freshness_and_old_timeout_reason():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token", frame_timeout_seconds=5.0)
    feed._last_frame_at = __import__("time").time() - 10.0
    feed._last_error = "broker frame timeout: stale"
    feed._reset_feed_epoch()
    assert feed.snapshot()["last_frame_at"] is None
    assert feed.snapshot()["last_frame_age_seconds"] is None
    assert feed.snapshot()["frame_stale"] is False


def test_shoonya_timestamp_normalizer_supports_epoch_units():
    from app.market_data.shoonya_normalizer import ShoonyaTickNormalizer

    seconds = 1_794_551_794
    expected_ns = seconds * 1_000_000_000
    assert ShoonyaTickNormalizer._timestamp_ns({"ft": str(seconds)}) == expected_ns
    assert ShoonyaTickNormalizer._timestamp_ns({"ft": str(seconds * 1_000)}) == expected_ns
    assert ShoonyaTickNormalizer._timestamp_ns({"ft": str(seconds * 1_000_000)}) == expected_ns
    assert ShoonyaTickNormalizer._timestamp_ns({"ft": str(expected_ns)}) == expected_ns
    assert ShoonyaTickNormalizer._timestamp_ns({"ft": "not-a-timestamp"}) == 0
    assert ShoonyaTickNormalizer._timestamp_ns({"ft": "0"}) == 0


def test_shoonya_normalized_record_exposes_canonical_exchange_timestamp():
    from app.market_data.shoonya_normalizer import ShoonyaTickNormalizer

    exchange_seconds = 1_794_551_794
    expected_ns = exchange_seconds * 1_000_000_000
    record = ShoonyaTickNormalizer().normalize(
        descriptor(),
        {
            "t": "tk", "e": "NFO", "tk": "12345",
            "lp": "100.00", "ft": str(exchange_seconds),
        },
    )
    assert record.timestamp_ns == expected_ns
    assert record.payload["exchange_timestamp_ns"] == expected_ns
    assert record.payload["source_timestamp_ns"] == expected_ns

def test_shoonya_adapter_rejects_out_of_order_exchange_ticks_but_allows_same_timestamp_updates():
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws, feed._authenticated = fake_ws, True

    base = 1_794_551_794
    feed._on_message(fake_ws, json.dumps({
        "t": "tk", "e": "NFO", "tk": "12345", "lp": "100.00", "ft": str(base),
    }))
    # Same exchange-second timestamps may contain quote changes and remain valid.
    feed._on_message(fake_ws, json.dumps({
        "t": "tf", "e": "NFO", "tk": "12345", "lp": "100.10", "ft": str(base),
    }))
    # A delayed older frame must never roll the instrument backwards.
    feed._on_message(fake_ws, json.dumps({
        "t": "tf", "e": "NFO", "tk": "12345", "lp": "99.00", "ft": str(base - 1),
    }))
    assert feed.snapshot()["ticks_received"] == 2
    assert feed.snapshot()["out_of_order_ticks"] == 1
    assert feed._snapshots[("NFO", "12345")]["lp"] == "100.10"

    feed._on_message(fake_ws, json.dumps({
        "t": "tf", "e": "NFO", "tk": "12345", "lp": "100.20", "ft": str(base + 1),
    }))
    assert feed.snapshot()["ticks_received"] == 3
    assert feed.snapshot()["out_of_order_ticks"] == 1


def test_authenticated_reconnect_replays_desired_subscription_set():
    sent = []
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    instruments = [descriptor()]
    feed.subscribe(instruments)

    old_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws = old_ws
    feed._authenticated = True
    feed._reset_feed_epoch()

    new_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = new_ws
    feed._on_message(new_ws, json.dumps({"t": "ak", "s": "OK"}))

    assert feed.snapshot()["authenticated"] is True
    assert feed.snapshot()["subscriptions"] == 1
    assert [json.loads(frame) for frame in sent] == [
        {"t": "t", "k": "NFO|12345"},
    ]


def test_tick_queue_overflow_is_counted_without_blocking_feed_thread():
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token", max_dispatch_queue=1,
    )
    closed = []
    states = []
    fake_ws = SimpleNamespace(close=lambda: closed.append(True))
    feed._ws = fake_ws
    feed._queue.put_nowait(("tick", {"symbol": "FIRST"}))
    feed._enqueue("tick", {"symbol": "SECOND"})

    snapshot = feed.snapshot()
    assert snapshot["dispatch_queue_depth"] == 1
    assert snapshot["dispatch_drops"] == 1
    assert snapshot["authenticated"] is False
    assert snapshot["last_error"].startswith("tick dispatch queue full:")
    assert closed == [True]
    assert feed._queue.get_nowait()[1]["symbol"] == "FIRST"


def test_order_queue_overflow_faults_feed_and_closes_socket():
    states = []
    closed = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    fake_ws = SimpleNamespace(close=lambda: closed.append(True))
    feed._ws = fake_ws
    for index in range(feed._order_queue.maxsize):
        feed._order_queue.put_nowait(("order", {"sequence": index}))

    feed._enqueue("order", {"sequence": "overflow"})
    snapshot = feed.snapshot()
    assert snapshot["order_update_drops"] == 1
    assert closed == [True]
    assert states[-1][0] == "ORDER_FEED_OVERFLOW"
    assert "reconcile broker order book" in states[-1][1]
    feed._on_close(fake_ws, 1000, "closed after overflow")
    assert states[-1][0] == "ORDER_FEED_OVERFLOW"



def test_authentication_watchdog_closes_socket_without_positive_ack():
    states = []
    closed = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        auth_timeout_seconds=5.0,
        on_state=lambda state, reason: states.append((state, reason)),
    )
    fake_ws = SimpleNamespace(close=lambda: closed.append(True))
    feed._ws = fake_ws
    feed._connected = True
    feed._authenticated = False
    feed._auth_started_at = __import__("time").monotonic() - 6.0

    assert feed._check_authentication_freshness(fake_ws) is True
    snapshot = feed.snapshot()
    assert closed == [True]
    assert states[-1][0] == "AUTH_TIMEOUT"
    assert "authentication acknowledgement timeout" in states[-1][1]
    assert snapshot["connected"] is False
    assert snapshot["authenticated"] is False
    assert snapshot["auth_timeout_seconds"] == 5.0


def test_authentication_watchdog_ignores_authenticated_or_replaced_socket():
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        auth_timeout_seconds=5.0,
    )
    stale_ws = SimpleNamespace(close=lambda: None)
    current_ws = SimpleNamespace(close=lambda: None)
    feed._ws = current_ws
    feed._auth_started_at = __import__("time").monotonic() - 10.0

    assert feed._check_authentication_freshness(stale_ws) is False
    feed._authenticated = True
    assert feed._check_authentication_freshness(current_ws) is False
    assert feed.snapshot()["authenticated"] is True


def test_authentication_timeout_state_survives_socket_close_callback():
    states = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    fake_ws = SimpleNamespace()
    feed._ws = fake_ws
    feed._last_error = "authentication acknowledgement timeout: no Shoonya ak frame for 16s (limit 15s)"
    feed._on_close(fake_ws, 1000, "closed by client")

    assert states[-1][0] == "AUTH_TIMEOUT"
    assert "authentication acknowledgement timeout" in states[-1][1]



def test_tick_queue_overflow_state_survives_socket_close_callback():
    states = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        max_dispatch_queue=1,
        on_state=lambda state, reason: states.append((state, reason)),
    )
    fake_ws = SimpleNamespace(close=lambda: None)
    feed._ws = fake_ws
    feed._queue.put_nowait(("tick", {"symbol": "FIRST"}))
    feed._enqueue("tick", {"symbol": "SECOND"})
    feed._on_close(fake_ws, 1000, "closed after overflow")

    assert states[-1][0] == "DEGRADED"
    assert "tick dispatch queue full" in states[-1][1]


def test_reconnect_backoff_uses_bounded_jitter(monkeypatch):
    import app.market_data.shoonya_websocket as shoonya_websocket_module

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        reconnect_min_seconds=2.0, reconnect_max_seconds=30.0,
    )
    bounds = []

    def choose_upper(lower, upper):
        bounds.append((lower, upper))
        return upper

    monkeypatch.setattr(shoonya_websocket_module.random, "uniform", choose_upper)
    assert feed._jittered_backoff_delay(2.0) == 2.4
    assert bounds[-1] == (2.0, 2.4)
    assert feed._jittered_backoff_delay(30.0) == 30.0
    assert bounds[-1] == (24.0, 30.0)


def test_authenticated_session_subscribes_order_feed_when_callback_configured():
    sent = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_order_update=lambda event: None,
    )
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = fake_ws

    feed._on_message(fake_ws, json.dumps({"t": "ak", "s": "OK"}))

    frames = [json.loads(frame) for frame in sent]
    assert frames == [
        {"t": "t", "k": "NFO|12345"},
        {"t": "o", "actid": "ACT123"},
    ]


def test_order_feed_subscription_is_replayed_after_authenticated_reconnect():
    sent = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_order_update=lambda event: None,
    )
    feed.subscribe([descriptor()])
    old_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws = old_ws
    feed._authenticated = True
    feed._reset_feed_epoch()
    new_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = new_ws

    feed._on_message(new_ws, json.dumps({"t": "ak", "s": "OK"}))

    frames = [json.loads(frame) for frame in sent]
    assert frames == [
        {"t": "t", "k": "NFO|12345"},
        {"t": "o", "actid": "ACT123"},
    ]


def test_order_feed_is_not_subscribed_without_order_callback():
    sent = []
    feed = ShoonyaWebSocket(uid="U1", actid="ACT123", access_token="token")
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: None)
    feed._ws = fake_ws

    feed._on_message(fake_ws, json.dumps({"t": "ak", "s": "OK"}))

    assert sent == []


def test_stale_socket_open_cannot_authenticate_or_send_credentials_after_reconnect():
    sent = []
    states = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    current_ws = SimpleNamespace(send=lambda frame: sent.append(("current", frame)), close=lambda: None)
    stale_ws = SimpleNamespace(send=lambda frame: sent.append(("stale", frame)), close=lambda: None)
    feed._ws = current_ws

    feed._on_open(stale_ws)

    assert sent == []
    assert feed.snapshot()["connected"] is False
    assert feed.snapshot()["authenticated"] is False
    assert states == []


def test_stale_socket_close_cannot_mark_replacement_session_disconnected():
    states = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    current_ws = SimpleNamespace()
    retired_ws = SimpleNamespace()
    feed._ws = current_ws
    feed._connected = True
    feed._authenticated = True

    feed._on_close(retired_ws, 1000, "late close from retired socket")

    assert feed.snapshot()["connected"] is True
    assert feed.snapshot()["authenticated"] is True
    assert states == []


def test_stale_auth_ack_cannot_mark_replacement_socket_live():
    states = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
    )
    current_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    stale_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws = current_ws

    feed._on_message(stale_ws, json.dumps({"t": "ak", "s": "OK"}))

    assert feed.snapshot()["authenticated"] is False
    assert feed._auth_ack_received is False
    assert states == []


def test_repeated_start_is_idempotent_and_restart_creates_one_new_worker_generation():
    created = []
    opened = []
    class BlockingSocket:
        def __init__(self, **callbacks):
            self.callbacks = callbacks
            self.closed = threading.Event()
            self.opened = threading.Event()
        def run_forever(self, **kwargs):
            self.opened.set()
            opened.append(self)
            self.closed.wait(timeout=3)
        def close(self):
            self.closed.set()

    def factory(url, **callbacks):
        socket = BlockingSocket(**callbacks)
        created.append(socket)
        return socket

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        websocket_factory=factory,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.02,
    )

    feed.start()
    assert created[0].opened.wait(timeout=1)
    first_supervisor = feed._thread
    first_dispatcher = feed._dispatch_thread
    feed.start()
    assert feed._thread is first_supervisor
    assert feed._dispatch_thread is first_dispatcher
    assert len(created) == 1

    feed.stop()
    assert not feed.snapshot()["shutdown_workers_alive"]

    feed.start()
    assert len(created) == 2 or (len(created) == 1 and created[0].opened.wait(timeout=0.1))
    if len(created) == 1:
        # The first fake socket has been closed; the second run loop may not
        # have entered its factory yet when the thread-start assertion occurs.
        deadline = __import__("time").monotonic() + 1
        while len(created) < 2 and __import__("time").monotonic() < deadline:
            threading.Event().wait(0.01)
    assert len(created) == 2
    assert created[1].opened.wait(timeout=1)
    feed.stop()
    assert not feed.snapshot()["shutdown_workers_alive"]


def test_read_only_tick_observer_receives_authenticated_generation_metadata_off_queue():
    observed = []
    legacy = []
    delivered = threading.Event()

    def observer(record, generation, authenticated, received_monotonic_ns):
        observed.append((record, generation, authenticated, received_monotonic_ns))
        delivered.set()

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_tick=legacy.append,
        on_tick_observation=observer,
    )
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=lambda frame: None, close=lambda: None)
    feed._ws = fake_ws
    feed._authenticated = True
    feed._feed_epoch = 7
    feed._handle_tick({
        "t": "tk", "e": "NFO", "tk": "12345", "ts": "NIFTY26NOV25000CE",
        "lp": "125.50", "bp1": "125.45", "sp1": "125.55",
        "bq1": "100", "sq1": "120", "ft": "1794551794",
    })

    assert feed.snapshot()["dispatch_queue_depth"] == 1
    item = feed._queue.get_nowait()
    feed._queue.task_done()
    assert len(item) == 3
    feed._queue.put_nowait(item)

    dispatcher = threading.Thread(target=feed._dispatch_loop, daemon=True)
    dispatcher.start()
    assert delivered.wait(timeout=1)
    feed._stop.set()
    dispatcher.join(timeout=1)

    assert not dispatcher.is_alive()
    assert len(legacy) == 1 and isinstance(legacy[0], dict)
    assert len(observed) == 1
    record, generation, authenticated, received_ns = observed[0]
    assert record.timestamp_ns == 1794551794000000000
    assert generation == 7
    assert authenticated is True
    assert received_ns > 0


def test_read_only_tick_observer_does_not_receive_retired_generation():
    observed = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_tick_observation=lambda *args: observed.append(args),
    )
    feed.subscribe([descriptor()])
    feed._authenticated = True
    feed._feed_epoch = 3
    feed._handle_tick({
        "t": "tk", "e": "NFO", "tk": "12345", "ts": "NIFTY26NOV25000CE",
        "lp": "125.50", "bp1": "125.45", "sp1": "125.55",
        "bq1": "100", "sq1": "120", "ft": "1794551794",
    })
    feed._reset_feed_epoch()
    # Drain the accepted legacy payload; the observer must be fenced.
    feed._stop.set()
    dispatcher = threading.Thread(target=feed._dispatch_loop, daemon=True)
    dispatcher.start()
    dispatcher.join(timeout=1)
    assert not dispatcher.is_alive()
    assert observed == []



def test_superseded_epoch_cannot_mutate_snapshot_after_normalization(monkeypatch):
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")
    feed.subscribe([descriptor()])
    feed._authenticated = True
    original_normalize = feed._normalizer.normalize

    def normalize_then_reconnect(instrument, payload):
        record = original_normalize(instrument, payload)
        feed._reset_feed_epoch()
        feed._authenticated = True
        return record

    monkeypatch.setattr(feed._normalizer, "normalize", normalize_then_reconnect)
    feed._handle_tick({
        "t": "tk", "e": "NFO", "tk": "12345", "ts": "NIFTY26NOV25000CE",
        "lp": "125.50", "bp1": "125.45", "sp1": "125.55", "ft": "1794551794",
    })

    assert ("NFO", "12345") not in feed._snapshots
    assert feed.snapshot()["ticks_received"] == 0
    assert feed.snapshot()["dispatch_queue_depth"] == 0


def test_observer_runs_even_if_legacy_tick_callback_raises():
    observed = []
    delivered = threading.Event()

    def observer(record, generation, authenticated, received_monotonic_ns):
        observed.append((record, generation, authenticated, received_monotonic_ns))
        delivered.set()

    def broken_legacy(_payload):
        raise RuntimeError("legacy consumer failure")

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_tick=broken_legacy,
        on_tick_observation=observer,
    )
    feed.subscribe([descriptor()])
    feed._authenticated = True
    feed._feed_epoch = 11
    feed._handle_tick({
        "t": "tk", "e": "NFO", "tk": "12345", "ts": "NIFTY26NOV25000CE",
        "lp": "125.50", "bp1": "125.45", "sp1": "125.55",
        "bq1": "100", "sq1": "120", "ft": "1794551794",
    })

    dispatcher = threading.Thread(target=feed._dispatch_loop, daemon=True)
    dispatcher.start()
    assert delivered.wait(timeout=1)
    feed._stop.set()
    dispatcher.join(timeout=1)

    assert not dispatcher.is_alive()
    assert len(observed) == 1
    assert observed[0][1:3] == (11, True)



def test_session_observer_is_additive_to_legacy_state_callback():
    states, observations = [], []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
        on_session_observation=lambda *args: observations.append(args),
    )
    feed._feed_epoch = 11
    feed._connected = True
    feed._authenticated = True
    feed._emit_state("LIVE", None)

    assert states == [("LIVE", None)]
    assert len(observations) == 1
    state, reason, epoch, connected, authenticated, received_ns = observations[0]
    assert (state, reason, epoch, connected, authenticated) == ("LIVE", None, 11, True, True)
    assert received_ns > 0


def test_session_observer_failure_does_not_break_legacy_state_callback():
    states = []
    def fail_observer(*_args):
        raise RuntimeError("observer failure")

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=lambda state, reason: states.append((state, reason)),
        on_session_observation=fail_observer,
    )
    feed._feed_epoch = 12
    feed._emit_state("AUTHENTICATING", None)
    assert states == [("AUTHENTICATING", None)]


def test_legacy_state_callback_failure_does_not_block_session_observer():
    observations = []
    def fail_state(_state, _reason):
        raise RuntimeError("legacy callback failure")

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_state=fail_state,
        on_session_observation=lambda *args: observations.append(args),
    )
    feed._feed_epoch = 13
    feed._connected = True
    feed._emit_state("AUTHENTICATING", None)
    assert len(observations) == 1
    assert observations[0][2] == 13


def test_partial_worker_start_failure_rolls_back_all_started_workers(monkeypatch):
    original_start = threading.Thread.start
    calls = {"count": 0}

    def fail_third_start(worker):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RuntimeError("simulated thread resource exhaustion")
        return original_start(worker)

    monkeypatch.setattr(threading.Thread, "start", fail_third_start)
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="token")

    with pytest.raises(RuntimeError, match="simulated thread resource exhaustion"):
        feed.start()

    assert calls["count"] == 3
    assert feed._stop.is_set()
    assert feed.snapshot()["shutdown_workers_alive"] == []
    assert feed._thread is None
    assert feed._dispatch_thread is None
    assert feed._order_dispatch_thread is None
    assert feed._heartbeat_thread is None


@pytest.mark.parametrize(
    "operation",
    ["open", "heartbeat", "market_subscription", "order_subscription"],
)
def test_blocked_shoonya_socket_write_does_not_hold_lifecycle_lock(operation):
    send_entered = threading.Event()
    release_send = threading.Event()
    close_called = threading.Event()

    def blocked_send(_frame):
        send_entered.set()
        assert release_send.wait(timeout=2.0)

    class BlockingWriteSocket:
        def send(self, frame):
            blocked_send(frame)

        def close(self):
            close_called.set()
            release_send.set()

    order_callback = (lambda _event: None) if operation == "order_subscription" else None
    feed = ShoonyaWebSocket(
        uid="U1", actid="ACT123", access_token="token",
        on_order_update=order_callback,
    )
    socket = BlockingWriteSocket()
    feed._ws = socket
    if operation != "open":
        feed._authenticated = True
    if operation in {"market_subscription", "order_subscription"}:
        feed.subscribe([descriptor()])

    def write():
        if operation == "open":
            feed._on_open(socket)
        elif operation == "heartbeat":
            feed._send_heartbeat(socket)
        elif operation == "market_subscription":
            feed._send_subscriptions(socket)
        else:
            feed._send_order_subscription(socket)

    writer = threading.Thread(target=write, name=f"blocked-{operation}-write")
    writer.start()
    assert send_entered.wait(timeout=1.0)

    stopper = threading.Thread(target=feed.stop, name=f"stop-during-{operation}-write")
    stopper.start()
    try:
        assert close_called.wait(timeout=0.5), (
            f"stop() could not close the socket while {operation} send was blocked; "
            "network I/O must not hold the lifecycle lock"
        )
    finally:
        release_send.set()
        writer.join(timeout=1.0)
        stopper.join(timeout=1.0)

    assert not writer.is_alive()
    assert not stopper.is_alive()
    assert feed.snapshot()["shutdown_workers_alive"] == []



def test_late_shoonya_factory_completion_cannot_resurrect_stopped_adapter():
    factory_entered = threading.Event()
    release_factory = threading.Event()
    created = []

    class DelayedSocket:
        def __init__(self):
            self.closed = False
            self.run_calls = 0

        def close(self):
            self.closed = True

        def run_forever(self, **kwargs):
            self.run_calls += 1

    def delayed_factory(*args, **kwargs):
        factory_entered.set()
        assert release_factory.wait(timeout=8.0)
        socket = DelayedSocket()
        created.append(socket)
        return socket

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        websocket_factory=delayed_factory,
    )
    feed.start()
    assert factory_entered.wait(timeout=2.0)
    try:
        feed.stop()
    finally:
        release_factory.set()

    supervisor = feed._thread
    assert supervisor is not None
    supervisor.join(timeout=2.0)
    assert not supervisor.is_alive()
    assert len(created) == 1
    assert created[0].closed is True
    assert created[0].run_calls == 0
    assert feed.snapshot()["connected"] is False
    assert feed._ws is None



def test_retired_epoch_queue_items_never_reach_legacy_or_shadow_callbacks():
    legacy, shadow = [], []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_tick=legacy.append,
        on_tick_observation=lambda *args: shadow.append(args),
    )
    feed._authenticated = True
    for retired_epoch in range(1, 8):
        feed._feed_epoch = retired_epoch
        feed._enqueue(
            "tick", {"epoch": retired_epoch},
            observation=(object(), retired_epoch, True, time.monotonic_ns()),
        )
    # Simulate the latest reconnect completing before the dispatcher drains.
    feed._feed_epoch = 8
    feed._authenticated = True
    feed._stop.set()
    dispatcher = threading.Thread(target=feed._dispatch_loop, name="stale-epoch-drain")
    dispatcher.start()
    dispatcher.join(timeout=2.0)

    assert not dispatcher.is_alive()
    assert legacy == []
    assert shadow == []
    assert feed.snapshot()["stale_dispatch_drops"] == 7
    assert feed.snapshot()["dispatch_queue_depth"] == 0
    assert feed.snapshot()["ticks_dispatched"] == 0


def test_shoonya_dispatch_queue_remains_bounded_during_reconnect_burst():
    close_calls = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        max_dispatch_queue=3,
    )
    feed._ws = SimpleNamespace(close=lambda: close_calls.append(True))
    feed._authenticated = True
    for epoch in range(1, 11):
        feed._feed_epoch = epoch
        feed._enqueue(
            "tick", {"epoch": epoch},
            observation=(object(), epoch, True, time.monotonic_ns()),
        )

    state = feed.snapshot()
    assert state["dispatch_queue_capacity"] == 3
    assert state["dispatch_queue_depth"] == 3
    assert state["dispatch_drops"] == 7
    assert len(close_calls) == 7



def test_shoonya_order_updates_from_retired_epoch_are_dropped():
    delivered = []
    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_order_update=delivered.append,
    )
    feed._feed_epoch = 4
    feed._authenticated = True
    feed._enqueue("order", {"t": "om", "status": "OLD"})
    feed._feed_epoch = 5
    feed._authenticated = True
    feed._stop.set()
    worker = threading.Thread(target=feed._order_dispatch_loop, daemon=True)
    worker.start()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert delivered == []
    assert feed.snapshot()["stale_dispatch_drops"] == 1
    assert feed.snapshot()["order_queue_depth"] == 0


def test_shoonya_shadow_observer_is_fenced_if_epoch_changes_during_legacy_callback():
    entered = threading.Event()
    release = threading.Event()
    observed = []

    def legacy(_payload):
        entered.set()
        assert release.wait(timeout=2.0)

    feed = ShoonyaWebSocket(
        uid="U1", actid="U1", access_token="token",
        on_tick=legacy,
        on_tick_observation=lambda *args: observed.append(args),
    )
    feed.subscribe([descriptor()])
    feed._authenticated = True
    feed._feed_epoch = 21
    feed._handle_tick({
        "t": "tk", "e": "NFO", "tk": "12345", "ts": "NIFTY26NOV25000CE",
        "lp": "125.50", "bp1": "125.45", "sp1": "125.55",
        "bq1": "100", "sq1": "120", "ft": "1794551794",
    })
    worker = threading.Thread(target=feed._dispatch_loop, daemon=True)
    worker.start()
    assert entered.wait(timeout=1.0)
    feed._reset_feed_epoch()
    feed._authenticated = True
    release.set()
    feed._stop.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert observed == []
    assert feed.snapshot()["stale_dispatch_drops"] == 1
