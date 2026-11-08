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



def test_alternate_official_angel_master_is_loaded_into_importer_view(monkeypatch):
    from scripts import import_iv_official_baskets as importer

    rows = [
        {"symbol": "AAA", "exch_seg": "NSE", "token": "123", "instrumenttype": "EQ"}
    ]

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return rows

    requested = {}

    def fake_get(url, *, timeout):
        requested["url"] = url
        requested["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(importer.requests, "get", fake_get)
    master = SimpleNamespace(
        instruments=[],
        _loaded=False,
        _symbol_exchange_index="stale",
    )

    loaded = importer._load_alternate_official_master(master)

    assert loaded == rows
    assert master.instruments == rows
    assert master._loaded is True
    assert master._symbol_exchange_index is None
    assert requested["url"] == importer.ANGEL_ONE_ALTERNATE_MASTER_URL
    assert requested["timeout"] == (15, 90)


def test_invalid_alternate_official_angel_master_is_rejected(monkeypatch):
    from scripts import import_iv_official_baskets as importer

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"symbol": "AAA", "exch_seg": "NSE", "token": ""}]

    monkeypatch.setattr(
        importer.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    master = SimpleNamespace(
        instruments=[],
        _loaded=False,
        _symbol_exchange_index=None,
    )

    with pytest.raises(ValueError, match="invalid or empty"):
        importer._load_alternate_official_master(master)


def test_current_master_loader_uses_alternate_after_primary_failure(monkeypatch):
    from scripts import import_iv_official_baskets as importer

    rows = [{"symbol": "AAA", "exch_seg": "NSE", "token": "123"}]
    master = SimpleNamespace(download=lambda: (_ for _ in ()).throw(TimeoutError("primary timeout")))
    monkeypatch.setattr(
        importer,
        "_load_alternate_official_master",
        lambda _master: rows,
    )

    assert importer._load_current_instrument_master(master) == rows
