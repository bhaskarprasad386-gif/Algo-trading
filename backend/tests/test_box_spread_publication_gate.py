import threading
import time

from app.scanner.box_spread_publication_gate import BoxSpreadPublicationGate


def test_publication_gate_transfer_waits_for_inflight_old_publisher():
    gate = BoxSpreadPublicationGate()
    old_owner = object()
    new_owner = object()
    gate.activate(old_owner)
    entered = threading.Event()
    release = threading.Event()
    published = []

    def old_publish():
        entered.set()
        assert release.wait(2)
        published.append("old")

    publisher = threading.Thread(target=lambda: gate.publish(old_owner, old_publish))
    publisher.start()
    assert entered.wait(1)

    activated = threading.Event()

    def activate_new():
        gate.activate(new_owner)
        activated.set()

    transfer = threading.Thread(target=activate_new)
    transfer.start()
    time.sleep(0.03)
    assert not activated.is_set()

    release.set()
    publisher.join(1)
    transfer.join(1)
    assert activated.is_set()
    assert published == ["old"]
    assert gate.is_owner(new_owner)
    assert gate.publish(old_owner, lambda: published.append("stale")) is False
    assert published == ["old"]


def test_publication_gate_rejects_non_owner_and_deactivation_is_owner_scoped():
    gate = BoxSpreadPublicationGate()
    first = object()
    second = object()
    published = []
    gate.activate(first)
    assert gate.publish(second, lambda: published.append("wrong")) is False
    assert gate.deactivate(second) is False
    assert gate.is_owner(first)
    assert gate.deactivate(first) is True
    assert gate.is_owner(first) is False
    assert published == []
