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



@pytest.mark.parametrize("invalid_owner", [True, False, -1, 1.5, "7", None])
def test_session_store_rejects_non_integer_or_negative_owner_ids(invalid_owner):
    store = BrokerSessionStore()
    with pytest.raises(ValueError, match="owner ID"):
        store.get(invalid_owner, "shoonya")
    with pytest.raises(ValueError, match="owner ID"):
        store.clear_owner(invalid_owner)


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


def test_commit_if_current_rejects_session_superseded_by_new_reservation():
    store = BrokerSessionStore()
    adapter = FakeAdapter("shoonya")
    generation = store.reserve_replacement(7, "shoonya")
    installed, previous = store.replace_if_reserved(7, "shoonya", adapter, generation)
    assert installed is True
    assert previous is None

    # A later OAuth attempt reserves ownership while the prior adapter is
    # still installed and before the later attempt finishes its exchange.
    newer_generation = store.reserve_replacement(7, "shoonya")
    committed = []
    accepted = store.commit_if_current(
        7,
        "shoonya",
        adapter,
        lambda: committed.append(True),
        generation=generation,
    )

    assert newer_generation > generation
    assert accepted is False
    assert committed == []
    assert store.get(7, "shoonya") is adapter


def test_commit_if_current_accepts_matching_active_reservation():
    store = BrokerSessionStore()
    adapter = FakeAdapter("shoonya")
    generation = store.reserve_replacement(7, "shoonya")
    installed, _ = store.replace_if_reserved(7, "shoonya", adapter, generation)
    assert installed is True

    committed = []
    assert store.commit_if_current(
        7,
        "shoonya",
        adapter,
        lambda: committed.append(True),
        generation=generation,
    )
    assert committed == [True]


def test_stale_reconnect_cannot_remove_adapter_owned_by_newer_reservation():
    store = BrokerSessionStore()
    adapter = FakeAdapter("shoonya")
    generation = store.reserve_replacement(7, "shoonya")
    installed, _ = store.replace_if_reserved(7, "shoonya", adapter, generation)
    assert installed is True

    store.reserve_replacement(7, "shoonya")
    removed = store.remove_if_current(
        7, "shoonya", adapter, generation=generation
    )

    assert removed is False
    assert store.get(7, "shoonya") is adapter
    assert adapter.connected is True


def test_failed_installed_adapter_can_be_detached_without_invalidating_newer_reservation():
    store = BrokerSessionStore()
    failed_adapter = FakeAdapter("shoonya")
    generation = store.reserve_replacement(7, "shoonya")
    installed, _ = store.replace_if_reserved(7, "shoonya", failed_adapter, generation)
    assert installed is True

    newer_generation = store.reserve_replacement(7, "shoonya")
    assert store.remove_if_adapter_current(7, "shoonya", failed_adapter) is True
    assert store.get(7, "shoonya") is None

    newer_adapter = FakeAdapter("shoonya")
    accepted, previous = store.replace_if_reserved(7, "shoonya", newer_adapter, newer_generation)
    assert accepted is True
    assert previous is None
    assert store.get(7, "shoonya") is newer_adapter


def test_failed_old_adapter_cannot_detach_newer_installed_adapter():
    store = BrokerSessionStore()
    old_adapter = FakeAdapter("shoonya")
    old_generation = store.reserve_replacement(7, "shoonya")
    assert store.replace_if_reserved(7, "shoonya", old_adapter, old_generation)[0] is True

    newer_generation = store.reserve_replacement(7, "shoonya")
    newer_adapter = FakeAdapter("shoonya")
    assert store.replace_if_reserved(7, "shoonya", newer_adapter, newer_generation)[0] is True

    assert store.remove_if_adapter_current(7, "shoonya", old_adapter) is False
    assert store.get(7, "shoonya") is newer_adapter


def test_current_reconnect_can_remove_its_own_failed_adapter():
    store = BrokerSessionStore()
    adapter = FakeAdapter("shoonya")
    generation = store.reserve_replacement(7, "shoonya")
    installed, _ = store.replace_if_reserved(7, "shoonya", adapter, generation)
    assert installed is True

    assert store.remove_if_current(
        7, "shoonya", adapter, generation=generation
    )
    assert store.get(7, "shoonya") is None

def test_clear_owner_invalidates_pending_replacement_without_installed_session():
    store = BrokerSessionStore()
    stale_generation = store.reserve_replacement(7, "shoonya")

    assert store.clear_owner(7) == []
    adapter = FakeAdapter("shoonya")
    installed, previous = store.replace_if_reserved(
        7, "shoonya", adapter, stale_generation
    )

    assert installed is False
    assert previous is None
    assert store.get(7, "shoonya") is None


def test_clear_invalidates_pending_replacement_without_installed_session():
    store = BrokerSessionStore()
    stale_generation = store.reserve_replacement(7, "shoonya")

    assert store.clear() == []
    adapter = FakeAdapter("shoonya")
    installed, previous = store.replace_if_reserved(
        7, "shoonya", adapter, stale_generation
    )

    assert installed is False
    assert previous is None
    assert store.get(7, "shoonya") is None


def test_remove_invalidates_pending_replacement_even_when_session_is_absent():
    store = BrokerSessionStore()
    stale_generation = store.reserve_replacement(7, "shoonya")

    assert store.remove(7, "shoonya") is None
    installed, previous = store.replace_if_reserved(
        7, "shoonya", FakeAdapter("shoonya"), stale_generation
    )

    assert installed is False
    assert previous is None


def test_unreserved_replace_invalidates_older_pending_replacement():
    store = BrokerSessionStore()
    stale_generation = store.reserve_replacement(7, "shoonya")
    current = FakeAdapter("shoonya")

    assert store.replace(7, "shoonya", current) is None
    installed, previous = store.replace_if_reserved(
        7, "shoonya", FakeAdapter("shoonya"), stale_generation
    )

    assert installed is False
    assert previous is None
    assert store.get(7, "shoonya") is current


def test_wrong_adapter_does_not_consume_valid_replacement_reservation():
    store = BrokerSessionStore()
    generation = store.reserve_replacement(7, "shoonya")

    with pytest.raises(ValueError, match="does not match"):
        store.replace_if_reserved(
            7, "shoonya", FakeAdapter("angel_one"), generation
        )

    expected = FakeAdapter("shoonya")
    installed, previous = store.replace_if_reserved(
        7, "shoonya", expected, generation
    )
    assert installed is True
    assert previous is None
    assert store.get(7, "shoonya") is expected


def test_failed_metadata_commit_releases_lock_and_preserves_current_session():
    store = BrokerSessionStore()
    adapter = FakeAdapter("shoonya")
    generation = store.reserve_replacement(7, "shoonya")
    installed, _ = store.replace_if_reserved(7, "shoonya", adapter, generation)
    assert installed is True

    def fail_commit():
        raise RuntimeError("metadata write failed")

    with pytest.raises(RuntimeError, match="metadata write failed"):
        store.commit_if_current(
            7, "shoonya", adapter, fail_commit, generation=generation
        )

    committed = []
    assert store.commit_if_current(
        7, "shoonya", adapter, lambda: committed.append(True),
        generation=generation,
    )
    assert committed == [True]
    assert store.get(7, "shoonya") is adapter
