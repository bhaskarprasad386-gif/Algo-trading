"""Durable persistence adapter for multi-leg backtest baskets."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from app.backtesting.ledger import BacktestLedger, LedgerRecord
from app.backtesting.multi_leg_pnl import BasketPnl


class MultiLegLedgerWriter:
    """Write basket-level results as compact append-only ledger records."""

    def __init__(self, ledger: BacktestLedger) -> None:
        self.ledger = ledger

    def append(self, run_id: str, basket: BasketPnl, timestamp_ns: int) -> None:
        self.ledger.append(
            LedgerRecord(
                run_id=run_id,
                record_type="multi_leg_basket",
                timestamp_ns=timestamp_ns,
                payload={
                    "signal_id": basket.signal_id,
                    "gross_pnl": basket.gross_pnl,
                    "charges": basket.charges,
                    "funding": basket.funding,
                    "net_pnl": basket.net_pnl,
                    "legs": [asdict(leg) for leg in basket.legs],
                },
            )
        )

    def append_many(self, run_id: str, baskets: Iterable[BasketPnl], timestamp_ns: int) -> int:
        records = []
        for basket in baskets:
            records.append(
                LedgerRecord(
                    run_id=run_id,
                    record_type="multi_leg_basket",
                    timestamp_ns=timestamp_ns,
                    payload={
                        "signal_id": basket.signal_id,
                        "gross_pnl": basket.gross_pnl,
                        "charges": basket.charges,
                        "funding": basket.funding,
                        "net_pnl": basket.net_pnl,
                        "legs": [asdict(leg) for leg in basket.legs],
                    },
                )
            )
        return self.ledger.append_batch(records)
