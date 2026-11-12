import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from app.market_data import instruments as instruments_module


def _rows():
    return [{"symbol": "NIFTY", "exch_seg": "NSE", "token": "99926000"}]


def _reset_cache():
    cls = instruments_module.InstrumentMaster
    with cls._cache_lock:
        cls._cached_instruments = None
        cls._cached_snapshot_date = None


def test_cache_reads_are_not_blocked_while_master_network_refresh_waits(monkeypatch):
    cls = instruments_module.InstrumentMaster
    _reset_cache()
    entered_download = threading.Event()
    release_download = threading.Event()
    download_errors = []

    def slow_download(*args, **kwargs):
        entered_download.set()
        assert release_download.wait(timeout=3)
        return _rows()

    monkeypatch.setattr(instruments_module, "download_master_rows", slow_download)
    master = cls()

    def run_download():
        try:
            master.download(force=True)
        except Exception as exc:  # surfaced in the main test thread below
            download_errors.append(exc)

    worker = threading.Thread(target=run_download, daemon=True)
    worker.start()
    assert entered_download.wait(timeout=2)

    # This is the lock used by constructors/readers. It must remain available
    # while the network call is blocked on the separate download lock.
    acquired = cls._cache_lock.acquire(timeout=0.5)
    assert acquired, "cache lock was held during network I/O"
    if acquired:
        cls._cache_lock.release()

    release_download.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert not download_errors
    assert master.instruments == _rows()
    _reset_cache()


def test_concurrent_refreshes_are_single_flight(monkeypatch):
    cls = instruments_module.InstrumentMaster
    _reset_cache()
    entered_download = threading.Event()
    release_download = threading.Event()
    calls = []
    errors = []
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()

    def slow_download(*args, **kwargs):
        calls.append(args[0])
        entered_download.set()
        assert release_download.wait(timeout=3)
        return _rows()

    monkeypatch.setattr(instruments_module, "download_master_rows", slow_download)
    first, second = cls(), cls()

    def run(master):
        try:
            master.download()
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=run, args=(first,), daemon=True)
    t2 = threading.Thread(target=run, args=(second,), daemon=True)
    t1.start()
    assert entered_download.wait(timeout=2)
    t2.start()
    release_download.set()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert not t1.is_alive() and not t2.is_alive()
    assert not errors
    assert len(calls) == 1
    assert first.instruments == second.instruments == _rows()
    assert cls._cached_snapshot_date == today
    _reset_cache()
