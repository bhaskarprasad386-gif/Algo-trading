from datetime import date, datetime, timezone
import hashlib
import json
from types import SimpleNamespace

from app.scanner.iv_skew_flip.basket_registry import summarize_basket_registry


def record(underlying="NIFTY", effective="2026-11-01", *, id=1, symbols=("AAA", "BBB")):
    components = [{"symbol": symbol, "weight": 100 / len(symbols)} for symbol in symbols]
    tokens = {symbol: str(index + 1) for index, symbol in enumerate(symbols)}
    digest = hashlib.sha256(b"official file").hexdigest()
    return SimpleNamespace(
        id=id,
        underlying=underlying,
        effective_date=datetime.fromisoformat(effective).replace(tzinfo=timezone.utc),
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        status="VALID",
        source_name="Official weights",
        source_url="https://www.niftyindices.com/weights.csv",
        source_sha256=digest,
        roster_source_name="Official roster",
        roster_source_url="https://www.niftyindices.com/roster.csv",
        roster_sha256=digest,
        components_json=json.dumps(components),
        token_mapping_json=json.dumps(tokens),
        weight_sum=100.0,
    )


def test_registry_reports_missing_baskets_fail_closed():
    result = summarize_basket_registry([], as_of=date(2026, 11, 8))
    assert result["baskets"]["NIFTY"]["status"] == "MISSING"
    assert result["baskets"]["BANKNIFTY"]["status"] == "MISSING"
    assert result["signals_allowed"] is False
    assert result["activation_enabled"] is False


def test_registry_reports_fresh_independent_index_baskets_without_activation():
    result = summarize_basket_registry(
        [record("NIFTY"), record("BANKNIFTY", id=2)],
        as_of=date(2026, 11, 8),
    )
    assert result["baskets"]["NIFTY"]["status"] == "VERIFIED_FRESH"
    assert result["baskets"]["BANKNIFTY"]["status"] == "VERIFIED_FRESH"
    assert result["baskets"]["NIFTY"]["constituent_count"] == 2
    assert result["signals_allowed"] is False
    assert result["activation_enabled"] is False


def test_registry_marks_stale_or_future_baskets_unusable():
    result = summarize_basket_registry(
        [record("NIFTY", "2026-09-01"), record("BANKNIFTY", "2026-11-09", id=2)],
        as_of=date(2026, 11, 8),
    )
    assert result["baskets"]["NIFTY"]["status"] == "STALE"
    assert result["baskets"]["BANKNIFTY"]["status"] == "FUTURE_DATED"


def test_registry_uses_latest_version_and_revalidates_persisted_payload():
    older = record("NIFTY", "2026-10-01", id=1)
    newer = record("NIFTY", "2026-11-01", id=2)
    newer.token_mapping_json = json.dumps({"AAA": "1"})
    result = summarize_basket_registry([older, newer], as_of=date(2026, 11, 8))
    assert result["baskets"]["NIFTY"]["record_id"] == 2
    assert result["baskets"]["NIFTY"]["status"] == "INCOMPLETE_TOKEN_MAPPING"


def test_registry_rejects_unofficial_source_even_if_record_claims_valid():
    item = record()
    item.source_url = "https://niftyindices.com.attacker.example/weights.csv"
    result = summarize_basket_registry([item], as_of=date(2026, 11, 8))
    assert result["baskets"]["NIFTY"]["status"] == "INVALID_SOURCE_PROVENANCE"
