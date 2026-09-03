package com.algotrading.app

/**
 * Compatibility accessors used by the Android paper-trading UI.
 * Keep the wire models aligned with the backend while exposing the
 * UI-friendly fields expected by MainActivity.
 */
val PaperEntryResponse.quantity: Double
    get() = fill.quantity

val PaperPositionResponse.active: Boolean
    get() = position != null

val PaperPositionResponse.symbol: String
    get() = position?.symbol ?: "PAPER"

val PaperPositionResponse.entry_price: Double
    get() = position?.entry_price ?: 0.0

val PaperPositionResponse.quantity: Double
    get() = position?.quantity ?: 0.0

val PaperPositionResponse.orders: List<PaperOrder>
    get() = emptyList()

val PaperExitResponse.net_profit: Double
    get() = pnl
