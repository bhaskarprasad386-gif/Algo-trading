from dataclasses import dataclass

import pytest

from app.brokers.session_store import BrokerSessionStore


@dataclass
class FakeAdapter:
    name: str
    connected: bool = True

    def connect(self, **credentials):
        return {"connected": True}

    def place_order(self, *args, **kwargs):
        raise NotImplementedError

    def cancel_order(self, *args, **kwargs):
        raise NotImplementedError

    def disconnect(self):
        self.connected = False


def test_session_store_normalizes_broker_key_and_isolates_owners():
    store = BrokerSessionStore()
    first = FakeAdapter("angel_one")
    second = FakeAdapter("angel_one")

    assert store.replace(7, " ANGEL_ONE ", first) is None
    assert store.get(7, "angel_one") is first
    assert store.get(8, "angel_one") is None

    previous = store.replace(7, "Angel_One", second)
    assert previous is first
    assert store.get(7, "ANGEL_ONE") is second


def test_session_store_rejects_adapter_for_different_broker():
    store = BrokerSessionStore()
    with pytest.raises(ValueError, match="does not match"):
        store.replace(7, "zerodha", FakeAdapter("fyers"))


def test_session_store_remove_and_clear_owner_return_sessions():
    store = BrokerSessionStore()
    angel = FakeAdapter("angel_one")
    fyers = FakeAdapter("fyers")
    zerodha = FakeAdapter("zerodha")
    store.replace(7, "angel_one", angel)
    store.replace(7, "fyers", fyers)
    store.replace(8, "zerodha", zerodha)

    assert store.remove(7, "ANGEL_ONE") is angel
    assert store.remove(7, "angel_one") is None
    assert store.clear_owner(7) == [fyers]
    assert store.get(8, "zerodha") is zerodha


def test_session_store_clear_returns_all_sessions():
    store = BrokerSessionStore()
    angel = FakeAdapter("angel_one")
    store.replace(7, "angel_one", angel)

    assert store.clear() == [angel]
    assert store.get(7, "angel_one") is None


def test_session_store_rejects_empty_broker_name():
    store = BrokerSessionStore()
    with pytest.raises(ValueError, match="cannot be empty"):
        store.get(7, "   ")



def test_disconnect_all_closes_every_session_and_clears_store():
    store = BrokerSessionStore()
    angel = FakeAdapter("angel_one")
    fyers = FakeAdapter("fyers")
    store.replace(7, "angel_one", angel)
    store.replace(8, "fyers", fyers)

    assert store.disconnect_all() == []
    assert angel.connected is False
    assert fyers.connected is False
    assert store.get(7, "angel_one") is None
    assert store.get(8, "fyers") is None


def test_disconnect_all_continues_after_one_provider_disconnect_fails():
    class BrokenAdapter(FakeAdapter):
        def disconnect(self):
            raise RuntimeError("provider logout unavailable")

    store = BrokerSessionStore()
    broken = BrokenAdapter("angel_one")
    healthy = FakeAdapter("fyers")
    store.replace(7, "angel_one", broken)
    store.replace(8, "fyers", healthy)

    failures = store.disconnect_all()

    assert len(failures) == 1
    assert failures[0][0] == "angel_one"
    assert isinstance(failures[0][1], RuntimeError)
    assert healthy.connected is False
    assert store.get(7, "angel_one") is None
    assert store.get(8, "fyers") is None


def test_replace_and_disconnect_previous_keeps_new_session_when_old_logout_fails():
    class BrokenAdapter(FakeAdapter):
        def disconnect(self):
            raise RuntimeError("provider logout unavailable")

    store = BrokerSessionStore()
    previous = BrokenAdapter("fyers")
    current = FakeAdapter("fyers")
    store.replace(7, "fyers", previous)

    error = store.replace_and_disconnect_previous(7, "fyers", current)

    assert isinstance(error, RuntimeError)
    assert store.get(7, "fyers") is current
    assert current.connected is True


def test_session_lease_keeps_adapter_connected_until_request_finishes():
    from threading import Event, Thread

    store = BrokerSessionStore()
    adapter = FakeAdapter("angel_one")
    store.replace(7, "angel_one", adapter)
    acquired = Event()
    release = Event()

    def request():
        with store.lease(7, "angel_one") as leased:
            assert leased is adapter
            acquired.set()
            assert release.wait(2)

    worker = Thread(target=request)
    worker.start()
    assert acquired.wait(2)

    # Revocation makes the session unavailable to new requests immediately.
    removed = []

    def revoke():
        removed.append(store.remove(7, "angel_one"))

    revoker = Thread(target=revoke)
    revoker.start()
    assert store.get(7, "angel_one") is None
    assert revoker.is_alive()
    assert adapter.connected is True

    release.set()
    worker.join(2)
    revoker.join(2)
    assert not worker.is_alive()
    assert not revoker.is_alive()
    assert removed == [adapter]


def test_new_request_cannot_lease_a_session_after_replacement():
    from threading import Event, Thread

    store = BrokerSessionStore()
    previous = FakeAdapter("fyers")
    current = FakeAdapter("fyers")
    store.replace(9, "fyers", previous)
    acquired = Event()
    release = Event()

    def request():
        with store.lease(9, "fyers") as leased:
            assert leased is previous
            acquired.set()
            assert release.wait(2)

    worker = Thread(target=request)
    worker.start()
    assert acquired.wait(2)

    disconnected = []
    def reconnect():
        disconnected.append(
            store.replace_and_disconnect_previous(9, "fyers", current)
        )

    reconnecter = Thread(target=reconnect)
    reconnecter.start()
    # New leases resolve only to the replacement, while old lease drains.
    with store.lease(9, "fyers") as leased:
        assert leased is current
    assert previous.connected is True
    assert reconnecter.is_alive()

    release.set()
    worker.join(2)
    reconnecter.join(2)
    assert not worker.is_alive()
    assert not reconnecter.is_alive()
    assert disconnected == [None]
    assert previous.connected is False
    assert current.connected is True


def test_lease_returns_none_when_session_is_absent():
    store = BrokerSessionStore()
    with store.lease(7, "angel_one") as adapter:
        assert adapter is None



def test_replacing_session_with_same_adapter_does_not_disconnect_live_session():
    store = BrokerSessionStore()
    adapter = FakeAdapter("fyers")
    store.replace(7, "fyers", adapter)

    error = store.replace_and_disconnect_previous(7, "fyers", adapter)

    assert error is None
    assert store.get(7, "fyers") is adapter
    assert adapter.connected is True



@pytest.mark.parametrize("cleanup", ["clear", "clear_owner"])
def test_bulk_cleanup_waits_for_active_request_lease(cleanup):
    from threading import Event, Thread

    store = BrokerSessionStore()
    adapter = FakeAdapter("angel_one")
    store.replace(7, "angel_one", adapter)
    acquired = Event()
    release = Event()
    cleaned = Event()

    def request():
        with store.lease(7, "angel_one") as leased:
            assert leased is adapter
            acquired.set()
            assert release.wait(2)

    def revoke():
        if cleanup == "clear":
            assert store.clear() == [adapter]
        else:
            assert store.clear_owner(7) == [adapter]
        cleaned.set()

    worker = Thread(target=request)
    worker.start()
    assert acquired.wait(2)

    revoker = Thread(target=revoke)
    revoker.start()
    assert not cleaned.wait(0.05)
    assert adapter.connected is True
    assert store.get(7, "angel_one") is None

    release.set()
    worker.join(2)
    revoker.join(2)
    assert not worker.is_alive()
    assert not revoker.is_alive()
    assert cleaned.is_set()
