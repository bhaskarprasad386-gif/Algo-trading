from datetime import date

import pytest

from app.scanner.iv_skew_flip.universe import resolve_option_universe


def master_rows(symbol="NIFTY", expiries=("12Nov2026", "19Nov2026")):
    rows = []
    for expiry_index, expiry in enumerate(expiries):
        for strike in range(21800, 22201, 50):
            for side in ("CE", "PE"):
                rows.append({
                    "exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": symbol,
                    "expiry": expiry, "symbol": f"{symbol}{expiry}{strike}{side}",
                    "strike": str(strike * 100), "token": f"{expiry_index}-{strike}-{side}",
                    "lotsize": "75" if symbol == "NIFTY" else "30",
                })
    return rows


def test_nifty_weekly_resolves_nearest_expiry_and_seven_strikes():
    result = resolve_option_universe(
        master_rows(), underlying="NIFTY", expiry_type="WEEKLY",
        spot=22025, today=date(2026, 11, 6),
    )
    assert result.expiry == "2026-11-12"
    assert len(result.strikes) == 7
    assert len(result.contracts) == 14
    assert {c.option_type for c in result.contracts} == {"CE", "PE"}
    assert len(set(result.tokens)) == 14
    assert {c.symbol for c in result.contracts} == {row["symbol"] for row in master_rows() if row["expiry"] == "12Nov2026" and row["strike"] in {str(s * 100) for s in result.strikes}}


def test_banknifty_monthly_selects_last_expiry_in_nearest_available_month():
    rows = master_rows("BANKNIFTY", ("10Nov2026", "24Nov2026", "29Dec2026"))
    result = resolve_option_universe(
        rows, underlying="BANKNIFTY", expiry_type="MONTHLY",
        spot=22000, today=date(2026, 11, 6),
    )
    assert result.expiry == "2026-11-24"
    assert len(result.contracts) == 14


def test_universe_fails_closed_when_any_selected_ce_pe_is_missing():
    rows = master_rows()
    rows = [row for row in rows if not (row["strike"] == "2200000" and row["symbol"].endswith("PE"))]
    with pytest.raises(LookupError, match="incomplete CE/PE"):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


def test_universe_rejects_wrong_configured_expiry_type():
    with pytest.raises(ValueError, match="configured for WEEKLY"):
        resolve_option_universe(
            master_rows(), underlying="NIFTY", expiry_type="MONTHLY",
            spot=22000, today=date(2026, 11, 6),
        )
