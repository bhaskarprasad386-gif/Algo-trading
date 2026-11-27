package com.algotrading.app

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.text.InputType
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

    private var ownerLoginPromptShown = false

    private fun currentTimestamp(): String = SimpleDateFormat("dd-MM-yyyy HH:mm:ss", Locale.getDefault()).format(Date())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ApiService.initializeOwnerSession(applicationContext)
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
        disableCashFutureScanner(); checkServerStatus(); checkBrokerStatus(); checkSafetyStatus(); loadMarketOverview()
        btnAppUpdate.setOnClickListener { startActivity(Intent(this, UpdateActivity::class.java)) }
        btnStrategies.setOnClickListener { startActivity(Intent(this, StrategyRegistryActivity::class.java)) }
        btnResultsJournal.setOnClickListener { startActivity(Intent(this, ResultsJournalActivity::class.java)) }
        btnMarketOverviewRefresh.setOnClickListener { loadMarketOverview() }
        btnLiveDataHealthRefresh.setOnClickListener { loadLiveDataHealth() }
        btnSaveDashboardLayout.setOnClickListener { saveDashboardLayout() }
        btnResetDashboardLayout.setOnClickListener { resetDashboardLayout() }
        btnRunScanner.setOnClickListener { disableCashFutureScanner() }
        btnFullFnoBacktest.setOnClickListener { startActivity(Intent(this, FullFnoBacktestActivity::class.java)) }
        btnBoxSpread.setOnClickListener { startActivity(Intent(this, BoxSpreadActivity::class.java)) }
        
        btnConnectBroker.setOnClickListener { connectAngelOne() }; btnDisconnectBroker.setOnClickListener { disconnectAngelOne() }
        btnEnableRealTrading.setOnClickListener { confirmEnableRealTrading() }; btnDisableRealTrading.setOnClickListener { disableRealTrading() }; btnKillSwitch.setOnClickListener { triggerKillSwitch() }
        
        btnQuickMarket.setOnClickListener { mainScroll.smoothScrollTo(0, 0); loadMarketOverview() }
        btnQuickScanner.setOnClickListener { mainScroll.post { mainScroll.smoothScrollTo(0, btnRunScanner.top) } }
        btnQuickStrategies.setOnClickListener { startActivity(Intent(this, StrategyRegistryActivity::class.java)) }
        btnQuickResults.setOnClickListener { startActivity(Intent(this, ResultsJournalActivity::class.java)) }
        btnQuickExpansion.setOnClickListener { mainScroll.post { mainScroll.smoothScrollTo(0, findViewById<View>(R.id.phase13Expansion).top) } }
    }

    override fun onDestroy() { super.onDestroy() }

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
    private fun checkBrokerStatus() = lifecycleScope.launch(Dispatchers.IO) { try { val response = ApiService.retrofitService.brokerStatus("angel_one"); withContext(Dispatchers.Main) { tvBrokerStatus.text = if (response.connected) "Broker Status: Angel One CONNECTED • Real Trading ${if (response.real_trading) "ON" else "OFF"}" else "Broker Status: Angel One not connected • Real Trading OFF" } } catch (_: Exception) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Owner login required • Real Trading OFF"; if (!ApiService.hasRealOwnerAccessToken()) promptRealOwnerLogin() } } }

    private fun promptRealOwnerLogin() {
        if (ownerLoginPromptShown || ApiService.hasRealOwnerAccessToken() || isFinishing) return
        ownerLoginPromptShown = true
        val passwordInput = EditText(this).apply {
            hint = "Box Spread owner password"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        AlertDialog.Builder(this)
            .setTitle("Box Spread Owner Login")
            .setMessage("Enter the dedicated real-owner password. This is separate from your broker password.")
            .setView(passwordInput)
            .setNegativeButton("LATER") { _, _ -> ownerLoginPromptShown = false }
            .setPositiveButton("LOGIN") { _, _ ->
                val password = passwordInput.text.toString()
                lifecycleScope.launch(Dispatchers.IO) {
                    try {
                        val response = ApiService.retrofitService.loginRealOwner(RealOwnerLoginRequest(password))
                        if (response.access_token.isBlank()) throw IllegalStateException("Server returned no owner token")
                        ApiService.setRealOwnerAccessToken(response.access_token, response.expires_in)
                        withContext(Dispatchers.Main) {
                            ownerLoginPromptShown = false
                            tvBrokerStatus.text = "Broker Status: Owner authenticated • Real Trading OFF"
                            checkBrokerStatus()
                            checkSafetyStatus()
                        }
                    } catch (error: Exception) {
                        withContext(Dispatchers.Main) {
                            ownerLoginPromptShown = false
                            tvBrokerStatus.text = "Broker Status: Owner login failed • Real Trading OFF"
                            AlertDialog.Builder(this@MainActivity)
                                .setTitle("Owner Login Failed")
                                .setMessage(error.message ?: "Authentication failed. Check server configuration and try again.")
                                .setPositiveButton("OK", null)
                                .show()
                        }
                    }
                }
            }
            .setOnCancelListener { ownerLoginPromptShown = false }
            .show()
    }
    private fun checkSafetyStatus() = lifecycleScope.launch(Dispatchers.IO) { try { val response = ApiService.retrofitService.safetyStatus(); withContext(Dispatchers.Main) { renderSafety(response) } } catch (_: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: OFF • Kill Switch: ON\nReal-trading arming unavailable: owner authentication and durable account ownership are required."; tvSafetyRouting.text = "Live order routing: DISABLED"; btnEnableRealTrading.isEnabled = false; btnEnableRealTrading.text = "REAL TRADING ARMING UNAVAILABLE"; if (!ApiService.hasRealOwnerAccessToken()) promptRealOwnerLogin() } } }
    private fun renderSafety(response: SafetyResponse) { val enabled = response.real_trading_enabled; val kill = response.kill_switch; tvSafetyStatus.text = "Safety: ${if (enabled) "ARMED" else "OFF"} • Kill Switch: ${if (kill) "ON" else "OFF"} • Broker: ${if (response.broker_connected) "CONNECTED" else "NOT CONNECTED"}\nReal-trading arming unavailable: owner authentication and durable account ownership are required."; tvSafetyRouting.text = "Live order routing: ${if (response.live_order_routing) "ENABLED" else "DISABLED"}"; btnEnableRealTrading.isEnabled = false; btnEnableRealTrading.text = "REAL TRADING ARMING UNAVAILABLE"; btnDisableRealTrading.isEnabled = enabled }
    private fun confirmEnableRealTrading() { AlertDialog.Builder(this).setTitle("Enable Real Trading Safety?").setMessage("This arms the real-trading safety state. Live order routing is still disabled until a later approved release. Continue only if you understand this.").setNegativeButton("CANCEL", null).setPositiveButton("CONTINUE") { _, _ -> enableRealTrading() }.show() }
    private fun enableRealTrading() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: ENABLE REQUEST IN PROGRESS..." }; try { val response = ApiService.retrofitService.enableRealTrading(RealTradingEnableRequest("ENABLE REAL TRADING")); withContext(Dispatchers.Main) { renderSafety(response) } } catch (error: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: ENABLE FAILED • ${error.message ?: "API error"}" } } }
    private fun disableRealTrading() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: DISABLING..." }; try { val response = ApiService.retrofitService.disableRealTrading(); withContext(Dispatchers.Main) { renderSafety(response) } } catch (error: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "Safety: DISABLE FAILED • ${error.message ?: "API error"}" } } }
    private fun triggerKillSwitch() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { btnKillSwitch.isEnabled = false; tvSafetyStatus.text = "🚨 KILL SWITCH ACTIVATING..." }; try { val response = ApiService.retrofitService.triggerKillSwitch(); withContext(Dispatchers.Main) { renderSafety(response); tvSafetyStatus.text = "🚨 KILL SWITCH ON • Real Trading OFF" } } catch (error: Exception) { withContext(Dispatchers.Main) { tvSafetyStatus.text = "KILL SWITCH FAILED • ${error.message ?: "API error"}" } } finally { withContext(Dispatchers.Main) { btnKillSwitch.isEnabled = true } } }

    private fun connectAngelOne() {
        val apiKey = etBrokerApiKey.text.toString().trim(); val clientCode = etBrokerClientCode.text.toString().trim(); val password = etBrokerPassword.text.toString(); val totpSecret = etBrokerTotpSecret.text.toString().trim()
        if (apiKey.isBlank()) { etBrokerApiKey.error = "Enter API key"; return }; if (clientCode.isBlank()) { etBrokerClientCode.error = "Enter client code"; return }; if (password.isBlank()) { etBrokerPassword.error = "Enter password"; return }; if (totpSecret.isBlank()) { etBrokerTotpSecret.error = "Enter TOTP secret"; return }
        lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { btnConnectBroker.isEnabled = false; btnDisconnectBroker.isEnabled = false; tvBrokerStatus.text = "Broker Status: Connecting to Angel One…" }; try { val response = ApiService.retrofitService.connectBroker(BrokerConnectRequest(api_key = apiKey, client_code = clientCode, password = password, totp_secret = totpSecret)); withContext(Dispatchers.Main) { tvBrokerStatus.text = if (response.connected) "Broker Status: Angel One CONNECTED • Real Trading OFF" else "Broker Status: Connection failed • Real Trading OFF"; etBrokerApiKey.text.clear(); etBrokerPassword.text.clear(); etBrokerTotpSecret.text.clear() }; checkSafetyStatus() } catch (error: Exception) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Connection failed • ${error.message ?: "API error"} • Real Trading OFF"; if (!ApiService.hasRealOwnerAccessToken()) promptRealOwnerLogin() } } finally { withContext(Dispatchers.Main) { btnConnectBroker.isEnabled = true; btnDisconnectBroker.isEnabled = true } } }
    }
    private fun disconnectAngelOne() = lifecycleScope.launch(Dispatchers.IO) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Disconnecting…" }; try { ApiService.retrofitService.disconnectBroker("angel_one"); withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Angel One disconnected • Real Trading OFF" }; checkSafetyStatus() } catch (error: Exception) { withContext(Dispatchers.Main) { tvBrokerStatus.text = "Broker Status: Disconnect failed • ${error.message ?: "API error"}"; if (!ApiService.hasRealOwnerAccessToken()) promptRealOwnerLogin() } } }

    private fun disableCashFutureScanner() {
        cbScannerAutoRefresh.isChecked = false
        cbScannerAutoRefresh.isEnabled = false
        etScannerRefreshSeconds.isEnabled = false
        btnRunScanner.isEnabled = false
        btnRunScanner.text = "CASH-FUTURE SCANNER DISABLED"
        tvScannerAutoRefreshStatus.text = "Auto Refresh: DISABLED"
        tvScannerNextRefresh.text = "Next Refresh: DISABLED"
        tvScannerResult.text = "Cash-Future live scanning is disabled. No scanner API request will be sent."
        tvAlertCenter.text = "Cash-Future scanner alerts are disabled."
    }

}
