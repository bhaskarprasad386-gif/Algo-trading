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
                "underlying": "NIFTY", "expiry": 20261126, "expiry_type": "WEEKLY",
                "atm_strike": 115, "data": rows,
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
    assert "estimated_fees_per_unit" not in row
    assert "estimated_net_credit_per_unit" not in row
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

def test_invalid_freshness_configuration_fails_closed():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    try:
        evaluate_butterfly_condor_snapshot(snapshot, max_age_seconds=0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero freshness must be rejected")



def test_expiry_and_index_eligibility_matrix():
    prices = {100: (5, 6), 110: (20, 21), 120: (5, 6)}
    cases = [
        ("NIFTY", "WEEKLY", True),
        ("NIFTY", "MONTHLY", True),
        ("SENSEX", "WEEKLY", True),
        ("SENSEX", "MONTHLY", True),
        ("BANKNIFTY", "MONTHLY", True),
        ("BANKNIFTY", "WEEKLY", False),
        ("FINNIFTY", "WEEKLY", False),
        ("BANKEX", "MONTHLY", False),
        ("NIFTY", "UNKNOWN", False),
    ]
    for symbol, expiry_type, expected in cases:
        snapshot = _snapshot(prices)
        group = snapshot["quotes"]["groups"][0]
        group["underlying"] = symbol
        group["expiry_type"] = expiry_type
        result = evaluate_butterfly_condor_snapshot(snapshot)
        assert bool(result["results"]) is expected, (symbol, expiry_type, result)


def test_minimum_gross_expiry_profit_threshold_is_strictly_greater_than_five():
    # Long butterfly credit is 2*10 - 7 - 8 = 5 exactly; reject it.
    exact = _snapshot({100: (6, 7), 110: (10, 11), 120: (7, 8)})
    exact_result = evaluate_butterfly_condor_snapshot(exact)
    assert not any(
        row["strategy"] == "LONG_CREDIT_BUTTERFLY"
        and row["strikes"] == [100, 110, 120]
        for row in exact_result["results"]
    )

    above = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    above_result = evaluate_butterfly_condor_snapshot(above)
    row = _find(above_result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert row["minimum_gross_expiry_pnl_per_unit"] > 5
    assert row["expiry_type"] == "WEEKLY"


def test_negative_minimum_profit_threshold_is_rejected():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    try:
        evaluate_butterfly_condor_snapshot(
            snapshot, min_gross_expiry_profit_per_unit=-0.01
        )
    except ValueError:
        pass
    else:
        raise AssertionError("negative gross expiry threshold must be rejected")



def test_expiry_type_missing_or_unknown_fails_closed():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    group = snapshot["quotes"]["groups"][0]
    group.pop("expiry_type")
    result = evaluate_butterfly_condor_snapshot(
        snapshot, expiry_type_resolver=lambda _symbol, _expiry: "UNKNOWN"
    )
    assert result["result_count"] == 0
    assert result["rejected_counts"]["INELIGIBLE_INDEX_OR_EXPIRY"] == 1


def test_nanosecond_timestamp_integer_precision_is_preserved():
    from app.scanner.butterfly_condor_shadow import _whole

    timestamp = 1_800_000_000_000_000_123
    assert _whole(timestamp) == timestamp

def test_depth_rejects_provider_timestamp_older_than_freshness_window():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, depth=True)
    for row in snapshot["depth"]["records"]:
        row["timestamp_ns"] -= 6_000_000_000
    result = evaluate_butterfly_condor_snapshot(
        snapshot, max_age_seconds=5, max_leg_skew_seconds=10
    )
    butterfly = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert butterfly["depth"]["status"] == "INCOMPLETE"
    assert butterfly["depth"]["reason"] == "STALE_DEPTH_TIMESTAMP"


def test_depth_rejects_crossed_book_even_when_marked_fresh():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, depth=True)
    snapshot["depth"]["records"][0]["bids"] = [(30, 500)]
    snapshot["depth"]["records"][0]["asks"] = [(20, 500)]
    result = evaluate_butterfly_condor_snapshot(snapshot)
    butterfly = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert butterfly["depth"]["status"] == "INCOMPLETE"
    assert butterfly["depth"]["reason"] == "CROSSED_DEPTH_BOOK"


def test_depth_rejects_unsorted_or_invalid_level_ladders():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, depth=True)
    snapshot["depth"]["records"][0]["bids"] = [(4, 500), (5, 500)]
    result = evaluate_butterfly_condor_snapshot(snapshot)
    butterfly = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert butterfly["depth"]["status"] == "INCOMPLETE"
    assert butterfly["depth"]["reason"] == "INVALID_DEPTH_LEVELS"

    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, depth=True)
    snapshot["depth"]["records"][0]["asks"] = [(6, 0)]
    result = evaluate_butterfly_condor_snapshot(snapshot)
    butterfly = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert butterfly["depth"]["status"] == "INCOMPLETE"
    assert butterfly["depth"]["reason"] == "INVALID_DEPTH_LEVELS"


def test_quote_diagnostics_separate_stale_and_invalid_records():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    rows = snapshot["quotes"]["groups"][0]["data"]
    rows[0]["timestamp_ns"] -= 6_000_000_000
    rows[1]["bid"] = -1
    result = evaluate_butterfly_condor_snapshot(snapshot)
    assert result["rejected_counts"]["STALE_QUOTE"] == 1
    assert result["rejected_counts"]["INVALID_QUOTE"] == 1
    assert result["rejected_counts"]["STALE_OR_INVALID_QUOTE"] == 2
    assert result["diagnostics"]["valid_quotes"] == 1


def test_cross_leg_quote_timestamp_skew_is_reported():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)})
    snapshot["quotes"]["groups"][0]["data"][0]["timestamp_ns"] -= 3_000_000_000
    result = evaluate_butterfly_condor_snapshot(snapshot, max_leg_skew_seconds=2)
    assert result["result_count"] == 0
    assert result["diagnostics"]["cross_leg_quote_timestamp_skew"] > 0


def test_depth_complete_and_incomplete_counts_are_exposed():
    snapshot = _snapshot({100: (5, 6), 110: (20, 21), 120: (5, 6)}, depth=True)
    result = evaluate_butterfly_condor_snapshot(snapshot)
    assert result["depth_status"]["complete"] > 0
    assert result["depth_status"]["incomplete"] == 0
    assert result["diagnostics"]["depth_ready"] == result["depth_status"]["complete"]

    snapshot["depth"]["records"] = []
    result = evaluate_butterfly_condor_snapshot(snapshot)
    assert result["depth_status"]["complete"] == 0
    assert result["depth_status"]["incomplete"] > 0
    assert result["depth_status"]["incomplete_reasons"]["MISSING_OR_STALE_DEPTH"] > 0


def test_live_coverage_reports_quote_to_depth_coverage_by_option_side():
    snapshot = _snapshot(
        {100: (5, 6), 110: (20, 21), 120: (5, 6)},
        option_types=("CE",), depth=True,
    )
    result = evaluate_butterfly_condor_snapshot(snapshot)
    ce = next(row for row in result["live_coverage"] if row["option_type"] == "CE")
    pe = next(row for row in result["live_coverage"] if row["option_type"] == "PE")
    assert ce["listed_quote_rows"] == 3
    assert ce["valid_quote_count"] == 3
    assert ce["candidate_structure_count"] > 0
    assert ce["result_count"] > 0
    assert ce["depth_ready_count"] == ce["result_count"]
    assert pe["listed_quote_rows"] == 0
    assert pe["valid_quote_count"] == 0
    assert pe["result_count"] == 0


def test_l1_and_depth_vwap_comparison_uses_common_executable_lots():
    snapshot = _snapshot(
        {100: (5, 6), 110: (20, 21), 120: (5, 6)},
        depth=True,
    )
    for row in snapshot["quotes"]["groups"][0]["data"]:
        row["bid_qty"] = 1000
        row["ask_qty"] = 1000
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    comparison = row["l1_vs_depth_vwap"]
    assert comparison["status"] == "COMPARABLE"
    assert comparison["executable_lots"] == 10
    assert comparison["l1_top_of_book_credit_per_unit"] == 28
    assert abs(comparison["depth_vwap_credit_per_unit"] - 27.9) < 1e-9
    assert abs(comparison["l1_minus_depth_vwap_credit_per_unit"] - 0.1) < 1e-9


def test_l1_depth_comparison_is_not_comparable_when_l1_size_is_smaller():
    snapshot = _snapshot(
        {100: (5, 6), 110: (20, 21), 120: (5, 6)},
        depth=True,
    )
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert row["l1_vs_depth_vwap"]["status"] == "L1_CAPACITY_BELOW_DEPTH"
    assert row["l1_vs_depth_vwap"]["l1_executable_lots"] == 5
    assert row["l1_vs_depth_vwap"]["depth_executable_lots"] == 10



def test_burst_evaluation_is_deterministic_and_does_not_mutate_shared_snapshot():
    from copy import deepcopy

    base = _snapshot(
        {100: (5, 6), 110: (20, 21), 120: (5, 6)},
        option_types=("CE", "PE"),
    )
    # Simulate a busy existing Box Spread snapshot with many independent
    # underlying/expiry groups while keeping the same selected-contract shape.
    groups = []
    for index in range(24):
        group = deepcopy(base["quotes"]["groups"][0])
        group["expiry"] = 20261126 + index
        groups.append(group)
    snapshot = {
        "quotes": {"captured_at_ns": base["quotes"]["captured_at_ns"], "groups": groups},
        "depth": {"records": []},
    }
    before = deepcopy(snapshot)

    first = evaluate_butterfly_condor_snapshot(snapshot)
    second = evaluate_butterfly_condor_snapshot(snapshot)

    assert snapshot == before
    assert first["result_count"] == second["result_count"]
    assert first["diagnostics"] == second["diagnostics"]
    assert first["live_coverage"] == second["live_coverage"]
    assert first["result_count"] > 0


def test_parallel_shadow_evaluations_are_isolated_under_concurrent_load():
    from concurrent.futures import ThreadPoolExecutor

    snapshot = _snapshot(
        {100: (5, 6), 110: (20, 21), 120: (5, 6)},
        option_types=("CE", "PE"),
        depth=True,
    )
    expected = evaluate_butterfly_condor_snapshot(snapshot)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda _index: evaluate_butterfly_condor_snapshot(snapshot),
            range(32),
        ))

    assert len(results) == 32
    assert all(result["result_count"] == expected["result_count"] for result in results)
    assert all(result["diagnostics"] == expected["diagnostics"] for result in results)
    assert all(result["live_coverage"] == expected["live_coverage"] for result in results)
    assert all(result["results"] == expected["results"] for result in results)


def test_malformed_depth_expiry_rows_are_ignored_without_aborting_evaluation():
    snapshot = _snapshot(
        {100: (5, 6), 110: (20, 21), 120: (5, 6)},
        depth=True,
    )
    snapshot["depth"]["records"].append({
        "underlying": "NIFTY",
        "expiry": "not-an-expiry",
        "strike": 100,
        "option_type": "CE",
        "timestamp_ns": "bad",
        "bids": [],
        "asks": [],
    })
    result = evaluate_butterfly_condor_snapshot(snapshot)
    row = _find(result, "LONG_CREDIT_BUTTERFLY", [100, 110, 120])
    assert row["depth"]["status"] == "READY"
