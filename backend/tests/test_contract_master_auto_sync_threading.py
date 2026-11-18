import threading

from app import main


def test_sync_contract_master_snapshot_owns_catalog_on_worker_thread(monkeypatch, tmp_path):
    created_thread_ids = []
    closed_thread_ids = []

    class FakeCatalog:
        def __init__(self, database_path):
            created_thread_ids.append(threading.get_ident())
            self.database_path = database_path

        def close(self):
            closed_thread_ids.append(threading.get_ident())

    class FakeResult:
        snapshot_date = None
        records = 7
        skipped = False

    class FakeSync:
        def __init__(self, catalog):
            assert threading.get_ident() == created_thread_ids[-1]
            self.catalog = catalog

        def sync(self, *, snapshot_date):
            assert threading.get_ident() == created_thread_ids[-1]
            return FakeResult()

    monkeypatch.setattr(main, "ContractMasterCatalog", FakeCatalog)
    monkeypatch.setattr(main, "DailyContractMasterSync", FakeSync)

    result = main._sync_contract_master_snapshot(
        str(tmp_path / "contract.sqlite3"),
        None,
    )

    assert result.records == 7
    assert len(created_thread_ids) == 1
    assert closed_thread_ids == created_thread_ids
