from datetime import date, datetime
import io
import zipfile

import pytest

from app.scanner.iv_skew_flip.weight_import import (
    parse_official_weight_basket,
    validate_basket_token_mapping,
)


CSV = b"symbol,weight\nAAA,60.25\nBBB,39.75\n"
EXPECTED = ("AAA", "BBB")
BASE = {
    "filename": "weights.csv",
    "underlying": "NIFTY",
    "effective_date": "2026-11-01",
    "source_name": "Official index weights",
    "source_url": "https://www.niftyindices.com/reports/monthly",
    "expected_symbols": EXPECTED,
    "as_of": date(2026, 11, 8),
}


def test_imports_complete_official_csv_and_records_provenance():
    result = parse_official_weight_basket(CSV, **BASE)
    assert result.underlying == "NIFTY"
    assert result.effective_date == date(2026, 11, 1)
    assert result.file_format == "CSV"
    assert result.weight_sum == pytest.approx(100.0)
    assert len(result.sha256) == 64
    assert [item.symbol for item in result.components] == ["AAA", "BBB"]


def test_import_accepts_case_insensitive_headers():
    raw = b" Symbol , WEIGHT \nAAA,60\nBBB,40\n"
    result = parse_official_weight_basket(raw, **BASE)
    assert result.weight_sum == pytest.approx(100.0)


@pytest.mark.parametrize(
    "raw,expected_error",
    [
        (b"symbol,weight\nAAA,100\n", "roster mismatch"),
        (b"symbol,weight\nAAA,50\nBBB,40\n", "weight sum"),
        (b"symbol,weight\nAAA,50\nAAA,50\n", "duplicate"),
        (b"symbol,weight\nAAA,nan\nBBB,100\n", "finite and positive"),
        (b"symbol,weight\nAAA,0\nBBB,100\n", "finite and positive"),
        (b"symbol,weight\nAAA,inf\nBBB,100\n", "finite and positive"),
        (b"symbol,weight,extra\nAAA,50,x\nBBB,50,y\n", "exactly"),
    ],
)
def test_rejects_incomplete_duplicate_invalid_or_malformed_csv(raw, expected_error):
    with pytest.raises(ValueError, match=expected_error):
        parse_official_weight_basket(raw, **BASE)


def test_rejects_unknown_symbols_even_when_row_count_matches():
    raw = b"symbol,weight\nAAA,50\nCCC,50\n"
    with pytest.raises(ValueError, match="roster mismatch"):
        parse_official_weight_basket(raw, **BASE)


@pytest.mark.parametrize(
    "effective_date,as_of,error",
    [
        ("2026-09-01", date(2026, 11, 8), "stale"),
        ("2026-11-09", date(2026, 11, 8), "future-dated"),
        ("not-a-date", date(2026, 11, 8), "ISO date"),
    ],
)
def test_rejects_stale_future_or_invalid_effective_date(effective_date, as_of, error):
    args = {**BASE, "effective_date": effective_date, "as_of": as_of}
    with pytest.raises(ValueError, match=error):
        parse_official_weight_basket(CSV, **args)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.niftyindices.com/file.csv",
        "https://example.com/file.csv",
        "https://niftyindices.com.attacker.example/file.csv",
        "https://niftyindices.com@attacker.example/file.csv",
        "https://niftyindices.com:8443/file.csv",
    ],
)
def test_rejects_non_official_or_ambiguous_source_provenance(url):
    with pytest.raises(ValueError, match="official NSE HTTPS"):
        parse_official_weight_basket(CSV, **{**BASE, "source_url": url})


def test_zip_import_requires_exactly_one_csv_and_is_hash_of_original_upload():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("weights.csv", CSV)
    raw = buffer.getvalue()
    result = parse_official_weight_basket(raw, **{**BASE, "filename": "weights.zip"})
    assert result.file_format == "ZIP"
    assert len(result.sha256) == 64

    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("weights.csv", CSV)
        archive.writestr("other.csv", CSV)
    with pytest.raises(ValueError, match="exactly one CSV"):
        parse_official_weight_basket(bad.getvalue(), **{**BASE, "filename": "weights.zip"})


def test_rejects_bad_zip_and_unsupported_file_type():
    with pytest.raises(ValueError, match="invalid ZIP"):
        parse_official_weight_basket(b"not a zip", **{**BASE, "filename": "weights.zip"})
    with pytest.raises(ValueError, match="only CSV or ZIP"):
        parse_official_weight_basket(CSV, **{**BASE, "filename": "weights.xlsx"})


def test_token_mapping_must_be_complete_and_unique():
    basket = parse_official_weight_basket(CSV, **BASE)
    assert validate_basket_token_mapping(basket, {"AAA": "101", "BBB": "202"}) == {
        "AAA": "101", "BBB": "202"
    }
    with pytest.raises(ValueError, match="missing NSE cash"):
        validate_basket_token_mapping(basket, {"AAA": "101"})
    with pytest.raises(ValueError, match="duplicate NSE cash"):
        validate_basket_token_mapping(basket, {"AAA": "101", "BBB": "101"})


@pytest.mark.parametrize("invalid_token", [True, False, 0, -1, "0", "  ", "token-101", "1.5"])
def test_token_mapping_rejects_non_numeric_or_non_positive_tokens(invalid_token):
    basket_result = parse_official_weight_basket(CSV, **BASE)
    with pytest.raises(ValueError, match="invalid NSE cash instrument token"):
        validate_basket_token_mapping(basket_result, {"AAA": invalid_token, "BBB": "202"})


def test_token_mapping_rejects_unexpected_extra_symbols():
    basket_result = parse_official_weight_basket(CSV, **BASE)
    with pytest.raises(ValueError, match="unexpected symbols"):
        validate_basket_token_mapping(
            basket_result, {"AAA": "101", "BBB": "202", "CCC": "303"}
        )



@pytest.mark.parametrize(
    "raw",
    [
        b"symbol,weight\nAAA,60\nBBB\n",
        b"symbol,weight\nAAA,60,unexpected\nBBB,40\n",
    ],
)
def test_rejects_short_or_extra_csv_row_fields(raw):
    with pytest.raises(ValueError, match="malformed CSV row"):
        parse_official_weight_basket(raw, **BASE)


def test_token_mapping_normalizes_symbol_keys_and_rejects_normalization_collisions():
    basket_result = parse_official_weight_basket(CSV, **BASE)
    assert validate_basket_token_mapping(basket_result, {" aaa ": "101", "bbb": "202"}) == {
        "AAA": "101", "BBB": "202"
    }
    with pytest.raises(ValueError, match="duplicate normalized symbol"):
        validate_basket_token_mapping(
            basket_result, {"AAA": "101", " aaa ": "202", "BBB": "303"}
        )


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"as_of": datetime(2026, 11, 8, 12, 0)}, "as_of must be a calendar date"),
        ({"effective_date": datetime(2026, 11, 1, 12, 0)}, "effective_date must be an ISO date"),
        ({"expected_symbols": None}, "authoritative expected constituent roster"),
        ({"source_name": 123}, "source name and HTTPS"),
        ({"source_url": None}, "source name and HTTPS"),
        ({"filename": ""}, "upload filename"),
    ],
)
def test_parser_rejects_ambiguous_or_malformed_metadata(overrides, error):
    with pytest.raises(ValueError, match=error):
        parse_official_weight_basket(CSV, **{**BASE, **overrides})


def test_parser_rejects_non_bytes_upload_content():
    with pytest.raises(ValueError, match="upload content must be bytes"):
        parse_official_weight_basket("symbol,weight", **BASE)
