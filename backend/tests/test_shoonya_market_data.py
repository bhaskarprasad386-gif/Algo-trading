import pytest
import json
import threading
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


def test_rejected_authentication_never_subscribes():
    sent, closed = [], []
    feed = ShoonyaWebSocket(uid="U1", actid="U1", access_token="bad")
    feed.subscribe([descriptor()])
    fake_ws = SimpleNamespace(send=sent.append, close=lambda: closed.append(True))
    feed._on_open(fake_ws)
    feed._on_message(fake_ws, json.dumps({"t": "ak", "s": "Not_Ok"}))
    assert not feed.snapshot()["authenticated"]
    assert feed._connection_opened_at is None
    assert feed.snapshot()["last_error"] == "Shoonya authentication rejected"
    assert closed
    assert all(json.loads(frame)["t"] != "t" for frame in sent)


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
