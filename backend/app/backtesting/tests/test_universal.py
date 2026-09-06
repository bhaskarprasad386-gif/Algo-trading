from app.backtesting.universal import MarketEvent, ordered_events, replay


class CollectStrategy:
    def on_event(self, event, history):
        return [{"timestamp_ns": event.timestamp_ns, "history_count": len(history)}]


def event(ts, instrument="NIFTY", sequence=0):
    return MarketEvent(ts, instrument, sequence, data=(("price", 100.0),))


def test_microsecond_timestamps_and_same_timestamp_order_are_preserved():
    events = [event(1_000_002, sequence=2), event(1_000_001, sequence=0), event(1_000_002, sequence=1)]
    ordered = ordered_events(events)
    assert [(item.timestamp_ns, item.sequence) for item in ordered] == [
        (1_000_001, 0), (1_000_002, 1), (1_000_002, 2)
    ]


def test_replay_does_not_invent_intermediate_microsecond_events():
    decisions = replay([event(1_000_000), event(1_002_000)], CollectStrategy())
    assert [item["timestamp_ns"] for item in decisions] == [1_000_000, 1_002_000]
    assert decisions[-1]["history_count"] == 1


def test_multiple_instruments_are_supported():
    decisions = replay([event(2, "OPT-CALL"), event(1, "FUT"), event(1, "OPT-PUT")], CollectStrategy())
    assert len(decisions) == 3
    assert decisions[0]["timestamp_ns"] == 1
