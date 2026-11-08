from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest

from app.scanner.iv_skew_flip import official_weight_pdf


def _archive(name="NIFTY_50_Oct2026.pdf"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, b"%PDF-test")
    return output.getvalue()


def _reader_with_text(monkeypatch, text):
    class FakePage:
        def extract_text(self, **_kwargs):
            return text

    class FakeReader:
        def __init__(self, *_args, **_kwargs):
            self.pages = [FakePage()]

    monkeypatch.setattr(official_weight_pdf, "PdfReader", FakeReader)


def _parse(monkeypatch, text, expected=("AAA", "BBB")):
    _reader_with_text(monkeypatch, text)
    return official_weight_pdf.parse_official_weight_pdf_archive(
        _archive(),
        filename="indices_dataOct2026.zip",
        underlying="NIFTY",
        source_name="NSE Indices monthly weight archive",
        source_url=(
            "https://www.niftyindices.com/Indices_-_Market_Capitalisation_and_Weightage/"
            "indices_dataOct2026.zip"
        ),
        expected_symbols=expected,
        as_of=__import__("datetime").date(2026, 11, 8),
    )


def test_parses_complete_official_pdf_and_preserves_archive_hash(monkeypatch):
    raw = _archive()
    _reader_with_text(
        monkeypatch,
        "Constituents of NIFTY 50\nOctober 30, 2026\n"
        "AAA Company A 60.00\nBBB Company B 40.00",
    )
    basket = official_weight_pdf.parse_official_weight_pdf_archive(
        raw,
        filename="indices_dataOct2026.zip",
        underlying="NIFTY",
        source_name="NSE Indices monthly weight archive",
        source_url="https://www.niftyindices.com/indices_dataOct2026.zip",
        expected_symbols=("AAA", "BBB"),
        as_of=__import__("datetime").date(2026, 11, 8),
    )
    assert basket.effective_date.isoformat() == "2026-10-30"
    assert basket.file_format == "PDFZIP"
    assert basket.sha256 == __import__("hashlib").sha256(raw).hexdigest()
    assert {row.symbol: row.weight for row in basket.components} == {
        "AAA": 60.0,
        "BBB": 40.0,
    }


def test_parses_weight_wrapped_onto_following_pdf_line(monkeypatch):
    basket = _parse(
        monkeypatch,
        "Constituents of NIFTY 50\nOctober 30, 2026\n"
        "AAA Company A 60.00\nBBB Company Name\nLimited 40.00",
    )
    assert {row.symbol: row.weight for row in basket.components} == {
        "AAA": 60.0,
        "BBB": 40.0,
    }


def test_rejects_incomplete_roster(monkeypatch):
    with pytest.raises(ValueError, match="roster mismatch"):
        _parse(
            monkeypatch,
            "Constituents of NIFTY 50\nOctober 30, 2026\nAAA Company A 100.00",
        )


def test_rejects_weight_total_not_equal_to_one_hundred(monkeypatch):
    with pytest.raises(ValueError, match="weight sum"):
        _parse(
            monkeypatch,
            "Constituents of NIFTY 50\nOctober 30, 2026\n"
            "AAA Company A 60.00\nBBB Company B 39.00",
        )


def test_rejects_missing_official_index_pdf(monkeypatch):
    _reader_with_text(monkeypatch, "unused")
    with pytest.raises(ValueError, match="exactly one NIFTY"):
        official_weight_pdf.parse_official_weight_pdf_archive(
            _archive("NIFTY_Bank_Oct2026.pdf"),
            filename="indices_dataOct2026.zip",
            underlying="NIFTY",
            source_name="NSE Indices monthly weight archive",
            source_url="https://www.niftyindices.com/indices_dataOct2026.zip",
            expected_symbols=("AAA", "BBB"),
            as_of=__import__("datetime").date(2026, 11, 8),
        )



def test_current_master_loader_uses_iv_isolated_official_failover(monkeypatch):
    from scripts import import_iv_official_baskets as importer

    rows = [{"symbol": "AAA", "exch_seg": "NSE", "token": "123"}]
    master = object()
    calls = []

    def fake_download_iv_instruments(received_master):
        calls.append(received_master)
        return rows

    monkeypatch.setattr(importer, "download_iv_instruments", fake_download_iv_instruments)
    assert importer._load_current_instrument_master(master) == rows
    assert calls == [master]


def test_current_master_loader_fails_closed_when_all_official_sources_fail(monkeypatch):
    from scripts import import_iv_official_baskets as importer

    def unavailable(_master):
        raise RuntimeError("primary and alternate hosts timed out; no recent validated snapshot")

    monkeypatch.setattr(importer, "download_iv_instruments", unavailable)
    with pytest.raises(RuntimeError, match="no recent validated snapshot.*no basket was imported"):
        importer._load_current_instrument_master(object())


def test_fetch_roster_accepts_metadata_and_normalises_symbol_header(monkeypatch):
    from scripts import import_iv_official_baskets as importer

    class FakeResponse:
        content = (
            b"\xef\xbb\xbfConstituent List\r\n"
            b" Company Name , Industry , SYMBOL  , Series\r\n"
            b"Example Bank,Financial Services,AAA,EQ\r\n"
            b"Another Bank,Financial Services,BBB,EQ\r\n"
        )
        headers = {"Content-Type": "application/octet-stream"}

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, _url, *, headers, timeout):
            assert timeout == 60
            assert headers["User-Agent"] == "Mozilla/5.0"
            assert headers["Accept"] == "text/csv,text/plain,*/*"
            assert headers["Referer"].endswith("/nifty-bank")
            return FakeResponse()

    symbols, digest = importer._fetch_roster(FakeSession(), "https://official.example/roster.csv")
    assert symbols == ("AAA", "BBB")
    assert len(digest) == 64


def test_fetch_roster_reports_received_header_when_symbol_is_absent():
    from scripts import import_iv_official_baskets as importer

    class FakeResponse:
        content = b"Company,Industry\nExample Bank,Finance\n"

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, _url, *, headers, timeout):
            assert headers["User-Agent"] == "Mozilla/5.0"
            assert headers["Accept"] == "text/csv,text/plain,*/*"
            return FakeResponse()

    with pytest.raises(ValueError, match="first rows"):
        importer._fetch_roster(FakeSession(), "https://official.example/roster.csv")

def test_fetch_roster_rejects_html_edge_response_before_csv_parsing():
    from scripts import import_iv_official_baskets as importer

    class FakeResponse:
        content = b"<!DOCTYPE html><html><title>Access denied</title></html>"
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def raise_for_status(self):
            pass

    class FakeSession:
        def get(self, _url, *, headers, timeout):
            assert headers["Accept"] == "text/csv,text/plain,*/*"
            return FakeResponse()

    with pytest.raises(ValueError, match="returned HTML instead of CSV"):
        importer._fetch_roster(FakeSession(), "https://www.niftyindices.com/roster.csv")

