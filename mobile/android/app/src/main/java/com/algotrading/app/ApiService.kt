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
data class MarketLtpResponse(val status: Boolean = false, val exchange: String = "", val tradingsymbol: String = "", val symboltoken: String = "", val ltp: Double? = null)
data class CashFutureOpportunity(val symbol: String, val cash_price: Double = 0.0, val future_price: Double = 0.0, val gap: Double = 0.0, val gap_pct: Double = 0.0, val gross_spread_profit: Double = 0.0, val margin_required: Double = 0.0, val deployed_capital: Double = 0.0, val net_profit: Double = 0.0, val roi_pct: Double = 0.0, val executable: Boolean = false)
data class LiveCashFutureSignal(
    val symbol: String = "", val contract_month: String = "", val cash_ltp: Double = 0.0, val future_ltp: Double = 0.0,
    val cash_bid: Double? = null, val cash_ask: Double? = null, val future_bid: Double? = null, val future_ask: Double? = null,
    val cash_bid_qty: Double? = null, val cash_ask_qty: Double? = null, val future_bid_qty: Double? = null, val future_ask_qty: Double? = null,
    val liquidity_qty: Double? = null, val gap: Double = 0.0, val gap_pct: Double = 0.0, val timestamp_ns: Long = 0L,
    val cash_day_high: Double = 0.0, val cash_day_low: Double = 0.0, val future_day_high: Double = 0.0, val future_day_low: Double = 0.0,
    val lot_size: Int? = null, val gross_lot_value: Double? = null, val alert_lots: Int? = null,
    val gross_profit: Double? = null, val net_profit: Double? = null, val estimated_cost: Double = 0.0,
    val net_gap: Double = 0.0, val net_gap_pct: Double = 0.0, val annualized_gap_pct: Double? = null,
    val stable_observations: Int = 0, val capacity_lots: Int? = null, val capacity_notional: Double? = null,
    val lifecycle: String = "", val reason_codes: List<String> = emptyList(), val observation_ref: String = "", val alert_event: String? = null,
    val rank_score: Double = 0.0, val rank_factors: Map<String, Double?> = emptyMap(), val peer_contract_month: String? = null,
    val peer_gap_pct: Double? = null, val gap_pct_delta_vs_peer: Double? = null, val is_best_contract_month: Boolean = false
) {
    val executable: Boolean get() = lifecycle != "EXPIRED" && gap > 0.0 && net_gap > 0.0 && (cash_ask ?: cash_ltp) > 0.0 && (future_bid ?: future_ltp) > 0.0
    val roi_pct: Double get() = net_gap_pct
}
data class LiveCashFutureScanResponse(val status: String = "", val scanner: String = "", val mode: String = "", val data: List<LiveCashFutureSignal> = emptyList(), val errors: List<CashFutureScanError> = emptyList())
data class CashFutureScanError(val symbol: String = "", val error: String = "")
data class CashFutureScanResponse(val status: String, val scanner: String, val mode: String, val symbols_requested: List<String> = emptyList(), val scanned_observations: Int = 0, val opportunity_count: Int = 0, val data: List<CashFutureOpportunity> = emptyList(), val errors: List<CashFutureScanError> = emptyList())
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
data class DailyGapCalendarItem(val trading_date: String = "", val symbol: String = "", val direction: String = "FLAT", val gap: Double = 0.0, val gap_percent: Double = 0.0, val weighted_gap: Double = 0.0, val previous_close: Double = 0.0, val open: Double = 0.0, val high: Double = 0.0, val low: Double = 0.0, val close: Double = 0.0, val lot_size: Double = 0.0, val gap_high_timestamp: String? = null, val cash_price_at_gap_high: Double? = null, val future_price_at_gap_high: Double? = null, val expiry_date: String? = null, val is_expiry_day: Boolean = false, val margin_required: Double = 0.0, val charges: Double = 0.0, val funding_cost: Double = 0.0, val net_profit: Double = 0.0, val roi_pct: Double = 0.0)
data class DailyGapCalendarResponse(val status: String = "", val trading_date: String = "", val top: DailyGapCalendarItem? = null, val data: List<DailyGapCalendarItem> = emptyList())
data class MonthlyGapResult(val trading_date: String = "", val symbol: String = "", val gap: Double = 0.0, val gap_value: Double = 0.0, val open: Double = 0.0, val high: Double = 0.0, val low: Double = 0.0, val close: Double = 0.0, val lot_size: Double = 0.0, val previous_close: Double? = null, val contract_month: String? = null, val gap_high_timestamp: String? = null, val cash_price_at_gap_high: Double? = null, val future_price_at_gap_high: Double? = null, val expiry_date: String? = null, val is_expiry_day: Boolean = false, val margin_required: Double = 0.0, val charges: Double = 0.0, val funding_cost: Double = 0.0, val net_profit: Double = 0.0, val roi_pct: Double = 0.0)
data class MonthlyGapSearchResponse(val status: String = "", val month: String = "", val mode: String = "opening", val instrument_type: String = "STOCK", val result: MonthlyGapResult? = null)
data class MonthlyGapTop10Item(val rank: Int = 0, val symbol: String = "", val lot_size: Double = 0.0, val month_gap_high: Double = 0.0, val gap_value: Double = 0.0, val gap_high_date: String = "", val gap_high_time: String = "", val gap_high_timestamp: String = "", val cash_price_at_gap_high: Double = 0.0, val future_price_at_gap_high: Double = 0.0, val contract_month: String? = null, val instrument_key: String? = null, val expiry_date: String? = null, val is_expiry_day: Boolean = false, val margin_required: Double = 0.0, val charges: Double = 0.0, val funding_cost: Double = 0.0, val net_profit: Double = 0.0, val roi_pct: Double = 0.0)
data class MonthlyGapTop10Response(val status: String = "", val month: String = "", val mode: String = "shorting", val instrument_type: String = "STOCK", val count: Int = 0, val data: List<MonthlyGapTop10Item> = emptyList())
data class MonthlyGraphPoint(val trading_date: String = "", val open: Double = 0.0, val high: Double = 0.0, val low: Double = 0.0, val close: Double = 0.0, val lot_size: Double = 0.0, val contract_month: String? = null)
data class MonthlyGraphResponse(val status: String = "", val symbol: String = "", val month: String = "", val instrument_type: String = "STOCK", val contract_month: String? = null, val count: Int = 0, val series: List<MonthlyGraphPoint> = emptyList())
data class IntradayReplayPoint(val timestamp: String = "", val open: Double = 0.0, val high: Double = 0.0, val low: Double = 0.0, val volume: Double? = null, val oi: Double? = null, val lot_size: Double = 0.0, val contract_month: String? = null, val instrument_key: String? = null)
data class CashFutureReplayPoint(val timestamp: String = "", val cash_price: Double = 0.0, val future_price: Double = 0.0, val gap: Double = 0.0, val gap_pct: Double = 0.0, val contract_month: String? = null, val lot_size: Double = 0.0, val margin_required: Double = 0.0, val volume: Double? = null, val oi: Double? = null, val cash_bid: Double? = null, val cash_ask: Double? = null, val future_bid: Double? = null, val future_ask: Double? = null, val charges: Double? = null, val funding_cost: Double? = null)
data class CashFutureGraphPoint(val timestamp: String = "", val cash: Double = 0.0, val future: Double = 0.0, val gap: Double = 0.0, val gap_pct: Double = 0.0, val contract_month: String? = null)

/** One durable historical backtest trade, mapped directly to graph entry/exit markers. */
data class CashFutureTradeMarker(
    val entry_time: String = "",
    val exit_time: String = "",
    val symbol: String = "",
    val contract_month: String = "",
    val lot_size: Double = 0.0,
    val quantity: Double = 0.0,
    val entry_cash_price: Double = 0.0,
    val entry_future_price: Double = 0.0,
    val entry_gap: Double = 0.0,
    val exit_cash_price: Double = 0.0,
    val exit_future_price: Double = 0.0,
    val exit_gap: Double = 0.0,
    val gross_profit: Double = 0.0,
    val charges: Double = 0.0,
    val funding_cost: Double = 0.0,
    val net_profit: Double = 0.0,
    val execution_model: String = "gap",
    val exit_reason: String = "",
    val reserved_margin: Double = 0.0,
)

data class CashFutureStrategyRunResponse(
    val status: String = "",
    val run_id: String = "",
    val strategy_id: String = "",
    val strategy_version: String = "1",
    val initial_capital: Double = 0.0,
    val final_capital: Double = 0.0,
    val final_available_capital: Double = 0.0,
    val final_reserved_margin: Double = 0.0,
    val blocked_entry_count: Int = 0,
    val net_profit: Double = 0.0,
    val signal_count: Int = 0,
    val trade_count: Int = 0,
    val signals: List<Map<String, Any?>> = emptyList(),
    val trades: List<CashFutureTradeMarker> = emptyList(),
    val equity_curve: List<Map<String, Any?>> = emptyList(),
    val analysis: Map<String, Any?> = emptyMap(),
)

data class CalendarSpreadInstrument(
    val underlying: String = "",
    val exchange: String = "",
    val instrument_type: String = "",
    val priority: Int = 9,
    val active_contract_count: Int = 0,
    val near_contract_month: String = "",
    val far_contract_month: String? = null,
    val near_symbol: String = "",
    val far_symbol: String? = null,
    val lot_size: Int = 0,
)
data class CalendarSpreadInstrumentUniverseResponse(
    val status: String = "",
    val as_of: String = "",
    val priority: String = "",
    val count: Int = 0,
    val instruments: List<CalendarSpreadInstrument> = emptyList(),
)

data class CalendarSpreadContractMonth(
    val contract_month: String = "",
    val symbol: String = "",
    val token: String = "",
    val expiry: String = "",
    val lot_size: Int = 0,
)
data class CalendarSpreadContractMonthsResponse(
    val status: String = "",
    val underlying: String = "",
    val as_of: String = "",
    val instrument_type: String = "",
    val exchange: String = "",
    val priority: String = "",
    val contracts: List<CalendarSpreadContractMonth> = emptyList(),
)

data class CalendarSpreadReplayPoint(
    val timestamp: String = "",
    val underlying: String = "",
    val near_expiry: String = "",
    val far_expiry: String = "",
    val near_bid: Double = 0.0,
    val near_ask: Double = 0.0,
    val far_bid: Double = 0.0,
    val far_ask: Double = 0.0,
    val lot_size: Double = 0.0,
    val strike: Double? = null,
    val option_type: String? = null,
)

data class CalendarSpreadHistoricalReplayResponse(
    val status: String = "",
    val underlying: String = "",
    val near_contract_month: String = "",
    val far_contract_month: String = "",
    val source_timeframe: String = "",
    val replay_timeframe: String = "",
    val source_min_interval_seconds: Int? = null,
    val available_replay_intervals: List<String> = emptyList(),
    val count: Int = 0,
    val series: List<CalendarSpreadReplayPoint> = emptyList(),
)

data class CalendarSpreadTradeMarker(
    val entry_time: String = "",
    val exit_time: String = "",
    val symbol: String = "",
    val lot_size: Double = 0.0,
    val quantity: Double = 0.0,
    val entry_price: Double = 0.0,
    val exit_price: Double = 0.0,
    val gross_profit: Double = 0.0,
    val fees: Double = 0.0,
    val net_profit: Double = 0.0,
)

data class CalendarSpreadStrategyRunResponse(
    val status: String = "",
    val run_id: String = "",
    val strategy_id: String = "",
    val strategy_version: String = "1",
    val direction: String = "",
    val start_timestamp: String = "",
    val end_timestamp: String = "",
    val completed_trades: Int = 0,
    val unresolved_trades: Int = 0,
    val net_profit: Double = 0.0,
    val trade_count: Int = 0,
    val trades: List<CalendarSpreadTradeMarker> = emptyList(),
)

data class CashFutureReplayResponse(val status: String = "", val trading_date: String = "", val symbol: String = "", val contract_month: String? = null, val contracts_seen: List<String> = emptyList(), val mode: String = "CURRENT", val timeframe: String = "1m", val source: String = "", val count: Int = 0, val first_timestamp: String = "", val last_timestamp: String = "", val source_min_interval_seconds: Int? = null, val available_replay_intervals: List<String> = emptyList(), val series: List<CashFutureReplayPoint> = emptyList(), val graph: List<CashFutureGraphPoint> = emptyList())
data class IntradayReplayResponse(val status: String = "", val trading_date: String = "", val symbol: String = "", val instrument_type: String = "CASH_FUTURE", val source_interval_minutes: Int = 1, val chart_interval_minutes: Int = 15, val count: Int = 0, val series: List<IntradayReplayPoint> = emptyList())
data class DateGapResponse(val status: String = "", val trading_date: String = "", val mode: String = "shorting", val instrument_type: String = "STOCK", val count: Int = 0, val top: DailyGapCalendarItem? = null, val data: List<DailyGapCalendarItem> = emptyList())
data class PriorGapComparisonResponse(val status: String = "", val trading_date: String = "", val mode: String = "shorting", val instrument_type: String = "STOCK", val symbol: String = "", val selected: DailyGapCalendarItem? = null, val has_larger_prior_gap: Boolean = false, val prior_larger: List<DailyGapCalendarItem> = emptyList())
data class CashFutureDownloadRequest(val spot_instrument: String, val exchange: String = "NFO", val underlying: String, val start: String, val end: String, val timeframe: String = "1m", val mode: String = "BOTH", val retry_attempts: Int = 3)
data class CashFutureDownloadAcceptedResponse(val job_id: String, val status: String)
data class CashFutureDownloadJob(val job_id: String = "", val source: String = "", val mode: String = "", val timeframe: String = "", val spot_instrument: String = "", val exchange: String = "", val underlying: String = "", val start_ns: Long = 0L, val end_ns: Long = 0L, val status: String = "", val requested_chunks: Int = 0, val completed_chunks: Int = 0, val skipped_chunks: Int = 0, val failed_chunks: Int = 0, val catalog_count: Int = 0, val fetched_records: Int = 0, val inserted_records: Int = 0, val updated_at_ns: Long = 0L, val error: String? = null)
data class CashFutureDownloadChunk(val sequence: Int = 0, val instrument: String = "", val status: String = "", val attempts: Int = 0, val expected_timestamps: Int = 0, val actual_timestamps: Int = 0, val missing_timestamps: Int = 0, val fetched_records: Int = 0, val inserted_records: Int = 0, val error: String? = null)
data class CashFutureDownloadStatusResponse(val job: CashFutureDownloadJob, val chunks: List<CashFutureDownloadChunk> = emptyList())
data class AppUpdateInfo(val platform: String = "android", val version_code: Int = 1, val version_name: String = "1.0", val release_notes: String = "", val apk_url: String = "", val sha256: String = "", val mandatory: Boolean = false)
data class StrategyRegistryItem(val id: String = "", val name: String = "", val version: String = "1", val enabled: Boolean = true, val screen: String = "backend", val data_mode: String = "", val execution_mode: String = "PAPER", val live_orders: Boolean = false, val capabilities: List<String> = emptyList(), val live_route: String? = null, val workspace_route: String? = null)
data class StrategyWorkspaceResponse(val status: String = "", val workspace: StrategyRegistryItem = StrategyRegistryItem())
data class StrategyRegistryResponse(val strategies: List<StrategyRegistryItem> = emptyList())
data class BoxSpreadOverview(
    val status: String = "", val strategy: String = "", val mode: String = "",
    val account: BoxSpreadAccount? = null, val open_position: BoxSpreadPositionSummary? = null,
    val live_opportunities: BoxSpreadLiveOpportunities = BoxSpreadLiveOpportunities(),
    val history: BoxSpreadHistoryPage = BoxSpreadHistoryPage(), val journal: BoxSpreadJournalPage = BoxSpreadJournalPage()
)

interface ApiInterface {
    @GET("/") suspend fun getRootStatus(): MarketStatus
    @GET("/api/v1/app/update") suspend fun appUpdate(): AppUpdateInfo
    @GET("/api/v1/app/strategies") suspend fun appStrategies(): StrategyRegistryResponse
    @GET("/api/v1/app/strategies/{strategy_id}/workspace") suspend fun strategyWorkspace(@retrofit2.http.Path("strategy_id") strategyId: String): StrategyWorkspaceResponse
    @GET("/api/v1/scanner/calendar-spread/live") suspend fun calendarSpreadLive(@Query("limit") limit: Int = 50): Map<String, Any?>
    @GET("/api/v1/scanner/synthetic-cash-carry/live") suspend fun syntheticCashCarryLive(@Query("limit") limit: Int = 50): Map<String, Any?>
    @GET("/api/v1/scanner/box-spread/live") suspend fun boxSpreadLive(@Query("limit") limit: Int = 50): Map<String, Any?>
    @GET("/api/v1/brokers/connections") suspend fun brokerConnections(): BrokerConnectionsResponse
    @POST("/api/v1/brokers/connect") suspend fun connectBroker(@Body request: BrokerConnectRequest): BrokerConnectResponse
    @GET("/api/v1/brokers/{broker}/status") suspend fun brokerStatus(@Path("broker") broker: String): BrokerStatusResponse
    @DELETE("/api/v1/brokers/{broker}") suspend fun disconnectBroker(@Path("broker") broker: String): BrokerStatusResponse
    @GET("/api/v1/brokers/safety") suspend fun safetyStatus(): SafetyResponse
    @POST("/api/v1/brokers/safety/enable") suspend fun enableRealTrading(@Body request: RealTradingEnableRequest): SafetyResponse
    @POST("/api/v1/brokers/safety/disable") suspend fun disableRealTrading(): SafetyResponse
    @POST("/api/v1/brokers/safety/kill-switch") suspend fun triggerKillSwitch(): SafetyResponse
    @GET("/api/v1/market-data/overview") suspend fun marketOverview(): MarketOverviewResponse
    @GET("/api/v1/market-data/live-cash-future/health") suspend fun liveCashFutureHealth(): Map<String, Any?>
    suspend fun liveDataHealth(): LiveDataHealthResponse
    @GET("/api/v1/market-data/ltp-by-symbol") suspend fun ltpBySymbol(@Query("tradingsymbol") tradingSymbol: String, @Query("exchange") exchange: String = "NSE"): MarketLtpResponse
    @GET("/api/v1/scanner/cash-future/live/fast") suspend fun liveCashFutureScan(@Query("max_age_seconds") maxAgeSeconds: Double = 5.0, @Query("limit") limit: Int = 50): LiveCashFutureScanResponse
    @GET("/api/v1/scanner/cash-future/live/auto") suspend fun cashFutureScan(): CashFutureScanResponse
    @GET("/api/v1/scanner/cash-future/calendar/{trading_date}/top-gap") suspend fun dailyGapCalendar(@Path("trading_date") tradingDate: String, @Query("limit") limit: Int = 10): DailyGapCalendarResponse
    @GET("/api/v1/backtesting/results/date-gap") suspend fun dateGapRanking(@Query("trading_date") tradingDate: String, @Query("mode") mode: String = "shorting", @Query("instrument_type") instrumentType: String = "STOCK", @Query("symbol") symbol: String? = null, @Query("contract_month") contractMonth: String? = null, @Query("limit") limit: Int = 200): DateGapResponse
    @GET("/api/v1/backtesting/results/prior-gap") suspend fun priorGapComparison(@Query("trading_date") tradingDate: String, @Query("mode") mode: String = "shorting", @Query("instrument_type") instrumentType: String = "STOCK", @Query("symbol") symbol: String? = null, @Query("contract_month") contractMonth: String? = null, @Query("limit") limit: Int = 5): PriorGapComparisonResponse
    @GET("/api/v1/backtesting/results/monthly-gap") suspend fun monthlyGapSearch(@Query("year") year: Int, @Query("month") month: Int, @Query("mode") mode: String = "opening", @Query("instrument_type") instrumentType: String = "STOCK", @Query("symbol") symbol: String? = null, @Query("contract_month") contractMonth: String? = null): MonthlyGapSearchResponse
    @GET("/api/v1/backtesting/results/monthly-gap-top10") suspend fun monthlyGapTop10(@Query("year") year: Int, @Query("month") month: Int, @Query("instrument_type") instrumentType: String = "STOCK", @Query("contract_month") contractMonth: String? = null): MonthlyGapTop10Response
    @GET("/api/v1/backtesting/results/monthly-graph") suspend fun monthlyGraph(@Query("year") year: Int, @Query("month") month: Int, @Query("symbol") symbol: String, @Query("instrument_type") instrumentType: String = "STOCK", @Query("contract_month") contractMonth: String? = null): MonthlyGraphResponse
    @POST("/api/v1/backtesting/calendar-spread/instruments")
    suspend fun calendarSpreadInstruments(@Body request: Map<String, Any?>): CalendarSpreadInstrumentUniverseResponse
    @POST("/api/v1/backtesting/calendar-spread/contract-months")
    suspend fun calendarSpreadContractMonths(@Body request: Map<String, Any?>): CalendarSpreadContractMonthsResponse
    @POST("/api/v1/backtesting/calendar-spread/historical-replay")
    suspend fun calendarSpreadHistoricalReplay(@Body request: Map<String, Any?>): CalendarSpreadHistoricalReplayResponse
    @POST("/api/v1/backtesting/calendar-spread/historical-strategy-run")
    suspend fun calendarSpreadHistoricalStrategyRun(@Body request: Map<String, Any?>): CalendarSpreadStrategyRunResponse
    @GET("/api/v1/backtesting/cash-future/replay") suspend fun cashFutureReplay(@Query("trading_date") tradingDate: String, @Query("symbol") symbol: String, @Query("contract_month") contractMonth: String? = null, @Query("timeframe") timeframe: String = "1m", @Query("mode") mode: String = "CURRENT", @Query("source") source: String = "angelone", @Query("spot_instrument") spotInstrument: String? = null, @Query("exchange") exchange: String = "NSE"): CashFutureReplayResponse
    @GET("/api/v1/backtesting/cash-future/live-coverage") suspend fun cashFutureLiveCoverage(@Query("trading_date") tradingDate: String, @Query("symbol") symbol: String): Map<String, Any?>
    @GET("/api/v1/backtesting/cash-future/strategy-run/{run_id}") suspend fun cashFutureStrategyRun(@Path("run_id") runId: String): CashFutureStrategyRunResponse
    @GET("/api/v1/backtesting/cash-future/strategy-run/{run_id}/results/{record_type}") suspend fun cashFutureStrategyResultPage(@Path("run_id") runId: String, @Path("record_type") recordType: String, @Query("limit") limit: Int = 50, @Query("after_id") afterId: Int? = null): Map<String, Any?>
    @POST("/api/v1/backtesting/cash-future/strategy-run") suspend fun startCashFutureStrategyRun(@Body request: Map<String, Any?>): Map<String, Any?>
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
    @POST("/api/v1/backtesting/cash-future/downloads") suspend fun startCashFutureDownload(@Body request: CashFutureDownloadRequest): CashFutureDownloadAcceptedResponse
    @GET("/api/v1/backtesting/cash-future/downloads/{job_id}") suspend fun cashFutureDownloadStatus(@Path("job_id") jobId: String): CashFutureDownloadStatusResponse
    @POST("/api/v1/backtesting/cash-future/downloads/{job_id}/resume") suspend fun resumeCashFutureDownload(@Path("job_id") jobId: String, @Query("retry_attempts") retryAttempts: Int = 3): CashFutureDownloadAcceptedResponse
}

object ApiService {
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
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
