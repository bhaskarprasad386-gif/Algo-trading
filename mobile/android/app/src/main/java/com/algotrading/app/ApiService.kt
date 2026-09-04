package com.algotrading.app

import android.content.Context
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

data class MarketStatus(val status: String, val message: String)
data class MarketLtpResponse(val status: Boolean = false, val exchange: String = "", val tradingsymbol: String = "", val symboltoken: String = "", val ltp: Double? = null)
data class PaperEntryRequest(val price: Double, val quantity: Double, val stop_loss_pct: Double = 0.02, val target_pct: Double = 0.04)
data class PaperFill(val price: Double, val quantity: Double)
data class PaperPosition(val symbol: String = "PAPER", val mode: String, val quantity: Double, val entry_price: Double, val stop_loss: Double, val target: Double)
data class PaperEntryResponse(val status: String, val mode: String, val fill: PaperFill, val entry_price: Double, val stop_loss: Double, val target: Double, val position: PaperPosition, val virtual_balance: Double = 0.0, val realized_pnl: Double = 0.0)
data class PaperExitRequest(val price: Double)
data class PaperExitResponse(val status: String, val entry_price: Double? = null, val exit_price: Double? = null, val quantity: Double? = null, val pnl: Double = 0.0, val virtual_balance: Double = 0.0, val realized_pnl: Double = 0.0)
data class PaperPositionResponse(val status: String, val position: PaperPosition? = null)
data class PaperOrder(val id: String, val symbol: String, val transaction_type: String, val price: Double? = null, val quantity: Double = 0.0, val status: String, val pnl: Double = 0.0)
data class PaperOrdersResponse(val mode: String, val orders: List<PaperOrder> = emptyList())
data class ScannerPaperEntryRequest(val symbol: String, val cash_price: Double, val quantity: Double, val future_price: Double? = null, val gap: Double? = null, val net_profit: Double? = null, val executable: Boolean = true, val stop_loss_pct: Double = 0.02, val target_pct: Double = 0.04)
data class ScannerPaperEntryResponse(val status: String, val mode: String, val source: String, val scanner_entry_price: Double, val order: PaperOrder, val position: PaperPosition? = null, val virtual_balance: Double = 0.0, val realized_pnl: Double = 0.0)
data class CashFutureOpportunity(val symbol: String, val cash_price: Double = 0.0, val future_price: Double = 0.0, val gap: Double = 0.0, val gap_pct: Double = 0.0, val gross_spread_profit: Double = 0.0, val margin_required: Double = 0.0, val deployed_capital: Double = 0.0, val net_profit: Double = 0.0, val roi_pct: Double = 0.0, val executable: Boolean = false)
data class CashFutureScanError(val symbol: String = "", val error: String = "")
data class CashFutureScanResponse(val status: String, val scanner: String, val mode: String, val symbols_requested: List<String> = emptyList(), val scanned_observations: Int = 0, val opportunity_count: Int = 0, val data: List<CashFutureOpportunity> = emptyList(), val errors: List<CashFutureScanError> = emptyList())
data class RegisterRequest(val email: String? = null, val mobile_number: String? = null, val password: String, val full_name: String? = null)
data class LoginRequest(val identifier: String, val password: String)
data class TokenResponse(val access_token: String, val token_type: String = "bearer")
data class AccountInfo(val id: Int, val mode: String, val virtual_balance: Double, val realized_pnl: Double = 0.0, val is_active: Boolean)
data class UserInfo(val id: Int, val email: String? = null, val mobile_number: String? = null, val full_name: String? = null, val account: AccountInfo)
data class BrokerConnectRequest(val broker: String = "angel_one", val display_name: String? = null, val api_key: String, val client_code: String, val password: String, val totp_secret: String)
data class BrokerConnectionInfo(val broker: String, val connected: Boolean, val display_name: String? = null, val connected_at: String? = null)
data class BrokerConnectionsResponse(val connections: List<BrokerConnectionInfo> = emptyList())
data class BrokerConnectResponse(val connected: Boolean, val broker: String, val display_name: String? = null, val client_code: String? = null, val real_trading: Boolean = false)
data class BrokerStatusResponse(val broker: String, val connected: Boolean, val display_name: String? = null, val real_trading: Boolean = false, val kill_switch: Boolean = true)
data class SafetyResponse(val real_trading_enabled: Boolean = false, val kill_switch: Boolean = true, val enabled_at: String? = null, val broker_connected: Boolean = false, val live_order_routing: Boolean = false, val message: String? = null)
data class RealTradingEnableRequest(val confirmation: String)

data class FullFnoJobRequest(val days: Int = 365, val min_entry_gap: Double = 0.0, val exit_gap: Double = 0.0, val charges_per_trade: Double = 0.0, val funding_cost_per_trade: Double = 0.0, val max_holding_days: Int = 30, val future_selection: String = "BOTH")
data class FullFnoJobAcceptedResponse(val status: String, val universe: String, val future_selection: String, val job: String)
data class FullFnoJobState(val job_id: String, val status: String, val symbol: String = "", val contract_month: String = "", val requested_days: Int = 0, val progress_pct: Double = 0.0, val symbols_processed: Int = 0, val symbols_total: Int = 0, val result_chunks: Int = 0, val message: String? = null, val result: Map<String, Any?>? = null, val created_at: String? = null, val updated_at: String? = null)
data class FullFnoJobStatusResponse(val status: String, val job: FullFnoJobState)
data class FullFnoResultChunk(val sequence: Int, val symbol: String, val result: Map<String, Any?> = emptyMap(), val created_at: String? = null)
data class FullFnoResultsPage(val status: String, val job_id: String, val total: Int = 0, val offset: Int = 0, val limit: Int = 0, val after_sequence: Int? = null, val next_after_sequence: Int? = null, val data: List<FullFnoResultChunk> = emptyList())
data class FullFnoJobControlResponse(val status: String, val job_id: String, val job_status: String)
data class FullFnoPurgeResponse(val status: String, val job_id: String, val job_status: String, val deleted_chunks: Int)

interface ApiInterface {
    @GET("/") suspend fun getRootStatus(): MarketStatus
    @POST("/api/v1/auth/register") suspend fun register(@Body request: RegisterRequest): TokenResponse
    @POST("/api/v1/auth/login") suspend fun login(@Body request: LoginRequest): TokenResponse
    @GET("/api/v1/auth/me") suspend fun me(): UserInfo
    @POST("/api/v1/auth/logout") suspend fun logout(): Map<String, String>
    @GET("/api/v1/brokers/connections") suspend fun brokerConnections(): BrokerConnectionsResponse
    @POST("/api/v1/brokers/connect") suspend fun connectBroker(@Body request: BrokerConnectRequest): BrokerConnectResponse
    @GET("/api/v1/brokers/{broker}/status") suspend fun brokerStatus(@Path("broker") broker: String): BrokerStatusResponse
    @DELETE("/api/v1/brokers/{broker}") suspend fun disconnectBroker(@Path("broker") broker: String): BrokerStatusResponse
    @GET("/api/v1/brokers/safety") suspend fun safetyStatus(): SafetyResponse
    @POST("/api/v1/brokers/safety/enable") suspend fun enableRealTrading(@Body request: RealTradingEnableRequest): SafetyResponse
    @POST("/api/v1/brokers/safety/disable") suspend fun disableRealTrading(): SafetyResponse
    @POST("/api/v1/brokers/safety/kill-switch") suspend fun triggerKillSwitch(): SafetyResponse
    @POST("/api/v1/execution/paper/entry") suspend fun paperEntry(@Body request: PaperEntryRequest): PaperEntryResponse
    @POST("/api/v1/execution/paper/from-scanner") suspend fun paperEntryFromScanner(@Body request: ScannerPaperEntryRequest): ScannerPaperEntryResponse
    @GET("/api/v1/execution/paper/position") suspend fun paperPosition(): PaperPositionResponse
    @POST("/api/v1/execution/paper/exit") suspend fun paperExit(@Body request: PaperExitRequest): PaperExitResponse
    @GET("/api/v1/execution/paper/orders") suspend fun paperOrders(): PaperOrdersResponse
    @GET("/api/v1/market-data/ltp-by-symbol") suspend fun ltpBySymbol(@Query("tradingsymbol") tradingSymbol: String, @Query("exchange") exchange: String = "NSE"): MarketLtpResponse
    @GET("/api/v1/scanner/cash-future/live/auto") suspend fun cashFutureScan(): CashFutureScanResponse
    @POST("/api/v1/scanner/cash-future/backtest/full/jobs") suspend fun startFullFnoJob(@Query("days") days: Int = 365, @Query("min_entry_gap") minEntryGap: Double = 0.0, @Query("exit_gap") exitGap: Double = 0.0, @Query("charges_per_trade") chargesPerTrade: Double = 0.0, @Query("funding_cost_per_trade") fundingCostPerTrade: Double = 0.0, @Query("max_holding_days") maxHoldingDays: Int = 30, @Query("future_selection") futureSelection: String = "BOTH"): FullFnoJobAcceptedResponse
    @GET("/api/v1/scanner/cash-future/backtest/jobs/{job_id}") suspend fun fullFnoJob(@Path("job_id") jobId: String): FullFnoJobStatusResponse
    @GET("/api/v1/scanner/cash-future/backtest/jobs/{job_id}/results") suspend fun fullFnoResults(@Path("job_id") jobId: String, @Query("limit") limit: Int = 50, @Query("after_sequence") afterSequence: Int? = null): FullFnoResultsPage
    @DELETE("/api/v1/scanner/cash-future/backtest/jobs/{job_id}") suspend fun cancelFullFnoJob(@Path("job_id") jobId: String): FullFnoJobControlResponse
    @DELETE("/api/v1/scanner/cash-future/backtest/jobs/{job_id}/results") suspend fun purgeFullFnoResults(@Path("job_id") jobId: String): FullFnoPurgeResponse
}

object ApiService {
    private const val PREFS = "algo_trading_session"
    private const val TOKEN = "access_token"
    fun saveToken(context: Context, token: String) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(TOKEN, token).apply()
    fun getToken(context: Context): String? = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(TOKEN, null)
    fun clearToken(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(TOKEN).apply()
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            val request = chain.request()
            val path = request.url.encodedPath
            val publicAuthEndpoint = path == "/api/v1/auth/login" || path == "/api/v1/auth/register"
            val token = AppContextHolder.context?.let { getToken(it) }
            val authenticated = if (publicAuthEndpoint || token.isNullOrBlank()) request else request.newBuilder().addHeader("Authorization", "Bearer $token").build()
            val response = chain.proceed(authenticated)
            if (response.code == 401 && !publicAuthEndpoint) {
                AppContextHolder.context?.let { clearToken(it) }
            }
            response
        }).connectTimeout(10, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).writeTimeout(30, TimeUnit.SECONDS).build()
    }
    val retrofitService: ApiInterface by lazy { Retrofit.Builder().client(httpClient).addConverterFactory(GsonConverterFactory.create()).baseUrl(BuildConfig.BACKEND_BASE_URL).build().create(ApiInterface::class.java) }
}

object AppContextHolder { var context: Context? = null }
