# Backtest Order Lifecycle

The event-driven backtest now carries a shared time-in-force contract on simulated orders and integrates deterministic order lifecycle state with execution.

Supported lifecycle semantics:

- SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED
- IOC residual quantity -> CANCELLED
- FOK insufficient executable quantity -> REJECTED without portfolio fills
- STOP orders execute only after an observed point-in-time trigger
- Lifecycle state is exposed by the event engine for audit/testing

Execution remains point-in-time and uses only observed quote/depth data. Partial depth is never expanded into fabricated liquidity.
