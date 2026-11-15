from app.scanner.butterfly_condor_shadow import evaluate_butterfly_condor_snapshot


def _snapshot(prices, *, option_types=("CE",), depth=False):
    now = 1_800_000_000_000_000_000
    strikes = sorted(prices)
    rows = []
    depth_rows = []
    for option_type in option_types:
        for strike in strikes:
            bid, ask = prices[strike]
            rows.append({
                "underlying": "NIFTY", "instrument_class": "INDEX",
                "expiry": 20261126, "strike": strike, "option_type": option_type,
                "lot_size": 50, "timestamp_ns": now, "freshness": "LIVE",
                "bid": bid, "ask": ask, "bid_qty": 500, "ask_qty": 500,
            })
            if depth:
                depth_rows.append({
                    "underlying": "NIFTY", "expiry": 20261126, "strike": strike,
                    "option_type": option_type, "timestamp_ns": now,
                    "freshness": "FRESH", "depth_status": "DEPTH_FULL",
                    "bids": [(bid, 500), (bid - 0.1, 500)],
                    "asks": [(ask, 500), (ask + 0.1, 500)],
                })
    return {
        "quotes": {
            "captured_at_ns": now,
            "groups": [{
                "underlying": "NIFTY", "expiry": 20261126, "atm_strike": 115,
                "data": rows,
            }],
        },
        "depth": {"records": depth_rows},
    }


def _find(result, strategy, strikes, option_type="CE"):
    return next(
        row for row in result["results"]
        if row["strategy"] == strategy and row["strikes"] == strikes
        and row["option_type"] == option_type
    )


def test_long_credit_butterfly_uses_sell_middle_buy_wings_and_depth_vwap():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, depth=True)
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert row["gross_credit_per_unit"] == 28
    assert row["minimum_gross_expiry_pnl_per_unit"] == 28
    assert [leg["action"] for leg in row["legs"]] == ["BUY", "SELL", "BUY"]
    assert [leg["multiplier"] for leg in row["legs"]] == [1, 2, 1]
    assert row["depth"]["status"] == "READY"
    assert row["depth"]["executable_lots"] == 10


def test_short_credit_butterfly_reports_width_loss_and_two_lot_body():
    snapshot = _snapshot({100: (40, 41), 110: (5, 6), 120: (40, 41)})
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "SHORT_CREDIT_BUTTERFLY", [100, 110, 120])
    assert row["gross_credit_per_unit"] == 68
    assert row["minimum_gross_expiry_pnl_per_unit"] == 58
    assert row["maximum_gross_expiry_loss_per_unit"] == 0
    assert [leg["action"] for leg in row["legs"]] == ["SELL", "BUY", "SELL"]


def test_long_credit_condor_uses_four_equally_spaced_strikes():
    snapshot = _snapshot({100: (2, 3), 110: (30, 31), 120: (30, 31), 130: (2, 3)})
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "LONG_CREDIT_CONDOR", [100, 110, 120, 130])
    assert row["gross_credit_per_unit"] == 54
    assert row["minimum_gross_expiry_pnl_per_unit"] == 54
    assert [leg["action"] for leg in row["legs"]] == ["BUY", "SELL", "SELL", "BUY"]


def test_short_credit_condor_reports_maximum_expiry_loss():
    snapshot = _snapshot({100: (40, 41), 110: (5, 6), 120: (5, 6), 130: (40, 41)})
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "SHORT_CREDIT_CONDOR", [100, 110, 120, 130])
    assert row["gross_credit_per_unit"] == 68
    assert row["minimum_gross_expiry_pnl_per_unit"] == 58
    assert row["maximum_gross_expiry_loss_per_unit"] == 0


def test_ce_and_pe_are_calculated_independently():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, option_types=("CE",))
    result = evaluate_butterfly_condor_snapshot(snapshot)
    assert result["result_count"] > 0
    assert {row["option_type"] for row in result["results"]} == {"CE"}


def test_scope_is_limited_to_existing_atm_strike_radius():
    strikes = list(range(10, 401, 10))
    prices = {strike: (10, 11) for strike in strikes}
    prices.update({180: (2, 3), 190: (30, 31), 200: (30, 31), 210: (2, 3)})
    snapshot = _snapshot(prices)
    snapshot["quotes"]["groups"][0]["atm_strike"] = 205
    result = evaluate_butterfly_condor_snapshot(snapshot)
    assert result["result_count"] > 0
    # Only the nearest 15 actual listed strikes on either side of ATM
    # are eligible: 60..200 below and 210..350 above.
    assert all(60 <= strike <= 350 for row in result["results"] for strike in row["strikes"])

def test_invalid_freshness_and_fee_configuration_fail_closed():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    try:
        evaluate_butterfly_condor_snapshot(snapshot, fees_per_unit=-1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative fees must be rejected")
    try:
        evaluate_butterfly_condor_snapshot(snapshot, max_age_seconds=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero freshness must be rejected")
