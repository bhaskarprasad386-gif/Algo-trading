package com.algotrading.app

import okhttp3.OkHttpClient
import retrofit2.Retrofit
import com.google.gson.annotations.SerializedName
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

data class MarketStatus(val status: String, val message: String)
data class MarketOverviewRow(
    val exchange: String = "", val symbol: String = "", val token: String = "",
    val ltp: Double? = null, val open: Double? = null, val high: Double? = null, val low: Double? = null,
    val close: Double? = null, val change_percent: Double? = null, val volume: Double? = null,
    val oi: Double? = null, val bid: Double? = null, val ask: Double? = null, val status: String = ""
)
data class LiveDataHealthResponse(
    val status: String = "",
    val market_session: String = "",
    val checked_at: String = "",
    val persisted: Boolean = false,
    val source: String = "",
    val timeframe: String = "",
    val records: Long = 0,
    val instruments: Int = 0,
    val latest_timestamp_ns: Long? = null,
    val age_seconds: Double? = null,
    val feed_status: String = "",
    val live_orders: String = "OFF"
)

data class MarketOverviewResponse(
    val status: String = "", val mode: String = "", val indices: List<MarketOverviewRow> = emptyList(),
    val commodities: List<MarketOverviewRow> = emptyList(),
    val errors: List<Map<String, Any?>> = emptyList()
)
data class AppUpdateInfo(val platform: String = "android", val version_code: Int = 1, val version_name: String = "1.0", val release_notes: String = "", val apk_url: String = "", val sha256: String = "", val mandatory: Boolean = false)
data class StrategyRegistryItem(
    val id: String = "", val name: String = "", val version: String = "1",
    val enabled: Boolean = true, val screen: String = "backend",
    val data_mode: String = "", val execution_mode: String = "SCANNER ONLY",
    val live_orders: Boolean = false, val capabilities: List<String> = emptyList(),
    val live_route: String? = null, val workspace_route: String? = null
)
data class StrategyWorkspaceResponse(val status: String = "", val workspace: StrategyRegistryItem = StrategyRegistryItem())
data class StrategyRegistryResponse(val strategies: List<StrategyRegistryItem> = emptyList())
data class MarketLtpResponse(val status: Boolean = false, val exchange: String = "", val tradingsymbol: String = "", val symboltoken: String = "", val ltp: Double? = null)
data class RealOwnerLoginRequest(val password: String)
data class RealOwnerLoginResponse(val access_token: String = "", val token_type: String = "bearer", val expires_in: Long = 0L)
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
    @GET("/api/v1/app/update") suspend fun appUpdate(): AppUpdateInfo
    @GET("/api/v1/app/strategies") suspend fun appStrategies(): StrategyRegistryResponse
    @GET("/api/v1/app/strategies/{strategy_id}/workspace") suspend fun strategyWorkspace(@retrofit2.http.Path("strategy_id") strategyId: String): StrategyWorkspaceResponse
    @POST("/api/v1/brokers/owner-auth/login") suspend fun loginRealOwner(@Body request: RealOwnerLoginRequest): RealOwnerLoginResponse
    @GET("/api/v1/brokers/connections") suspend fun brokerConnections(): BrokerConnectionsResponse
    @POST("/api/v1/brokers/connect") suspend fun connectBroker(@Body request: BrokerConnectRequest): BrokerConnectResponse
    @GET("/api/v1/brokers/{broker}/status") suspend fun brokerStatus(@Path("broker") broker: String): BrokerStatusResponse
    @DELETE("/api/v1/brokers/{broker}") suspend fun disconnectBroker(@Path("broker") broker: String): BrokerStatusResponse
    @GET("/api/v1/brokers/safety") suspend fun safetyStatus(): SafetyResponse
    @POST("/api/v1/brokers/safety/enable") suspend fun enableRealTrading(@Body request: RealTradingEnableRequest): SafetyResponse
    @POST("/api/v1/brokers/safety/disable") suspend fun disableRealTrading(): SafetyResponse
    @POST("/api/v1/brokers/safety/kill-switch") suspend fun triggerKillSwitch(): SafetyResponse
    @GET("/api/v1/market-data/overview") suspend fun marketOverview(): MarketOverviewResponse
    suspend fun liveDataHealth(): LiveDataHealthResponse
    @GET("/api/v1/market-data/ltp-by-symbol") suspend fun ltpBySymbol(@Query("tradingsymbol") tradingSymbol: String, @Query("exchange") exchange: String = "NSE"): MarketLtpResponse
    @GET("/api/v1/backtesting/universal/runs/{run_id}") suspend fun universalRun(@Path("run_id") runId: String): Map<String, Any?>
    @GET("/api/v1/backtesting/universal/runs/{run_id}/equity") suspend fun universalEquityPage(@Path("run_id") runId: String, @Query("limit") limit: Int = 100, @Query("after_timestamp_ns") afterTimestampNs: Long? = null, @Query("after_equity_id") afterEquityId: Long? = null): Map<String, Any?>
    @GET("/api/v1/backtesting/universal/runs/{run_id}/events") suspend fun universalEvents(@Path("run_id") runId: String, @Query("limit") limit: Int = 100, @Query("after_sequence") afterSequence: Int = -1): Map<String, Any?>
    @GET("/api/v1/backtesting/universal/runs/{run_id}/fills") suspend fun universalFills(@Path("run_id") runId: String, @Query("limit") limit: Int = 100, @Query("after_sequence") afterSequence: Int = -1): Map<String, Any?>
    @GET("/api/v1/backtesting/universal/runs/{run_id}/trades") suspend fun universalTrades(@Path("run_id") runId: String, @Query("limit") limit: Int = 100, @Query("after_sequence") afterSequence: Int = -1): Map<String, Any?>
    @GET("/api/v1/backtesting/universal/runs/{run_id}/equity") suspend fun universalEquity(@Path("run_id") runId: String, @Query("limit") limit: Int = 100): Map<String, Any?>
    @POST("/api/v1/backtesting/full-fno/start") suspend fun startFullFnoJob(@Body request: FullFnoJobRequest = FullFnoJobRequest()): FullFnoJobAcceptedResponse
    @GET("/api/v1/backtesting/full-fno/{job_id}") suspend fun fullFnoJob(@Path("job_id") jobId: String): FullFnoJobStatusResponse
    @POST("/api/v1/backtesting/full-fno/{job_id}/cancel") suspend fun cancelFullFnoJob(@Path("job_id") jobId: String): FullFnoJobControlResponse
    @GET("/api/v1/backtesting/full-fno/{job_id}/results") suspend fun fullFnoResults(@Path("job_id") jobId: String, @Query("limit") limit: Int = 50, @Query("after_sequence") afterSequence: Int? = null): FullFnoResultsPage
    @DELETE("/api/v1/backtesting/full-fno/{job_id}/results") suspend fun purgeFullFnoResults(@Path("job_id") jobId: String): FullFnoPurgeResponse
}

object ApiService {
    @Volatile private var realOwnerAccessToken: String? = null

    fun setRealOwnerAccessToken(token: String?) { realOwnerAccessToken = token?.takeIf { it.isNotBlank() } }
    fun hasRealOwnerAccessToken(): Boolean = !realOwnerAccessToken.isNullOrBlank()

    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .addInterceptor { chain ->
                val request = chain.request()
                val isBrokerApi = request.url.encodedPath.startsWith("/api/v1/brokers/") &&
                    !request.url.encodedPath.endsWith("/owner-auth/login")
                val token = realOwnerAccessToken
                val authenticated = if (isBrokerApi && !token.isNullOrBlank()) {
                    request.newBuilder().header("Authorization", "Bearer $token").build()
                } else request
                val response = chain.proceed(authenticated)
                if (isBrokerApi && response.code == 401) realOwnerAccessToken = null
                response
            }
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .build()
    }
    val retrofitService: ApiInterface by lazy {
        Retrofit.Builder()
            .client(httpClient)
            .addConverterFactory(GsonConverterFactory.create())
            .baseUrl(BuildConfig.BACKEND_BASE_URL)
            .build()
            .create(ApiInterface::class.java)
    }
}
