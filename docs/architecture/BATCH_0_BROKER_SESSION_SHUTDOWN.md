# Batch 0: Broker Session Shutdown

## Scope

Broker execution adapters are process-local. Their authenticated client objects and tokens are not restored after an application restart. Startup already clears the durable Box Spread owner connection flag and keeps the real-trading kill switch engaged.

## Graceful shutdown

The application lifespan now drains the process-local broker session store during shutdown and calls each adapter's `disconnect()` method. The store is cleared before provider logout calls begin, so no request can retrieve a session while shutdown cleanup is underway.

Disconnect failures are isolated per provider: one failed logout is logged and does not prevent other sessions from being disconnected or the existing market-data/scanner shutdown sequence from running.

## Safety boundary

- No credentials or access tokens are persisted.
- No adapter session is automatically restored after restart.
- Startup continues to mark the durable owner disconnected and disarmed.
- This change does not add order placement, cancellation, modification, or Live authorization.
- It does not alter the Box Spread scanner, shared WebSocket consumers, or synthetic runner.
