import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market_data.provider_contracts import ProviderContractRegistry
from app.market_data.provider_master_parsers import (
    AngelOneMasterParser, ShoonyaMasterParser, publish_dual_master,
)


def _angel_rows(token="A-101", tick="5"):
    return [{
        "exch_seg": "NFO", "token": token, "symbol": "NIFTY31DEC2625000CE",
        "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "31DEC2026",
        "strike": "2500000", "lotsize": "65", "tick_size": tick,
    }]


def _shoonya_text(token="S-202", tick="0.05"):
    return (
        "Exchange|Token|LotSize|Symbol|TradingSymbol|Instrument|OptionType|"
        "StrikePrice|TickSize|Expiry\n"
        f"NFO|{token}|65|NIFTY|NIFTY31DEC2625000CE|OPTIDX|CE|25000|{tick}|31-Dec-2026\n"
    )


def test_angel_parser_matches_published_master_option_shape_and_paise_scaling():
    raw = json.dumps(_angel_rows(), separators=(",", ":"))
    report = AngelOneMasterParser.parse(raw)
    assert report.accepted_rows == 1
    item = report.mappings[0]
    assert item.key.expiry == date(2026, 12, 31)
    assert item.key.strike == Decimal("25000")
    assert item.key.option_type == "CE"
    assert item.tick_size == Decimal("0.05")
    assert item.provider.token == "A-101"
    assert len(report.source_sha256) == 64


def test_shoonya_pipe_master_preserves_case_sensitive_token_and_explicit_scale():
    report = ShoonyaMasterParser.parse(_shoonya_text(), delimiter="|")
    assert report.accepted_rows == 1
    item = report.mappings[0]
    assert item.trading_symbol == "NIFTY31DEC2625000CE"
    assert item.key.underlying == "NIFTY"
    assert item.key.strike == Decimal("25000")
    assert item.provider.token == "S-202"


def test_shoonya_strike_scale_is_explicit_and_not_guessed():
    text = _shoonya_text().replace("|25000|", "|2500000|")
    report = ShoonyaMasterParser.parse(text, delimiter="|")
    assert report.mappings[0].key.strike == Decimal("2500000")
    report_scaled = ShoonyaMasterParser.parse(
        text, delimiter="|", strike_scale=Decimal("100")
    )
    assert report_scaled.mappings[0].key.strike == Decimal("25000")


def test_parser_quarantines_duplicate_provider_token_and_bad_rows():
    rows = _angel_rows()
    rows.append({**_angel_rows()[0], "symbol": "NIFTY31DEC2625000PE"})
    rows.append({"exch_seg": "NFO", "token": "103", "symbol": "BROKEN"})
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


def test_dual_publish_is_atomic_and_keeps_broker_tokens_separate():
    angel = AngelOneMasterParser.parse(_angel_rows())
    shoonya = ShoonyaMasterParser.parse(_shoonya_text(), delimiter="|")
    registry = ProviderContractRegistry()
    published = publish_dual_master(
        registry, [angel, shoonya], source_version="fixture-1",
        fetched_at=datetime.now(timezone.utc),
    )
    key = angel.mappings[0].key
    assert registry.resolve(key, "ANGELONE").provider.token == "A-101"
    assert registry.resolve(key, "SHOONYA").provider.token == "S-202"
    assert published.source_sha256 != angel.source_sha256


def test_dual_publish_rejection_threshold_and_missing_broker_preserve_last_good():
    angel = AngelOneMasterParser.parse(_angel_rows())
    shoonya = ShoonyaMasterParser.parse(_shoonya_text(), delimiter="|")
    registry = ProviderContractRegistry()
    original = publish_dual_master(
        registry, [angel, shoonya], source_version="fixture-1",
        fetched_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="exactly one Angel One"):
        publish_dual_master(
            registry, [angel], source_version="fixture-2",
            fetched_at=original.fetched_at + timedelta(seconds=1),
        )
    rejected = AngelOneMasterParser.parse(_angel_rows() + [{"bad": "row"}])
    with pytest.raises(ValueError, match="rejection ratio"):
        publish_dual_master(
            registry, [rejected, shoonya], source_version="fixture-3",
            fetched_at=original.fetched_at + timedelta(seconds=1),
        )
    assert registry.snapshot() == original


def test_dual_publish_rejects_lot_tick_disagreement_without_swap():
    angel = AngelOneMasterParser.parse(_angel_rows())
    shoonya = ShoonyaMasterParser.parse(_shoonya_text(tick="0.10"), delimiter="|")
    registry = ProviderContractRegistry()
    with pytest.raises(ValueError, match="lot/tick metadata mismatch"):
        publish_dual_master(
            registry, [angel, shoonya], source_version="fixture-1",
            fetched_at=datetime.now(timezone.utc),
        )
    assert registry.snapshot() is None

from app.market_data.provider_master_audit import audit_master_coverage


def test_coverage_audit_reports_common_and_one_sided_contracts():
    angel_rows = _angel_rows()
    angel_rows.append({
        "exch_seg": "NFO", "token": "A-102", "symbol": "NIFTY31DEC2625000PE",
        "name": "NIFTY", "instrumenttype": "OPTIDX", "expiry": "31DEC2026",
        "strike": "2500000", "lotsize": "65", "tick_size": "5",
    })
    shoonya_text = _shoonya_text()
    angel = AngelOneMasterParser.parse(angel_rows)
    shoonya = ShoonyaMasterParser.parse(shoonya_text, delimiter="|")
    audit = audit_master_coverage([angel, shoonya])
    assert audit.angel_contracts == 2
    assert audit.shoonya_contracts == 1
    assert audit.common_contracts == 1
    assert len(audit.angel_only) == 1
    assert not audit.metadata_mismatches


def test_coverage_audit_surfaces_lot_and_tick_metadata_mismatch():
    angel = AngelOneMasterParser.parse(_angel_rows())
    shoonya = ShoonyaMasterParser.parse(
        _shoonya_text(tick="0.10"), delimiter="|"
    )
    audit = audit_master_coverage([angel, shoonya])
    assert audit.metadata_mismatches == (angel.mappings[0].key,)


def test_coverage_audit_requires_both_brokers():
    with pytest.raises(ValueError, match="one Angel One and one Shoonya"):
        audit_master_coverage([AngelOneMasterParser.parse(_angel_rows())])


@pytest.mark.parametrize(
    ("symbol_alias", "instrument", "expected"),
    [
        ("SENSEXOPT", "OPTIDX", "SENSEX"),
        ("BANKEXOPT", "OPTIDX", "BANKEX"),
        ("SENSEXFUT", "FUTIDX", "SENSEX"),
        ("BANKEXFUT", "FUTIDX", "BANKEX"),
    ],
)
def test_shoonya_bfo_symbol_alias_normalizes_to_underlying(
    symbol_alias, instrument, expected,
):
    text = (
        "Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,OptionType,"
        "StrikePrice,TickSize,Expiry\n"
        f"BFO,9001,20,{symbol_alias},{expected}26NOV2026FUT,"
        f"{instrument},CE,0,0.05,26-Nov-2026\n"
    )
    if instrument == "OPTIDX":
        text = text.replace(",0,0.05,", ",100,0.05,")
    report = ShoonyaMasterParser.parse(text, delimiter=",")
    assert report.accepted_rows == 1
    assert report.mappings[0].key.underlying == expected
