"""Read-only bridge from broker-normalized records to quote parity observations.

No broker callback, subscription, scanner, or order path is connected here.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from .contracts import MarketDataRecord
from .provider_contracts import CanonicalContractKey
from .provider_quote_quality import ProviderQuoteObservation


ProviderName = Literal["angel_one", "shoonya"]


def observation_from_market_data_record(
    provider: ProviderName,
    record: MarketDataRecord,
    *,
    generation: int,
    authenticated: bool,
    connection_current: bool,
    received_monotonic_ns: int,
    depth_complete: bool = False,
) -> ProviderQuoteObservation:
    """Adapt one existing normalized record without changing its source data.

    depth_complete must be supplied by a separately verified provider adapter.
    It defaults to False because MarketDataRecord only guarantees top-of-book
    fields, not complete multi-level depth.
    """
    if provider not in ("angel_one", "shoonya"):
        raise ValueError("provider must be angel_one or shoonya")
    if not isinstance(record, MarketDataRecord):
        raise TypeError("record must be a MarketDataRecord")
    expiry = None
    if record.expiry:
        if type(record.expiry) is date:
            expiry = record.expiry
        elif isinstance(record.expiry, str):
            expiry = date.fromisoformat(record.expiry)
        else:
            raise TypeError("record expiry must be an ISO date string or date")

    contract = CanonicalContractKey(
        exchange=record.instrument.exchange,
        segment=record.instrument.segment,
        underlying=record.underlying or record.symbol,
        instrument_type=record.instrument_type.value,
        expiry=expiry,
        strike=record.strike,
        option_type=record.option_type.value if record.option_type else None,
    )
    return ProviderQuoteObservation(
        provider=provider,
        contract=contract,
        generation=generation,
        authenticated=authenticated,
        connection_current=connection_current,
        exchange_timestamp_ns=record.timestamp_ns or None,
        received_monotonic_ns=received_monotonic_ns,
        bid=record.bid,
        ask=record.ask,
        bid_size=record.bid_qty,
        ask_size=record.ask_qty,
        depth_complete=depth_complete,
    )
