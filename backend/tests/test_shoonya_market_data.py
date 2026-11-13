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
    feed._on_message(fake_ws, json.dumps({"t": "tk", "e": "NFO", "tk": "12345",
        "lp": "100.00", "oi": "777", "ft": "1794551794"}))
    assert feed.snapshot()["ticks_received"] == 1
    # A previous authenticated session's stability timer must not leak into
    # the replacement socket epoch.
    feed._connection_opened_at = 123.0
    feed._reset_feed_epoch()
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
