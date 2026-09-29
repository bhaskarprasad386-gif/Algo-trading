"""Daily SQLite shard manager for durable live market data.

The legacy base catalog remains readable. New live records are written to
YYYY_MM_DD shard databases so one trading day cannot grow one SQLite file
without bound. Shards are strategy-neutral and preserve exact record identity.
"""

from __future__ import annotations

from datetime import date, datetime
import heapq
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from app.backtesting.historical_catalog import HistoricalCatalog, HistoricalRecord, Gap
from app.backtesting.trading_calendar import TradingCalendar

IST = ZoneInfo("Asia/Kolkata")
SHARD_SUFFIX = ".sqlite3"


def shard_path(base_path: str | Path, trading_date: date) -> Path:
    base = Path(base_path)
    return base.with_name(f"{base.stem}_{trading_date:%Y_%m_%d}{base.suffix or SHARD_SUFFIX}")


def _record_date(timestamp_ns: int) -> date:
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=IST).date()


class DailyMarketDataShardCatalog:
    """Route new records to daily SQLite shards while reading legacy + shards."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        self._catalogs: dict[Path, HistoricalCatalog] = {}
        self._legacy = HistoricalCatalog(str(self.base_path)) if self.base_path.exists() else None

    def _open(self, path: Path) -> HistoricalCatalog:
        catalog = self._catalogs.get(path)
        if catalog is None:
            catalog = HistoricalCatalog(str(path))
            self._catalogs[path] = catalog
        return catalog

    def _catalogs_for_read(self) -> tuple[HistoricalCatalog, ...]:
        catalogs: list[HistoricalCatalog] = []
        if self._legacy is not None:
            catalogs.append(self._legacy)
        for path in sorted(self.base_path.parent.glob(f"{self.base_path.stem}_????_??_??{self.base_path.suffix or SHARD_SUFFIX}")):
            if self._legacy is not None and path.resolve() == self.base_path.resolve():
                continue
            catalogs.append(self._open(path))
        return tuple(catalogs)

    @staticmethod
    def _group_by_day(records: Iterable[HistoricalRecord]) -> dict[date, list[HistoricalRecord]]:
        grouped: dict[date, list[HistoricalRecord]] = {}
        for record in records:
            if not isinstance(record, HistoricalRecord):
                raise TypeError("records must contain HistoricalRecord values")
            grouped.setdefault(_record_date(record.timestamp_ns), []).append(record)
        return grouped

    def ingest_if_absent_batch(self, records: Iterable[HistoricalRecord], *, ingested_at_ns: int = 0) -> int:
        grouped = self._group_by_day(records)
        inserted = 0
        for trading_date, batch in grouped.items():
            inserted += self._open(shard_path(self.base_path, trading_date)).ingest_if_absent_batch(
                batch, ingested_at_ns=ingested_at_ns
            )
        return inserted

    def ingest_if_absent(self, record: HistoricalRecord, *, ingested_at_ns: int = 0) -> int:
        return self.ingest_if_absent_batch((record,), ingested_at_ns=ingested_at_ns)

    def ingest(self, records: Iterable[HistoricalRecord] | HistoricalRecord, *, ingested_at_ns: int = 0) -> int:
        if isinstance(records, HistoricalRecord):
            records = (records,)
        grouped = self._group_by_day(records)
        return sum(
            self._open(shard_path(self.base_path, trading_date)).ingest(batch, ingested_at_ns=ingested_at_ns)
            for trading_date, batch in grouped.items()
        )

    def append(self, records: Iterable[HistoricalRecord], *, ingested_at_ns: int = 0) -> int:
        return self.ingest(records, ingested_at_ns=ingested_at_ns)

    def ingest_events(self, records: Iterable[HistoricalRecord], *, ingested_at_ns: int = 0) -> int:
        return self.ingest(records, ingested_at_ns=ingested_at_ns)

    def _all(self, method: str, **kwargs):
        return [getattr(catalog, method)(**kwargs) for catalog in self._catalogs_for_read()]

    def count(self, *, source: str | None = None, instrument: str | None = None, timeframe: str | None = None) -> int:
        return sum(self._all("count", source=source, instrument=instrument, timeframe=timeframe))

    def instruments(self, *, source: str, timeframe: str, start_ns: int | None = None, end_ns: int | None = None, prefix: str | None = None) -> tuple[str, ...]:
        values: set[str] = set()
        for catalog in self._catalogs_for_read():
            values.update(catalog.instruments(source=source, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns, prefix=prefix))
        return tuple(sorted(values))

    def watermark(self, *, source: str, instrument: str, timeframe: str) -> int | None:
        values = [
            value for value in self._all("watermark", source=source, instrument=instrument, timeframe=timeframe)
            if value is not None
        ]
        return max(values) if values else None

    def timestamps(self, *, source: str, instrument: str, timeframe: str, start_ns: int, end_ns: int) -> tuple[int, ...]:
        values: set[int] = set()
        for catalog in self._catalogs_for_read():
            values.update(catalog.timestamps(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns))
        return tuple(sorted(values))

    def records(self, *, source: str, instrument: str, timeframe: str) -> tuple[HistoricalRecord, ...]:
        return tuple(self.iter_records(source=source, instrument=instrument, timeframe=timeframe))

    def iter_records(self, *, source: str, instrument: str | None = None, timeframe: str, start_ns: int | None = None, end_ns: int | None = None):
        iterators = [
            iter(catalog.iter_records(
                source=source, instrument=instrument, timeframe=timeframe,
                start_ns=start_ns, end_ns=end_ns
            ))
            for catalog in self._catalogs_for_read()
        ]
        heap: list[tuple[int, str, int, HistoricalRecord, object]] = []
        for index, iterator in enumerate(iterators):
            try:
                record = next(iterator)
            except StopIteration:
                continue
            heapq.heappush(heap, (record.timestamp_ns, record.instrument, index, record, iterator))
        while heap:
            _, _, index, record, iterator = heapq.heappop(heap)
            yield record
            try:
                next_record = next(iterator)
            except StopIteration:
                continue
            heapq.heappush(heap, (next_record.timestamp_ns, next_record.instrument, index, next_record, iterator))

    def events(self, *, source: str, instrument: str, timeframe: str, start_ns: int | None = None, end_ns: int | None = None) -> tuple[HistoricalRecord, ...]:
        return tuple(self.iter_records(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns))

    def event_count(self, *, source: str, instrument: str, timeframe: str, start_ns: int | None = None, end_ns: int | None = None) -> int:
        return sum(catalog.event_count(source=source, instrument=instrument, timeframe=timeframe, start_ns=start_ns, end_ns=end_ns) for catalog in self._catalogs_for_read())

    def records_by_instruments(self, *, source: str, instruments: Iterable[str], timeframe: str) -> dict[str, tuple[HistoricalRecord, ...]]:
        names = tuple(dict.fromkeys(instruments))
        return {
            instrument: tuple(self.iter_records(source=source, instrument=instrument, timeframe=timeframe))
            for instrument in names
        }

    def records_by_contract_tokens(self, *, source: str, contract_tokens: Iterable[str], timeframe: str, instrument_prefix: str = "NFO:") -> dict[str, tuple[HistoricalRecord, ...]]:
        tokens = tuple(dict.fromkeys(contract_tokens))
        return {
            token: self.records(source=source, instrument=f"{instrument_prefix}{token}", timeframe=timeframe)
            for token in tokens
        }

    def gaps(self, *, source: str, instrument: str, timeframe: str, interval_ns: int) -> tuple[Gap, ...]:
        timestamps = self.timestamps(
            source=source, instrument=instrument, timeframe=timeframe,
            start_ns=-(1 << 63), end_ns=(1 << 63) - 1
        )
        gaps: list[Gap] = []
        for previous, current in zip(timestamps, timestamps[1:]):
            if current - previous > interval_ns:
                gaps.append(Gap(instrument, timeframe, previous + interval_ns, current - interval_ns))
        return tuple(gaps)

    def session_gaps(self, *, source: str, instrument: str, timeframe: str, interval_ns: int, calendar: TradingCalendar, start_date, end_date) -> tuple[Gap, ...]:
        gaps: list[Gap] = []
        for session in calendar.sessions_between(start_date, end_date):
            timestamps = self.timestamps(source=source, instrument=instrument, timeframe=timeframe, start_ns=session.start_ns, end_ns=session.end_ns)
            for previous, current in zip(timestamps, timestamps[1:]):
                if current - previous > interval_ns:
                    gaps.append(Gap(instrument, timeframe, previous + interval_ns, current - interval_ns))
        return tuple(gaps)

    def checkpoint(self, *, mode: str = "PASSIVE") -> tuple[int, int, int]:
        rows = [catalog.checkpoint(mode=mode) for catalog in self._catalogs_for_read()]
        return rows[-1] if rows else (0, 0, 0)

    def shard_paths(self) -> tuple[Path, ...]:
        return tuple(sorted(self.base_path.parent.glob(f"{self.base_path.stem}_????_??_??{self.base_path.suffix or SHARD_SUFFIX}")))

    def close(self) -> None:
        seen: set[int] = set()
        for catalog in self._catalogs_for_read():
            marker = id(catalog)
            if marker in seen:
                continue
            seen.add(marker)
            catalog.close()
        self._catalogs.clear()
        self._legacy = None
