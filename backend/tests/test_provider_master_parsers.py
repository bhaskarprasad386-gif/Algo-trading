import json
from datetime import date
from decimal import Decimal

import pytest

from app.market_data.provider_master_parsers import AngelOneMasterParser, ShoonyaMasterParser


def test_angel_one_parser_normalizes_scaled_option_strike_and_hashes_source():
    rows = [{
        "exch_seg": "NFO", "token": "1001", "symbol": "NIFTY31DEC26C25000",
        "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "31DEC2026",
        "strike": "2500000", "lotsize": "65", "tick_size": "0.05",
        "optiontype": "CE",
    }]
    raw = json.dumps(rows, separators=(",", ":"))
    report = AngelOneMasterParser.parse(raw)
    assert report.accepted_rows == 1
    assert report.rejected_rows == 0
    item = report.mappings[0]
    assert item.key.expiry == date(2026, 12, 31)
    assert item.key.strike == Decimal("25000")
    assert item.key.option_type == "CE"
    assert item.provider.token == "1001"
    assert len(report.source_sha256) == 64


def test_shoonya_pipe_master_uses_trading_symbol_and_explicit_strike_scale():
    raw = (
        "Exchange|Token|LotSize|Symbol|TradingSymbol|Instrument|OptionType|"
        "StrikePrice|TickSize|Expiry\n"
        "NFO|AbC123|65|NIFTY|NIFTY31DEC26C25000|OPTIDX|CE|2500000|0.05|31-Dec-2026\n"
    )
    report = ShoonyaMasterParser.parse(raw, delimiter="|", strike_scale=Decimal("100"))
    assert report.accepted_rows == 1
    item = report.mappings[0]
    assert item.trading_symbol == "NIFTY31DEC26C25000"
    assert item.key.underlying == "NIFTY"
    assert item.key.strike == Decimal("25000")
    assert item.provider.token == "AbC123"


def test_shoonya_strike_scale_is_explicit_and_not_guessed():
    raw = (
        "Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,OptionType,"
        "StrikePrice,TickSize,Expiry\n"
        "NFO,101,65,NIFTY,NIFTY31DEC26C25000,OPTIDX,CE,2500000,0.05,31-Dec-2026\n"
    )
    report = ShoonyaMasterParser.parse(raw)
    assert report.accepted_rows == 0
    assert report.rejected_rows == 1
    assert "invalid strike" in report.quarantined[0].reason


def test_parser_quarantines_bad_rows_without_silently_accepting_them():
    rows = [
        {"exch_seg": "NFO", "token": "101", "symbol": "NIFTY31DEC26C25000",
         "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "31DEC2026",
         "strike": "2500000", "lotsize": "65", "tick_size": "0.05", "optiontype": "CE"},
        {"exch_seg": "NFO", "token": "101", "symbol": "NIFTY31DEC26P25000",
         "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "31DEC2026",
         "strike": "2500000", "lotsize": "65", "tick_size": "0.05", "optiontype": "PE"},
        {"exch_seg": "NFO", "token": "103", "symbol": "BROKEN"},
    ]
    report = AngelOneMasterParser.parse(rows)
    assert report.total_rows == 3
    assert report.accepted_rows == 1
    assert report.rejected_rows == 2
    assert "duplicate broker token" in report.quarantined[0].reason


def test_master_parser_rejects_non_array_angel_payload_and_bad_delimiter():
    with pytest.raises(ValueError, match="JSON array"):
        AngelOneMasterParser.parse('{"token":"1"}')
    with pytest.raises(ValueError, match="delimiter"):
        ShoonyaMasterParser.parse("a;b\n1;2", delimiter=";")
