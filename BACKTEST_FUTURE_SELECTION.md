# Cash–Future Backtest Future Selection

## User-selectable Future mode
The backtest UI must provide a mandatory Future Data Selection option:

- `CURRENT` — Spot BUY + historical current-month Future SELL.
- `NEAR` — Spot BUY + historical near-month Future SELL.
- `BOTH` — Spot BUY + historical current-month Future SELL + historical near-month Future SELL.

## Historical contract rules
- `CURRENT` and `NEAR` are resolved from the historical replay timestamp, actual contract expiry and historical contract availability.
- Never substitute today's contract for a historical date.
- In `BOTH`, the current and near contracts selected at entry are tracked as distinct contract identities until their applicable expiry/settlement; a later newly introduced contract must not silently replace an already-open leg.
- Index futures are excluded from the stock Cash–Future strategy because there is no corresponding individual cash/equity leg.

## Output rules
For the selected mode, show the applicable Spot/Future legs, gap, executable gap when bid/ask data exists, lot size, quantity, gross P&L, charges, funding, slippage and net P&L.

`BOTH` must also show separate current-leg and near-leg P&L plus combined portfolio P&L.

## Replay rules
- Canonical source remains 1-minute historical data.
- Replay controls remain 1m, 5m, 10m, 15m, 30m, 1h and 1d.
- Changing the Future selection or replay interval must not alter the underlying 1-minute historical dataset.
- The selected Future mode must be stored with the backtest job/result so the run is reproducible.

## Validation
- [ ] CURRENT mode selects and tracks the correct historical current-month contract.
- [ ] NEAR mode selects and tracks the correct historical near-month contract.
- [ ] BOTH mode opens both historical futures legs and keeps their contract identities fixed through the trade.
- [ ] Historical contract rollover cannot silently replace an already-open leg.
- [ ] Selected mode is persisted and returned in job/result APIs.
- [ ] P&L, lot size, charges and funding are calculated separately and combined correctly for BOTH mode.
