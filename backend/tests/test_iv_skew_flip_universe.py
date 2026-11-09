from datetime import date

import pytest

from app.scanner.iv_skew_flip.universe import resolve_option_universe


def master_rows(symbol="NIFTY", expiries=("12Nov2026", "19Nov2026")):
    rows = []
    for expiry_index, expiry in enumerate(expiries):
        for strike in range(21700, 22301, 50):
            for side in ("CE", "PE"):
                rows.append({
                    "exch_seg": "NFO", "instrumenttype": "OPTIDX", "name": symbol,
                    "expiry": expiry, "symbol": f"{symbol}{expiry}{strike}{side}",
                    "strike": str(strike * 100), "token": f"{expiry_index}-{strike}-{side}",
                    "lotsize": "75" if symbol == "NIFTY" else "30",
                })
    return rows


def test_nifty_weekly_resolves_nearest_expiry_and_eleven_strikes():
    result = resolve_option_universe(
        master_rows(), underlying="NIFTY", expiry_type="WEEKLY",
        spot=22025, today=date(2026, 11, 6), strike_count=11,
    )
    assert result.expiry == "2026-11-12"
    assert len(result.strikes) == 11
    assert result.atm_strike == 22050  # midpoint 22025 rounds to the upper listed strike
    assert result.strikes == (21800, 21850, 21900, 21950, 22000, 22050, 22100, 22150, 22200, 22250, 22300)
    assert len(result.contracts) == 22
    assert {c.option_type for c in result.contracts} == {"CE", "PE"}
    assert len(set(result.tokens)) == 22
    assert {c.symbol for c in result.contracts} == {row["symbol"] for row in master_rows() if row["expiry"] == "12Nov2026" and row["strike"] in {str(int(s * 100)) for s in result.strikes}}


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


def test_nifty_monthly_resolves_last_expiry_in_nearest_month():
    result = resolve_option_universe(
        master_rows(expiries=("12Nov2026", "26Nov2026", "31Dec2026")),
        underlying="NIFTY", expiry_type="MONTHLY",
        spot=22025, today=date(2026, 11, 6),
    )
    assert result.expiry == "2026-11-26"
    assert len(result.strikes) == 7
    assert len(result.contracts) == 14


def test_universe_rejects_wrong_configured_expiry_type():
    with pytest.raises(ValueError, match="configured for MONTHLY"):
        resolve_option_universe(
            master_rows("BANKNIFTY"), underlying="BANKNIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


def test_universe_rejects_duplicate_instrument_tokens():
    rows = master_rows()
    selected_ce = next(row for row in rows if row["expiry"] == "12Nov2026" and row["strike"] == "2200000" and row["symbol"].endswith("CE"))
    selected_pe = next(row for row in rows if row["expiry"] == "12Nov2026" and row["strike"] == "2200000" and row["symbol"].endswith("PE"))
    selected_pe["token"] = selected_ce["token"]
    with pytest.raises(LookupError, match="duplicate instrument token"):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


@pytest.mark.parametrize("invalid_strike", ["nan", "inf", "-inf", "not-a-number"])
def test_universe_ignores_non_finite_or_invalid_strikes(invalid_strike):
    rows = master_rows()
    selected = next(row for row in rows if row["expiry"] == "12Nov2026" and row["strike"] == "2200000" and row["symbol"].endswith("CE"))
    selected["strike"] = invalid_strike
    with pytest.raises(LookupError):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


@pytest.mark.parametrize("invalid_spot", [True, False, float("nan"), float("inf"), float("-inf"), 0, -1])
def test_universe_rejects_non_finite_or_non_positive_spot(invalid_spot):
    with pytest.raises(ValueError, match="finite positive live spot"):
        resolve_option_universe(
            master_rows(), underlying="NIFTY", expiry_type="WEEKLY",
            spot=invalid_spot, today=date(2026, 11, 6),
        )


def test_universe_rejects_boolean_lot_size():
    rows = master_rows()
    selected = next(row for row in rows if row["expiry"] == "12Nov2026" and row["strike"] == "2200000" and row["symbol"].endswith("CE"))
    selected["lotsize"] = True
    with pytest.raises(LookupError):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


def test_universe_ignores_boolean_strike():
    rows = master_rows()
    selected = next(row for row in rows if row["expiry"] == "12Nov2026" and row["strike"] == "2200000" and row["symbol"].endswith("CE"))
    selected["strike"] = True
    with pytest.raises(LookupError):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


def test_nifty_weekly_skips_monthly_expiry_and_uses_next_distinct_weekly():
    rows = master_rows(expiries=("26Nov2026", "03Dec2026", "10Dec2026", "31Dec2026"))
    weekly = resolve_option_universe(
        rows, underlying="NIFTY", expiry_type="WEEKLY",
        spot=22000, today=date(2026, 11, 20),
    )
    monthly = resolve_option_universe(
        rows, underlying="NIFTY", expiry_type="MONTHLY",
        spot=22000, today=date(2026, 11, 20),
    )
    assert monthly.expiry == "2026-11-26"
    assert weekly.expiry == "2026-12-03"
    assert weekly.expiry != monthly.expiry


def test_nifty_weekly_fails_closed_when_master_contains_only_monthly_expiries():
    rows = master_rows(expiries=("26Nov2026", "31Dec2026"))
    with pytest.raises(LookupError, match="no distinct weekly"):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 20),
        )



def test_universe_fails_closed_on_non_uniform_strike_ladder():
    rows = master_rows()
    # Remove a complete CE/PE strike pair: the remaining ladder has a gap.
    rows = [
        row for row in rows
        if not (row["expiry"] == "12Nov2026" and row["strike"] == "2205000")
    ]
    with pytest.raises(LookupError, match="not uniformly spaced"):
        resolve_option_universe(
            rows, underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6),
        )


@pytest.mark.parametrize("strike_count", [True, False, 7.0, "7", 2, 4])
def test_universe_rejects_invalid_strike_count(strike_count):
    with pytest.raises(ValueError, match="odd integer"):
        resolve_option_universe(
            master_rows(), underlying="NIFTY", expiry_type="WEEKLY",
            spot=22000, today=date(2026, 11, 6), strike_count=strike_count,
        )


def test_weekly_strike_window_is_exactly_centered_on_live_spot_atm():
    result = resolve_option_universe(
        master_rows(), underlying="NIFTY", expiry_type="WEEKLY",
        spot=22037, today=date(2026, 11, 6),
    )
    assert result.atm_strike == 22050
    assert result.strikes == (21900, 21950, 22000, 22050, 22100, 22150, 22200)


def test_weekly_universe_does_not_shift_atm_window_when_spot_is_near_ladder_edge():
    with pytest.raises(LookupError, match="cannot form ATM"):
        resolve_option_universe(
            master_rows(), underlying="NIFTY", expiry_type="WEEKLY",
            spot=21800, today=date(2026, 11, 6),
        )
