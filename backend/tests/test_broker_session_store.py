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


def test_replace_and_disconnect_previous_does_not_disconnect_same_adapter():
    store = BrokerSessionStore()
    adapter = FakeAdapter("shoonya")
    store.replace(7, "shoonya", adapter)

    error = store.replace_and_disconnect_previous(7, "shoonya", adapter)

    assert error is None
    assert store.get(7, "shoonya") is adapter
    assert adapter.connected is True
