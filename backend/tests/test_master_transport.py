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
    assert calls[0][1]["timeout"] == (8, 90)


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


def test_transport_tries_system_then_tls12_then_tls13(monkeypatch):
    rows = [{"symbol": "NIFTY", "exch_seg": "NSE", "token": "99926000"}]
    payload = json.dumps(rows).encode()
    profiles = []
    responses = [Response(b"", fail=True), Response(b"", fail=True), Response(payload)]

    def fake_profile_request(url, *, timeout, profile):
        profiles.append((url, timeout, profile))
        return responses.pop(0)

    monkeypatch.setattr(transport, "_profile_request", fake_profile_request)
    assert transport.download_master_rows(
        "https://example.test/master", retries=3, backoff_seconds=0
    ) == rows
    assert [profile for _, _, profile in profiles] == ["system", "tls12", "tls13"]


def test_tls_adapter_pins_requested_protocol_versions():
    import ssl

    tls12 = transport._TLSAdapter("tls12")
    tls13 = transport._TLSAdapter("tls13")
    assert tls12._profile == "tls12"
    assert tls13._profile == "tls13"
    assert ssl.TLSVersion.TLSv1_2 < ssl.TLSVersion.TLSv1_3


def test_transport_cleanup_closes_session_even_when_response_close_raises():
    rows = [{"symbol": "NIFTY", "exch_seg": "NSE", "token": "99926000"}]
    payload = json.dumps(rows).encode()

    class Session:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class BrokenCloseResponse(Response):
        def __init__(self, body):
            super().__init__(body)
            self._master_transport_session = Session()

        def close(self):
            raise RuntimeError("response close failed")

    response = BrokenCloseResponse(payload)
    assert transport.download_master_rows(
        "https://example.test/master",
        retries=1,
        get=lambda *args, **kwargs: response,
    ) == rows
    assert response._master_transport_session.closed


def test_transport_cleanup_does_not_mask_original_download_error():
    class BrokenCloseResponse(Response):
        def close(self):
            raise RuntimeError("cleanup failure")

    response = BrokenCloseResponse(b"", fail=True)
    with pytest.raises(RuntimeError, match="temporary HTTP failure"):
        transport.download_master_rows(
            "https://example.test/master",
            retries=1,
            get=lambda *args, **kwargs: response,
        )



def test_tcp_mss_mitigation_is_scoped_only_to_official_master_hosts():
    assert transport._tcp_mss_for_url(
        "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    ) == 1100
    assert transport._tcp_mss_for_url(
        "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
    ) == 1100
    assert transport._tcp_mss_for_url("https://apiconnect.angelone.in/rest/secure/angelbroking") is None
    assert transport._tcp_mss_for_url("https://example.test/master") is None


def test_tls_adapter_applies_reduced_mss_before_connect_only_when_configured():
    import socket
    from urllib3.connection import HTTPConnection

    adapter = transport._TLSAdapter("system", tcp_mss=1100)
    options = adapter.poolmanager.connection_pool_kw["socket_options"]
    assert options == [
        *HTTPConnection.default_socket_options,
        (socket.IPPROTO_TCP, socket.TCP_MAXSEG, 1100),
    ]
    default_adapter = transport._TLSAdapter("system")
    assert "socket_options" not in default_adapter.poolmanager.connection_pool_kw
