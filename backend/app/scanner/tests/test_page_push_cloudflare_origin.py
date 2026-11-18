from types import SimpleNamespace

from app.scanner.page_push_websocket import _origin_allowed


def _websocket(origin: str):
    return SimpleNamespace(headers={"origin": origin})


def test_page_push_accepts_account_scoped_cloudflare_worker_origins():
    assert _origin_allowed(_websocket(
        "https://algo-trading.bhaskarprasad386.workers.dev"
    ))
    assert _origin_allowed(_websocket(
        "https://fix-cloudflare-cors-algo-trading.bhaskarprasad386.workers.dev"
    ))
    assert _origin_allowed(_websocket(
        "https://preview.bhaskarprasad386.workers.dev"
    ))


def test_page_push_rejects_other_cloudflare_worker_tenants():
    assert not _origin_allowed(_websocket("https://attacker.workers.dev"))
    assert not _origin_allowed(_websocket(
        "https://bhaskarprasad386.workers.dev.attacker.example"
    ))
    assert not _origin_allowed(_websocket(
        "https://algo-trading.other-account.workers.dev"
    ))
