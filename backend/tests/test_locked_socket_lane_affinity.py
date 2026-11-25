from dataclasses import replace

from app.market_data.contracts import InstrumentKey
from app.market_data.registry import InstrumentDescriptor


def descriptor(kind, underlying, expiry_type=None):
    return InstrumentDescriptor(
        key=InstrumentKey("NFO" if kind != "index" else "NSE", "NFO" if kind != "index" else "NSE", f"{kind}-{underlying}-{expiry_type or 'spot'}"),
        symbol=f"{underlying}-{kind}",
        instrument_type=kind,
        exchange="NFO" if kind != "index" else "NSE",
        segment="NFO" if kind != "index" else "NSE",
        underlying=underlying,
        expiry_type=expiry_type,
    )


def test_locked_universe_has_exact_three_preferred_lanes():
    assert descriptor("index", "NIFTY").preferred_socket_lane == 0
    assert descriptor("option", "NIFTY", "WEEKLY").preferred_socket_lane == 0
    assert descriptor("index", "BANKNIFTY").preferred_socket_lane == 1
    assert descriptor("option", "BANKNIFTY", "MONTHLY").preferred_socket_lane == 1
    assert descriptor("future", "NIFTY").preferred_socket_lane == 2
    assert descriptor("future", "BANKNIFTY").preferred_socket_lane == 2


def test_wrong_expiry_family_and_other_indices_are_not_assigned_to_locked_lanes():
    assert descriptor("option", "NIFTY", "MONTHLY").preferred_socket_lane is None
    assert descriptor("option", "BANKNIFTY", "WEEKLY").preferred_socket_lane is None
    for symbol in ("FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"):
        assert descriptor("index", symbol).preferred_socket_lane is None
        assert descriptor("future", symbol).preferred_socket_lane is None



def test_wrong_exchange_instruments_do_not_inherit_locked_lane_affinity():
    wrong_nifty_spot = replace(descriptor("index", "NIFTY"), exchange="BSE", segment="BSE")
    wrong_bank_spot = replace(descriptor("index", "BANKNIFTY"), exchange="BSE", segment="BSE")
    wrong_nifty_option = replace(
        descriptor("option", "NIFTY", "WEEKLY"),
        exchange="BFO",
        segment="BFO",
    )
    wrong_bank_future = replace(
        descriptor("future", "BANKNIFTY"),
        exchange="BFO",
        segment="BFO",
    )

    assert wrong_nifty_spot.preferred_socket_lane is None
    assert wrong_bank_spot.preferred_socket_lane is None
    assert wrong_nifty_option.preferred_socket_lane is None
    assert wrong_bank_future.preferred_socket_lane is None
