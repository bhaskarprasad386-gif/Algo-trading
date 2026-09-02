package com.algotrading.app

import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST

data class MarketStatus(val status: String, val message: String)

data class PaperEntryRequest(val price: Double, val quantity: Double, val stop_loss_pct: Double = 0.02, val target_pct: Double = 0.04)
data class PaperFill(val price: Double, val quantity: Double)
data class PaperPosition(val mode: String, val quantity: Double, val entry_price: Double, val stop_loss: Double, val target: Double)
data class PaperEntryResponse(val status: String, val mode: String, val fill: PaperFill, val entry_price: Double, val stop_loss: Double, val target: Double, val position: PaperPosition)
data class PaperExitRequest(val price: Double)
data class PaperExitResponse(val status: String, val entry_price: Double? = null, val exit_price: Double? = null, val quantity: Double? = null, val pnl: Double = 0.0)
data class PaperPositionResponse(val status: String, val position: PaperPosition? = null)

data class CashFutureOpportunity(
    val symbol: String,
    val cash_price: Double = 0.0,
    val future_price: Double = 0.0,
    val gap: Double = 0.0,
    val gap_pct: Double = 0.0,
    val gross_spread_profit: Double = 0.0,
    val margin_required: Double = 0.0,
    val deployed_capital: Double = 0.0,
    val net_profit: Double = 0.0,
    val roi_pct: Double = 0.0,
    val executable: Boolean = false,
)

data class CashFutureScanError(
    val symbol: String = "",
    val error: String = "",
)

data class CashFutureScanResponse(
    val status: String,
    val scanner: String,
    val mode: String,
    val symbols_requested: List<String> = emptyList(),
    val scanned_observations: Int = 0,
    val opportunity_count: Int = 0,
    val data: List<CashFutureOpportunity> = emptyList(),
    val errors: List<CashFutureScanError> = emptyList(),
)

interface ApiInterface {
    @GET("/") suspend fun getRootStatus(): MarketStatus
    @POST("/api/v1/execution/paper/entry") suspend fun paperEntry(@Body request: PaperEntryRequest): PaperEntryResponse
    @GET("/api/v1/execution/paper/position") suspend fun paperPosition(): PaperPositionResponse
    @POST("/api/v1/execution/paper/exit") suspend fun paperExit(@Body request: PaperExitRequest): PaperExitResponse
    @GET("/api/v1/scanner/cash-future/live/auto") suspend fun cashFutureScan(): CashFutureScanResponse
}

object ApiService {
    val retrofitService: ApiInterface by lazy {
        Retrofit.Builder()
            .addConverterFactory(GsonConverterFactory.create())
            .baseUrl(BuildConfig.BACKEND_BASE_URL)
            .build()
            .create(ApiInterface::class.java)
    }
}
