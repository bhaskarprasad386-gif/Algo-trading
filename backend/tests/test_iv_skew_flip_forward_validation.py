from app.scanner.iv_skew_flip.validation import (
    SignalEvent,
    SpotPoint,
    group_outcomes_by_date,
    measure_forward_outcomes,
    load_persisted_forward_outcomes,
)


MINUTE = 60 * 1_000_000_000


def test_forward_outcomes_use_first_observation_at_or_after_target():
    event = SignalEvent(
        event_timestamp_ns=1_000 * MINUTE,
        spot_price=100.0,
        direction="BULLISH",
        state="PRE_MOVE",
        underlying="NIFTY",
    )
    points = [
        SpotPoint(1_009 * MINUTE, 105.0),  # before target: must not be used
        SpotPoint(1_011 * MINUTE, 102.0),  # first observation after 10m target
        SpotPoint(1_031 * MINUTE, 103.0),
    ]

    outcomes = measure_forward_outcomes(
        event, points, horizons_minutes=(10,), max_observation_delay_seconds=120,
    )

    assert len(outcomes) == 1
    assert outcomes[0].observed_timestamp_ns == 1_011 * MINUTE
    assert outcomes[0].raw_return_pct == 2.0
    assert outcomes[0].direction_adjusted_return_pct == 2.0
    assert outcomes[0].observation_delay_seconds == 60.0


def test_bearish_direction_adjusts_return_and_missing_future_is_omitted():
    event = SignalEvent(
        event_timestamp_ns=2_000 * MINUTE,
        spot_price=200.0,
        direction="BEARISH",
        state="CONFIRMED",
    )
    points = [SpotPoint(2_011 * MINUTE, 196.0)]

    outcomes = measure_forward_outcomes(event, points, horizons_minutes=(10, 30))

    assert len(outcomes) == 1
    assert outcomes[0].raw_return_pct == -2.0
    assert outcomes[0].direction_adjusted_return_pct == 2.0
    assert outcomes[0].horizon_minutes == 10


def test_late_future_observation_is_not_filled_or_interpolated():
    event = SignalEvent(3_000 * MINUTE, 100.0, "BULLISH", "PRE_MOVE")
    points = [SpotPoint(3_014 * MINUTE, 101.0)]

    assert measure_forward_outcomes(
        event, points, horizons_minutes=(10,), max_observation_delay_seconds=120,
    ) == []


def test_invalidated_and_invalid_events_do_not_receive_outcomes():
    points = [SpotPoint(4_011 * MINUTE, 101.0)]
    event = SignalEvent(4_000 * MINUTE, 100.0, "BULLISH", "INVALIDATED")
    assert measure_forward_outcomes(event, points) == []


def test_trading_date_uses_ist_across_utc_midnight():
    from datetime import datetime, timezone

    from app.scanner.iv_skew_flip.validation import trading_date_ist

    # 20:00 UTC on Jan 1 is 01:30 IST on Jan 2.
    timestamp_ns = int(datetime(
        2026, 1, 1, 20, 0, tzinfo=timezone.utc
    ).timestamp() * 1_000_000_000)

    assert trading_date_ist(timestamp_ns) == "2026-01-02"


def test_outcomes_are_grouped_by_event_date_not_random_rows():
    first = SignalEvent(1_000 * MINUTE, 100.0, "BULLISH", "PRE_MOVE")
    second = SignalEvent(2_000 * MINUTE, 100.0, "BEARISH", "CONFIRMED")
    rows = [
        (first, measure_forward_outcomes(first, [SpotPoint(1_011 * MINUTE, 101.0)])[0]),
        (second, measure_forward_outcomes(second, [SpotPoint(2_011 * MINUTE, 99.0)])[0]),
    ]

    grouped = group_outcomes_by_date(rows)

    assert sum(map(len, grouped.values())) == 2
    assert list(grouped) == sorted(grouped)



def test_database_join_uses_deduplicated_source_spot_ticks_and_event_filters():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base
    from app.models.iv_option_observation import IVOptionObservation
    from app.models.iv_signal_event import IVSignalEvent

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        IVSignalEvent.__table__, IVOptionObservation.__table__,
    ])
    session = sessionmaker(bind=engine)()
    minute = 60 * 1_000_000_000
    try:
        session.add(IVSignalEvent(
            stream_id="NIFTY_WEEKLY", underlying="NIFTY", expiry_type="WEEKLY",
            state="PRE_MOVE", direction="BULLISH",
            event_timestamp_ns=10_000 * minute,
            source_timestamp_ns=10_000 * minute,
            spot_timestamp_ns=10_000 * minute,
            spot_price=100.0, contracts_confirming=2,
            qualifying_tokens='["ce1","ce2"]', reason="test",
        ))
        for token in ("ce1", "ce2"):
            session.add(IVOptionObservation(
                token=token, underlying="NIFTY", expiry="2026-11-12",
                strike=100.0, option_type="CE",
                source_timestamp_ns=10_011 * minute,
                spot_timestamp_ns=10_011 * minute,
                spot_price=102.0, sampled_at_ns=10_011 * minute,
                iv=20.0, oi=100, bid=1.0, ask=1.1, ltp=1.05,
                mark=1.05, volume=10,
            ))
        session.commit()

        rows = load_persisted_forward_outcomes(
            session, underlying="NIFTY", expiry_type="WEEKLY",
            horizons_minutes=(10,),
        )
        assert len(rows) == 1
        assert rows[0][1].direction_adjusted_return_pct == 2.0
    finally:
        session.close()
        Base.metadata.drop_all(engine, tables=[
            IVSignalEvent.__table__, IVOptionObservation.__table__,
        ])
        engine.dispose()
