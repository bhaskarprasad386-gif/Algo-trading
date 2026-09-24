from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.backtesting.catalog_continuous_futures import build_continuous_futures_from_catalog
from app.backtesting.contract_master import ContractMasterCatalog, ContractRecord
from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord


IST = ZoneInfo("Asia/Kolkata")


def _ns(day: date) -> int:
    return int(datetime.combine(day, time(10, 0), tzinfo=IST).timestamp() * 1_000_000_000)


def _contract(token: str, expiry: date, snapshot: date) -> ContractRecord:
    return ContractRecord(
        exchange="NFO",
        symbol=f"ABC{token}FUT",
        token=token,
        expiry=expiry,
        instrument_type="STOCK_FUTURE",
        underlying="ABC",
        lot_size=1,
        snapshot_date=snapshot,
    )


def _bar(token: str, day: date, close: float) -> HistoricalRecord:
    return HistoricalRecord(
        source="test-source",
        instrument=f"NFO:{token}",
        timeframe="5m",
        timestamp_ns=_ns(day),
        payload={"close": close, "token": token},
    )


def test_contract_master_snapshots_build_multi_expiry_chain_from_catalog():
    contracts = ContractMasterCatalog()
    history = HistoricalCatalog()
    try:
        contracts.upsert_snapshot(date(2026, 1, 1), (
            _contract("JAN", date(2026, 1, 29), date(2026, 1, 1)),
            _contract("FEB", date(2026, 2, 26), date(2026, 1, 1)),
        ))
        contracts.upsert_snapshot(date(2026, 2, 1), (
            _contract("FEB", date(2026, 2, 26), date(2026, 2, 1)),
            _contract("MAR", date(2026, 3, 26), date(2026, 2, 1)),
        ))
        history.ingest((
            _bar("JAN", date(2026, 1, 28), 100.0),
            _bar("JAN", date(2026, 1, 29), 101.0),
            _bar("FEB", date(2026, 1, 30), 102.0),
            _bar("FEB", date(2026, 2, 26), 110.0),
            _bar("MAR", date(2026, 2, 27), 111.0),
        ))

        series = build_continuous_futures_from_catalog(
            contracts,
            history,
            underlying="ABC",
            start_date=date(2026, 1, 28),
            end_date=date(2026, 3, 2),
            source="test-source",
            timeframe="5m",
        )

        assert [item.contract_token for item in series] == ["JAN", "JAN", "FEB", "FEB", "MAR"]
        assert [item.payload["close"] for item in series] == [100.0, 101.0, 102.0, 110.0, 111.0]
    finally:
        contracts.close()
        history.close()


def test_missing_contract_bars_are_not_synthesized_and_post_expiry_data_is_excluded():
    contracts = ContractMasterCatalog()
    history = HistoricalCatalog()
    try:
        contracts.upsert_snapshot(date(2026, 1, 1), (
            _contract("JAN", date(2026, 1, 29), date(2026, 1, 1)),
            _contract("FEB", date(2026, 2, 26), date(2026, 1, 1)),
        ))
        history.ingest((
            _bar("JAN", date(2026, 1, 29), 100.0),
            _bar("FEB", date(2026, 1, 30), 105.0),
            _bar("FEB", date(2026, 2, 27), 999.0),
        ))

        series = build_continuous_futures_from_catalog(
            contracts,
            history,
            underlying="ABC",
            start_date=date(2026, 1, 29),
            end_date=date(2026, 2, 28),
            source="test-source",
            timeframe="5m",
        )

        assert [(item.contract_token, item.payload["close"]) for item in series] == [
            ("JAN", 100.0),
            ("FEB", 105.0),
        ]
    finally:
        contracts.close()
        history.close()
