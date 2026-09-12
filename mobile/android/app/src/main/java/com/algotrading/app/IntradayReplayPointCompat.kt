package com.algotrading.app

/**
 * Backward-compatible replay price for legacy IntradayReplayView mapping.
 * IntradayReplayPoint intentionally stores OHLC without a close field; use
 * the candle open as the single-price fallback for the legacy replay path.
 */
val IntradayReplayPoint.close: Double
    get() = open
