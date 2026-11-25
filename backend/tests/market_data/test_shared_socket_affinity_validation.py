from dataclasses import replace

import pytest

from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor
from app.market_data.shared_socket_affinity import ConsumerSocketAffinity


def descriptor(token: str, *, underlying: str, instrument_type: str, expiry_type: str | None = None):
    exchange = "NSE" if instrument_type == "index" else "NFO"
    return InstrumentDescriptor(
        key=InstrumentKey(exchange, exchange, token),
        symbol=f"{underlying}-{token}",
        instrument_type=instrument_type,
        exchange=exchange,
        segment=exchange,
        underlying=underlying,
        expiry_type=expiry_type,
        expiry="2026-12-31" if instrument_type != "index" else None,
    )


def test_instrument_plan_reports_unlocked_instruments_without_assigning_reserved_lane():
    planner = ConsumerSocketAffinity()
    items = (
        descriptor("NIFTY-M", underlying="NIFTY", instrument_type="option", expiry_type="MONTHLY"),
        descriptor("BANK-W", underlying="BANKNIFTY", instrument_type="option", expiry_type="WEEKLY"),
        descriptor("FIN-F", underlying="FINNIFTY", instrument_type="future"),
        descriptor("SENSEX-I", underlying="SENSEX", instrument_type="index"),
    )

    plan = planner.plan_instruments(
        items, mode=3, exchange_type_resolver=lambda _key: 2,
    )

    assert plan.assignments == {}
    assert plan.token_pairs_by_slot == {}
    assert plan.unassigned_keys == frozenset(item.key for item in items)


def test_instrument_plan_rejects_conflicting_duplicate_descriptor_before_planning():
    planner = ConsumerSocketAffinity()
    nifty_weekly = descriptor(
        "SAME", underlying="NIFTY", instrument_type="option", expiry_type="WEEKLY",
    )
    nifty_monthly = replace(nifty_weekly, expiry_type="MONTHLY")

    with pytest.raises(ValueError, match="conflicting descriptors"):
        planner.plan_instruments(
            (nifty_weekly, nifty_monthly),
            mode=3,
            exchange_type_resolver=lambda _key: 2,
        )
