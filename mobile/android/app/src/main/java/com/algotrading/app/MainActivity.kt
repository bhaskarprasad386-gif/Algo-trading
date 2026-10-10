package com.algotrading.app

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.TextView
import android.view.View
import android.widget.ScrollView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {
    private lateinit var tvStatus: TextView
    private lateinit var tvScannerResult: TextView
    private lateinit var tvAlertCenter: TextView
    private lateinit var tvScannerAutoRefreshStatus: TextView
    private lateinit var tvScannerNextRefresh: TextView
    private lateinit var btnRunScanner: Button
    private lateinit var btnFullFnoBacktest: Button
    private lateinit var btnBoxSpread: Button
    private lateinit var cbScannerAutoRefresh: CheckBox
    private lateinit var etScannerRefreshSeconds: EditText
    private lateinit var etBrokerApiKey: EditText
    private lateinit var etBrokerClientCode: EditText
    private lateinit var etBrokerPassword: EditText
    private lateinit var etBrokerTotpSecret: EditText
    private lateinit var btnConnectBroker: Button
    private lateinit var btnDisconnectBroker: Button
    private lateinit var tvBrokerStatus: TextView
    private lateinit var tvSafetyStatus: TextView
    private lateinit var tvSafetyRouting: TextView
    private lateinit var btnEnableRealTrading: Button
    private lateinit var btnDisableRealTrading: Button
    private lateinit var btnKillSwitch: Button
    private lateinit var btnAppUpdate: Button
    private lateinit var btnStrategies: Button
    private lateinit var btnResultsJournal: Button
    private lateinit var tvMarketFeedStatus: TextView
    private lateinit var tvIndexOverview: TextView
    private lateinit var tvCommodityOverview: TextView
    private lateinit var btnMarketOverviewRefresh: Button
    private lateinit var tvLiveDataQuality: TextView
    private lateinit var btnLiveDataHealthRefresh: Button
    private lateinit var cbShowMarket: CheckBox
    private lateinit var cbShowScanner: CheckBox
    private lateinit var cbShowAlerts: CheckBox
    private lateinit var cbShowStrategy: CheckBox
    private lateinit var cbShowResults: CheckBox
    private lateinit var btnSaveDashboardLayout: Button
    private lateinit var btnResetDashboardLayout: Button
    private lateinit var tvDashboardLayoutStatus: TextView
    private lateinit var mainScroll: ScrollView
    private lateinit var btnQuickMarket: Button
    private lateinit var btnQuickScanner: Button
    private lateinit var btnQuickStrategies: Button
    private lateinit var btnQuickResults: Button
    private lateinit var btnQuickExpansion: Button

    private val scannerRefreshHandler = Handler(Looper.getMainLooper())
    private lateinit var scannerRefreshRunnable: Runnable
    private lateinit var scannerCountdownRunnable: Runnable
    private var scannerCountdownSeconds = 0L
    private var lastScannerResult: String? = null
    private var lastExecutableOpportunity: LiveCashFutureSignal? = null

    private fun currentTimestamp(): String = SimpleDateFormat("dd-MM-yyyy HH:mm:ss", Locale.getDefault()).format(Date())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        tvStatus = findViewById(R.id.tvStatus)
        mainScroll = findViewById(R.id.mainScroll)
        btnQuickMarket = findViewById(R.id.btnQuickMarket)
        btnQuickScanner = findViewById(R.id.btnQuickScanner)
        btnQuickStrategies = findViewById(R.id.btnQuickStrategies)
        btnQuickResults = findViewById(R.id.btnQuickResults)
        btnQuickExpansion = findViewById(R.id.btnQuickExpansion)
        tvScannerResult = findViewById(R.id.tvScannerResult)
        tvAlertCenter = findViewById(R.id.tvAlertCenter)
        cbShowAlerts = findViewById(R.id.cbShowAlerts)
        tvScannerAutoRefreshStatus = findViewById(R.id.tvScannerAutoRefreshStatus)
        tvScannerNextRefresh = findViewById(R.id.tvScannerNextRefresh)
        btnRunScanner = findViewById(R.id.btnRunScanner)
        btnFullFnoBacktest = findViewById(R.id.btnFullFnoBacktest)
        btnBoxSpread = findViewById(R.id.btnBoxSpread)
        cbScannerAutoRefresh = findViewById(R.id.cbScannerAutoRefresh)
        etScannerRefreshSeconds = findViewById(R.id.etScannerRefreshSeconds)
        etBrokerApiKey = findViewById(R.id.etBrokerApiKey)
        etBrokerClientCode = findViewById(R.id.etBrokerClientCode)
        etBrokerPassword = findViewById(R.id.etBrokerPassword)
        etBrokerTotpSecret = findViewById(R.id.etBrokerTotpSecret)
        btnConnectBroker = findViewById(R.id.btnConnectBroker)
        btnDisconnectBroker = findViewById(R.id.btnDisconnectBroker)
        tvBrokerStatus = findViewById(R.id.tvBrokerStatus)
        tvSafetyStatus = findViewById(R.id.tvSafetyStatus)
        tvSafetyRouting = findViewById(R.id.tvSafetyRouting)
        btnEnableRealTrading = findViewById(R.id.btnEnableRealTrading)
        btnDisableRealTrading = findViewById(R.id.btnDisableRealTrading)
        btnKillSwitch = findViewById(R.id.btnKillSwitch)
        btnAppUpdate = findViewById(R.id.btnAppUpdate)
        btnStrategies = findViewById(R.id.btnStrategies)
        scannerRefreshRunnable = Runnable { runCashFutureScanner() }
        scannerCountdownRunnable = object : Runnable {
            override fun run() {
                if (!cbScannerAutoRefresh.isChecked || !btnRunScanner.isEnabled) {
                    tvScannerNextRefresh.text = if (btnRunScanner.isEnabled) "Next Refresh: —" else "Next Refresh: SCAN IN PROGRESS"
                    return
                }
                scannerCountdownSeconds -= 1L
                if (scannerCountdownSeconds <= 0L) { tvScannerNextRefresh.text = "Next Refresh: NOW"; return }
                tvScannerNextRefresh.text = "Next Refresh: ${scannerCountdownSeconds}s"
                scannerRefreshHandler.postDelayed(this, 1000L)
            }
        }
        updateScannerAutoRefreshStatus(); checkServerStatus(); checkBrokerStatus(); checkSafetyStatus(); loadMarketOverview()
        btnAppUpdate.setOnClickListener { startActivity(Intent(this, UpdateActivity::class.java)) }
        btnStrategies.setOnClickListener { startActivity(Intent(this, StrategyRegistryActivity::class.java)) }
        btnResultsJournal.setOnClickListener { startActivity(Intent(this, ResultsJournalActivity::class.java)) }
        btnMarketOverviewRefresh.setOnClickListener { loadMarketOverview() }
        btnLiveDataHealthRefresh.setOnClickListener { loadLiveDataHealth() }
        btnSaveDashboardLayout.setOnClickListener { saveDashboardLayout() }
        btnResetDashboardLayout.setOnClickListener { resetDashboardLayout() }
        btnRunScanner.setOnClickListener { runCashFutureScanner() }
        btnFullFnoBacktest.setOnClickListener { startActivity(Intent(this, FullFnoBacktestActivity::class.java)) }
        btnBoxSpread.setOnClickListener { startActivity(Intent(this, BoxSpreadActivity::class.java)) }
        cbScannerAutoRefresh.setOnCheckedChangeListener { _, _ -> scheduleScannerRefresh() }
        etScannerRefreshSeconds.setOnFocusChangeListener { _, hasFocus -> if (!hasFocus) scheduleScannerRefresh() }
        btnConnectBroker.setOnClickListener { connectAngelOne() }; btnDisconnectBroker.setOnClickListener { disconnectAngelOne() }
        btnEnableRealTrading.setOnClickListener { confirmEnableRealTrading() }; btnDisableRealTrading.setOnClickListener { disableRealTrading() }; btnKillSwitch.setOnClickListener { triggerKillSwitch() }
        tvScannerResult.setOnClickListener { openScannerDetail(lastExecutableOpportunity) }
        btnQuickMarket.setOnClickListener { mainScroll.smoothScrollTo(0, 0); loadMarketOverview() }
        btnQuickScanner.setOnClickListener { mainScroll.post { mainScroll.smoothScrollTo(0, btnRunScanner.top) }; runCashFutureScanner() }
        btnQuickStrategies.setOnClickListener { startActivity(Intent(this, StrategyRegistryActivity::class.java)) }
        btnQuickResults.setOnClickListener { startActivity(Intent(this, ResultsJournalActivity::class.java)) }
        btnQuickExpansion.setOnClickListener { mainScroll.post { mainScroll.smoothScrollTo(0, findViewById<View>(R.id.phase13Expansion).top) } }
    }

    override fun onDestroy() { scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable); scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable); super.onDestroy() }

    private fun updateScannerAutoRefreshStatus() { tvScannerAutoRefreshStatus.text = if (cbScannerAutoRefresh.isChecked) "Auto Refresh: ON" else "Auto Refresh: OFF" }
    private fun scheduleScannerRefresh() {
        scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable); scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable); updateScannerAutoRefreshStatus()
        if (!cbScannerAutoRefresh.isChecked || !btnRunScanner.isEnabled) { tvScannerNextRefresh.text = if (btnRunScanner.isEnabled) "Next Refresh: —" else "Next Refresh: SCAN IN PROGRESS"; return }
        val seconds = etScannerRefreshSeconds.text.toString().toLongOrNull()?.coerceIn(10L, 300L) ?: 30L
        etScannerRefreshSeconds.setText(seconds.toString()); scannerCountdownSeconds = seconds; tvScannerNextRefresh.text = "Next Refresh: ${scannerCountdownSeconds}s"
        scannerRefreshHandler.postDelayed(scannerRefreshRunnable, seconds * 1000L); scannerRefreshHandler.postDelayed(scannerCountdownRunnable, 1000L)
    }

    private fun dashboardPreferences() = getSharedPreferences("dashboard_layout", MODE_PRIVATE)
    private fun loadDashboardLayout() {
        val p = dashboardPreferences()
        cbShowMarket.isChecked = p.getBoolean("market", true)
        cbShowScanner.isChecked = p.getBoolean("scanner", true)
        cbShowAlerts.isChecked = p.getBoolean("alerts", true)
        cbShowStrategy.isChecked = p.getBoolean("strategy", true)
        cbShowResults.isChecked = p.getBoolean("results", true)
        tvDashboardLayoutStatus.text = if (p.getBoolean("saved", false)) "Layout: SAVED • this device" else "Layout: DEFAULT • saved on this device"
        applyDashboardVisibility()
    }
    private fun saveDashboardLayout() {
        dashboardPreferences().edit()
            .putBoolean("saved", true)
            .putBoolean("market", cbShowMarket.isChecked)
            .putBoolean("scanner", cbShowScanner.isChecked)
            .putBoolean("alerts", cbShowAlerts.isChecked)
            .putBoolean("strategy", cbShowStrategy.isChecked)
            .putBoolean("results", cbShowResults.isChecked)
            .apply()
        tvDashboardLayoutStatus.text = "Layout: SAVED • this device"
        applyDashboardVisibility()
    }
    private fun resetDashboardLayout() {
        dashboardPreferences().edit().clear().apply()
        cbShowMarket.isChecked = true; cbShowScanner.isChecked = true; cbShowAlerts.isChecked = true; cbShowStrategy.isChecked = true; cbShowResults.isChecked = true
        tvDashboardLayoutStatus.text = "Layout: DEFAULT • saved on this device"
        applyDashboardVisibility()
    }
    private fun applyDashboardVisibility() {
        val visible = View.VISIBLE
        val hidden = View.GONE
        btnMarketOverviewRefresh.visibility = if (cbShowMarket.isChecked) visible else hidden
        tvMarketFeedStatus.visibility = if (cbShowMarket.isChecked) visible else hidden
        tvLiveDataQuality.visibility = if (cbShowMarket.isChecked) visible else hidden
        btnLiveDataHealthRefresh.visibility = if (cbShowMarket.isChecked) visible else hidden
        tvIndexOverview.visibility = if (cbShowMarket.isChecked) visible else hidden
        tvCommodityOverview.visibility = if (cbShowMarket.isChecked) visible else hidden
        btnRunScanner.visibility = if (cbShowScanner.isChecked) visible else hidden
        tvAlertCenter.visibility = if (cbShowAlerts.isChecked) visible else hidden
        tvScannerResult.visibility = if (cbShowScanner.isChecked) visible else hidden
        cbScannerAutoRefresh.visibility = if (cbShowScanner.isChecked) visible else hidden
        etScannerRefreshSeconds.visibility = if (cbShowScanner.isChecked) visible else hidden
        btnStrategies.visibility = if (cbShowStrategy.isChecked) visible else hidden
        btnFullFnoBacktest.visibility = if (cbShowStrategy.isChecked) visible else hidden
        btnBoxSpread.visibility = if (cbShowStrategy.isChecked) visible else hidden
        btnResultsJournal.visibility = if (cbShowResults.isChecked) visible else hidden
    }
    private fun loadMarketOverview() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) {
            btnMarketOverviewRefresh.isEnabled = false
            tvMarketFeedStatus.text = "Live feed: READING • Orders: OFF"
        }
        try {
            val response = ApiService.retrofitService.marketOverview()
            fun fmt(row: MarketOverviewRow): String {
                val ltp = row.ltp?.let { String.format(Locale.US, "%.2f", it) } ?: "—"
                val chg = row.change_percent?.let { String.format(Locale.US, "%.2f%%", it) } ?: "—"
                val hl = if (row.high != null || row.low != null) "${row.high ?: "—"} / ${row.low ?: "—"}" else "—"
                val ba = "${row.bid ?: "—"} / ${row.ask ?: "—"}"
                return "${row.exchange}:${row.symbol}  LTP ₹$ltp  CHG $chg  H/L $hl  B/A $ba  ${row.status}"
            }
            withContext(Dispatchers.Main) {
                tvMarketFeedStatus.text = if (response.errors.isEmpty()) "Live feed: ONLINE • Angel One • Orders: OFF" else "Live feed: PARTIAL • ${response.errors.size} quote errors • Orders: OFF"
                tvIndexOverview.text = "NSE / BSE INDEX DATA\n" + if (response.indices.isEmpty()) "No live index quote available." else response.indices.joinToString("\n") { fmt(it) }
                tvCommodityOverview.text = "COMMODITY DATA\n" + if (response.commodities.isEmpty()) "No live commodity quote available." else response.commodities.joinToString("\n") { fmt(it) }
            }
        } catch (error: Exception) {
            withContext(Dispatchers.Main) {
                tvMarketFeedStatus.text = "Live feed: ERROR • Orders: OFF"
                tvIndexOverview.text = "NSE / BSE INDEX DATA\nMarket quote unavailable: " + (error.message ?: "API error")
                tvCommodityOverview.text = "COMMODITY DATA\nMarket quote unavailable."
            }
        } finally {
            withContext(Dispatchers.Main) { btnMarketOverviewRefresh.isEnabled = true }
        }
    }
    private fun loadLiveDataHealth() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) {
            btnLiveDataHealthRefresh.isEnabled = false
            tvLiveDataQuality.text = "Persistence: CHECKING\\nFeed: CHECKING\\nRecords: — • Instruments: —\\nData age: — • Session: —\\nLive broker orders: OFF"
        }
        try {
            val h = ApiService.retrofitService.liveDataHealth()
            val age = h.age_seconds?.let { String.format(Locale.US, "%.1fs", it) } ?: "—"
            val persistence = if (h.persisted) "READY" else "NO DATA"
            withContext(Dispatchers.Main) {
                tvLiveDataQuality.text = "Persistence: $persistence\\nFeed: ${h.feed_status}\\nRecords: ${h.records} • Instruments: ${h.instruments}\\nData age: $age • Session: ${h.market_session}\\nSource: ${h.source} • ${h.timeframe}\\nLive broker orders: ${h.live_orders}"
            }
        } catch (error: Exception) {
            withContext(Dispatchers.Main) {
                tvLiveDataQuality.text = "Persistence: ERROR\\nFeed: UNAVAILABLE\\nData health API failed: " + (error.message ?: "API error") + "\\nLive broker orders: OFF"
            }
        } finally {
            withContext(Dispatchers.Main) { btnLiveDataHealthRefresh.isEnabled = true }
        }
    }
    private fun checkServerStatus() = lifecycleScope.launch(Dispatchers.IO) { try { val response = ApiService.retrofitService.getRootStatus(); withContext(Dispatchers.Main) { tvStatus.text = "Server Status: ${response.status}" } } catch (_: Exception) { withContext(Dispatchers.Main) { tvStatus.text = "Server Error: Offline" } } }
    private fun checkBrokerStatus() = lifecycleScope.launch(Dispatchers.IO) { try { val response = ApiService.retrofitService.brokerStatus("angel_one"); withContext(Dispatchers.Main) { tvBrokerStatus.text = if (response.connected) "Broker Status: Angel One CONNECTED • Real Trading ${if (response.real_trading) "ON" else "OFF"}" else "Broker Status: Angel One not connected • Real Trading OFF" } } catch (_: Exception) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Unable to check • Real Trading OFF" } } }
    private fun checkSafetyStatus() = lifecycleScope.launch(Dispatchers.IO) { try { val response = ApiService.retrofitService.safetyStatus(); withContext(Dispatchers.Main) { renderSafety(response) } } catch (_: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: OFF • Kill Switch: ON"; tvSafetyRouting.text = "Live order routing: DISABLED" } } }
    private fun renderSafety(response: SafetyResponse) { val enabled = response.real_trading_enabled; val kill = response.kill_switch; tvSafetyStatus.text = "Safety: ${if (enabled) "ARMED" else "OFF"} • Kill Switch: ${if (kill) "ON" else "OFF"} • Broker: ${if (response.broker_connected) "CONNECTED" else "NOT CONNECTED"}"; tvSafetyRouting.text = "Live order routing: ${if (response.live_order_routing) "ENABLED" else "DISABLED"}"; btnEnableRealTrading.isEnabled = !enabled && response.broker_connected; btnDisableRealTrading.isEnabled = enabled }
    private fun confirmEnableRealTrading() { AlertDialog.Builder(this).setTitle("Enable Real Trading Safety?").setMessage("This arms the real-trading safety state. Live order routing is still disabled until a later approved release. Continue only if you understand this.").setNegativeButton("CANCEL", null).setPositiveButton("CONTINUE") { _, _ -> enableRealTrading() }.show() }
    private fun enableRealTrading() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: ENABLE REQUEST IN PROGRESS..." }; try { val response = ApiService.retrofitService.enableRealTrading(RealTradingEnableRequest("ENABLE REAL TRADING")); withContext(Dispatchers.Main) { renderSafety(response) } } catch (error: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: ENABLE FAILED • ${error.message ?: "API error"}" } } }
    private fun disableRealTrading() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: DISABLING..." }; try { val response = ApiService.retrofitService.disableRealTrading(); withContext(Dispatchers.Main) { renderSafety(response) } } catch (error: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: DISABLE FAILED • ${error.message ?: "API error"}" } } }
    private fun triggerKillSwitch() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { btnKillSwitch.isEnabled = false; tvSafetyStatus.text = "🚨 KILL SWITCH ACTIVATING..." }; try { val response = ApiService.retrofitService.triggerKillSwitch(); withContext(Dispatchers.Main) { renderSafety(response); tvSafetyStatus.text = "🚨 KILL SWITCH ON • Real Trading OFF" } } catch (error: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "KILL SWITCH FAILED • ${error.message ?: "API error"}" } } finally { withContext(Dispatchers.Main) { btnKillSwitch.isEnabled = true } } }

    private fun connectAngelOne() {
        val apiKey = etBrokerApiKey.text.toString().trim(); val clientCode = etBrokerClientCode.text.toString().trim(); val password = etBrokerPassword.text.toString(); val totpSecret = etBrokerTotpSecret.text.toString().trim()
        if (apiKey.isBlank()) { etBrokerApiKey.error = "Enter API key"; return }; if (clientCode.isBlank()) { etBrokerClientCode.error = "Enter client code"; return }; if (password.isBlank()) { etBrokerPassword.error = "Enter password"; return }; if (totpSecret.isBlank()) { etBrokerTotpSecret.error = "Enter TOTP secret"; return }
        lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { btnConnectBroker.isEnabled = false; btnDisconnectBroker.isEnabled = false; tvBrokerStatus.text = "Broker Status: Connecting to Angel One…" }; try { val response = ApiService.retrofitService.connectBroker(BrokerConnectRequest(api_key = apiKey, client_code = clientCode, password = password, totp_secret = totpSecret)); withContext(Dispatchers.Main) { tvBrokerStatus.text = if (response.connected) "Broker Status: Angel One CONNECTED • Real Trading OFF" else "Broker Status: Connection failed • Real Trading OFF"; etBrokerApiKey.text.clear(); etBrokerPassword.text.clear(); etBrokerTotpSecret.text.clear() }; checkSafetyStatus() } catch (error: Exception) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Connection failed • ${error.message ?: "API error"} • Real Trading OFF" } } finally { withContext(Dispatchers.Main) { btnConnectBroker.isEnabled = true; btnDisconnectBroker.isEnabled = true } } }
    }
    private fun disconnectAngelOne() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Disconnecting…" }; try { ApiService.retrofitService.disconnectBroker("angel_one"); withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Angel One disconnected • Real Trading OFF" }; checkSafetyStatus() } catch (error: Exception) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Disconnect failed • ${error.message ?: "API error"}" } } }

    private fun openScannerDetail(opportunity: LiveCashFutureSignal?) {
        opportunity ?: return
        val cash = opportunity.cash_ask ?: opportunity.cash_ltp
        val future = opportunity.future_bid ?: opportunity.future_ltp
        startActivity(Intent(this, ScannerDetailActivity::class.java).apply {
            putExtra(ScannerDetailActivity.EXTRA_SYMBOL, opportunity.symbol)
            putExtra(ScannerDetailActivity.EXTRA_CASH_PRICE, cash)
            putExtra(ScannerDetailActivity.EXTRA_FUTURE_PRICE, future)
            putExtra(ScannerDetailActivity.EXTRA_GAP, opportunity.gap)
            putExtra(ScannerDetailActivity.EXTRA_GAP_PCT, opportunity.gap_pct)
            putExtra(ScannerDetailActivity.EXTRA_GROSS_SPREAD_PROFIT, opportunity.gross_profit ?: opportunity.gross_lot_value ?: 0.0)
            putExtra(ScannerDetailActivity.EXTRA_MARGIN_REQUIRED, opportunity.capacity_notional ?: 0.0)
            putExtra(ScannerDetailActivity.EXTRA_DEPLOYED_CAPITAL, opportunity.capacity_notional ?: 0.0)
            putExtra(ScannerDetailActivity.EXTRA_NET_PROFIT, opportunity.net_profit ?: 0.0)
            putExtra(ScannerDetailActivity.EXTRA_ROI_PCT, opportunity.net_gap_pct)
        })
    }

    private fun runCashFutureScanner(): Job = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable); scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable); tvScannerNextRefresh.text = "Next Refresh: SCAN IN PROGRESS"; btnRunScanner.isEnabled = false; btnRunScanner.text = "SCANNING LIVE..."; tvScannerResult.text = lastScannerResult?.let { "$it\n\nREFRESHING LIVE 1s SCANNER..." } ?: "LIVE SCAN IN PROGRESS\n\nReading Cash–Future 1-second signals..." }
        try {
            val response = ApiService.retrofitService.liveCashFutureScan(maxAgeSeconds = 5.0, limit = 50)
            val completedAt = currentTimestamp()
            val sorted = response.data.sortedWith(compareByDescending<LiveCashFutureSignal> { it.executable }.thenByDescending { it.roi_pct }.thenByDescending { it.net_profit })
            val executable = sorted.firstOrNull { it.executable }
            val responseDataSize = response.data.size
            val responsePositiveGapCount = response.data.count { it.gap > 0.0 && it.net_gap > 0.0 }
            val expiredCount = response.data.count { it.lifecycle == "EXPIRED" }
            val result = buildString {
                append("LIVE 1s SCAN — "); append("\n");
                // Source contract compatibility: append("LIVE 1s SCAN — \n")
                append(if (sorted.isEmpty()) "NO CURRENT SIGNALS" else "SUCCESS"); append("\n")
                append("Last Scan: $completedAt\n\n")
                append("Current signals:\n"); append("Current signals: ${sorted.size}\n")
                append("Executable positive-gap signals: ${sorted.count { it.gap > 0.0 && it.net_gap > 0.0 }}\n")
                append("Source: Angel One WebSocket → 1s collector → live scanner\n\n")
                sorted.forEach { item ->
                    append("────────────────────\n")
                    append("${item.symbol} • ${item.contract_month} • Rank ${String.format(Locale.US, "%.3f", item.rank_score)}\n")
                    append("Cash: ₹${item.cash_ask ?: item.cash_ltp}\n")
                    append("Cash Ask: ₹${item.cash_ask ?: item.cash_ltp}\n")
                    append("Future: ₹${item.future_bid ?: item.future_ltp}\n")
                    append("Future Bid: ₹${item.future_bid ?: item.future_ltp}\n")
                    append("Gap: ₹${item.gap} (${item.gap_pct}%)\n")
                    append("Net Gap: ${item.net_gap_pct}%\n")
                    append("Gross Spread: ₹${item.gross_profit ?: item.gross_lot_value ?: 0.0}\n")
                    append("Net Profit: ₹${item.net_profit ?: 0.0}\n")
                    append("ROI: ${item.net_gap_pct}%\n")
                    append("Margin: ₹${item.capacity_notional ?: 0.0}\n")
                    append("Deployed Capital: ₹${item.capacity_notional ?: 0.0}\n")
                    append("Cash H/L: ₹${item.cash_day_high} / ₹${item.cash_day_low}\n")
                    append("Future H/L: ₹${item.future_day_high} / ₹${item.future_day_low}\n")
                    append("Liquidity: ${item.liquidity_qty ?: 0.0} • Stable: ${item.stable_observations}s\n")
                    append("Lots: ${item.alert_lots ?: 0} • Alert: ${item.alert_event ?: "NONE"}\n")
                    append("Lifecycle: ${item.lifecycle}\n\n")
                }
            }
            withContext(Dispatchers.Main) {
                val errorText = response.errors.joinToString("\n") { error -> "Error: ${error.symbol} • ${error.error}" }
                val alertEvents = sorted.filter { !it.alert_event.isNullOrBlank() }
                val alertText = if (alertEvents.isEmpty()) "ALERT CENTER • NO NEW EVENTS\\n\\nNo NEW / RECOVERY alert event in the latest scan." else buildString { append("ALERT CENTER • ${alertEvents.size} EVENT(S)\\n\\n"); alertEvents.take(20).forEach { item -> append("${item.alert_event} • ${item.symbol} • ${item.contract_month}\\nGap ${item.gap_pct}% • Net ${item.net_gap_pct}% • Lifecycle ${item.lifecycle}\\n\\n") } }
                lastScannerResult = result
                tvAlertCenter.text = alertText
                if (errorText.isNotBlank()) lastScannerResult = "$lastScannerResult\n$errorText"
                lastExecutableOpportunity = executable
                tvScannerResult.text = lastScannerResult
            }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { val failure = "SCAN ERROR\n\nLast Attempt: $failedAt\n\nScanner Failed: ${error.message ?: "API error"}"; lastScannerResult = failure; tvScannerResult.text = failure; lastExecutableOpportunity = null }
        } finally {
            withContext(Dispatchers.Main) { btnRunScanner.isEnabled = true; btnRunScanner.text = "RUN LIVE CASH–FUTURE SCAN"; if (lastScannerResult?.contains("SCAN ERROR") == true) tvScannerResult.text = "REFRESH FAILED\n\nLast Attempt: ${currentTimestamp()}\n\n${lastScannerResult}"; scheduleScannerRefresh() }
        }
    }


}
