"""No-op compatibility adapter: locally derived IV is disabled.

The shared market-data runner still publishes raw quotes, OI, volume and spot
records. IV fields are explicitly null so no local Black-Scholes inversion or
stale derived-IV history can leak into snapshots.
"""
from __future__ import annotations


def implied_volatility(price, spot, strike, expiry, option_type, *, now=None):
    """Local IV calculation is disabled; broker-native IV is handled separately."""
    return None


class SharedIndexDerivedIV:
    """Compatibility adapter that clears/returns null IV fields without calculation."""

    def __init__(self):
        self._history = {}

    def decorate(self, records, spots):
        out = []
        for record in records:
            row = dict(record)
            row["iv_percent"] = None
            row["iv_source"] = None
            row["iv_delta_1s"] = None
            row["iv_delta_3s"] = None
            row["iv_delta_5s"] = None
            out.append(row)
        return out

    def clear(self):
        self._history.clear()
