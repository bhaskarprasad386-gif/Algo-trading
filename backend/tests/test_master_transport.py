import json

import pytest

from app.market_data import master_transport as transport


class Response:
    def __init__(self, payload, fail=False):
        self.payload = payload
        self.fail = fail
        self.closed = False

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("temporary HTTP failure")

    def iter_content(self, chunk_size):
        assert chunk_size == transport.MASTER_CHUNK_BYTES
        yield self.payload[:9]
        yield b""
        yield self.payload[9:]

    def close(self):
        self.closed = True


def test_download_master_streams_valid_rows_and_closes_response():
    rows = [{"symbol": "NIFTY", "exch_seg": "NSE", "token": "99926000"}]
    response = Response(json.dumps(rows).encode())
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    assert transport.download_master_rows("https://example.test/master", get=get) == rows
    assert response.closed
    assert calls[0][1]["stream"] is True
    assert calls[0][1]["timeout"] == (10, 120)


def test_download_master_retries_transient_failure():
    rows = [{"symbol": "NIFTY", "exch_seg": "NSE", "token": "99926000"}]
    responses = [Response(b"", fail=True), Response(json.dumps(rows).encode())]
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return responses.pop(0)

    assert transport.download_master_rows(
        "https://example.test/master", retries=2, backoff_seconds=0, get=get
    ) == rows
    assert len(calls) == 2


@pytest.mark.parametrize("payload", [
    b"{}",
    b"[]",
    b'[{"symbol":"X","exch_seg":"NSE"}]',
    b'[{"symbol":"X","exch_seg":"NSE","token":"1"}, null]',
])
def test_download_master_rejects_invalid_payloads(payload):
    response = Response(payload)
    with pytest.raises(ValueError):
        transport.download_master_rows(
            "https://example.test/master", retries=1, get=lambda *a, **k: response
        )
    assert response.closed


def test_download_master_rejects_oversized_payload():
    class Oversized(Response):
        def iter_content(self, chunk_size):
            yield b"x" * (transport.MASTER_MAX_BYTES + 1)

    response = Oversized(b"")
    with pytest.raises(ValueError, match="exceeds"):
        transport.download_master_rows(
            "https://example.test/master", retries=1, get=lambda *a, **k: response
        )
    assert response.closed
