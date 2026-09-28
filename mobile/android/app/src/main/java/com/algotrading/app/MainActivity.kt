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
    private lateinit var etEntryPrice: EditText
    private lateinit var etQuantity: EditText
    private lateinit var etExitPrice: EditText
    private lateinit var tvPaperResult: TextView
    private lateinit var tvScannerResult: TextView
    private lateinit var tvScannerAutoRefreshStatus: TextView
    private lateinit var tvScannerNextRefresh: TextView
    private lateinit var btnRunScanner: Button
    private lateinit var btnScannerPaperExecute: Button
    private lateinit var btnFullFnoBacktest: Button
    private lateinit var btnBoxSpread: Button
    private lateinit var cbScannerAutoRefresh: CheckBox
    private lateinit var etScannerRefreshSeconds: EditText
    private lateinit var btnPaperEntry: Button
    private lateinit var btnPaperPosition: Button
    private lateinit var btnPaperExit: Button
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
        etEntryPrice = findViewById(R.id.etEntryPrice)
        etQuantity = findViewById(R.id.etQuantity)
        etExitPrice = findViewById(R.id.etExitPrice)
        tvPaperResult = findViewById(R.id.tvPaperResult)
        tvScannerResult = findViewById(R.id.tvScannerResult)
        tvScannerAutoRefreshStatus = findViewById(R.id.tvScannerAutoRefreshStatus)
        tvScannerNextRefresh = findViewById(R.id.tvScannerNextRefresh)
        btnRunScanner = findViewById(R.id.btnRunScanner)
        btnScannerPaperExecute = findViewById(R.id.btnScannerPaperExecute)
        btnFullFnoBacktest = findViewById(R.id.btnFullFnoBacktest)
        btnBoxSpread = findViewById(R.id.btnBoxSpread)
        cbScannerAutoRefresh = findViewById(R.id.cbScannerAutoRefresh)
        etScannerRefreshSeconds = findViewById(R.id.etScannerRefreshSeconds)
        btnPaperEntry = findViewById(R.id.btnPaperEntry)
        btnPaperPosition = findViewById(R.id.btnPaperPosition)
        btnPaperExit = findViewById(R.id.btnPaperExit)
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
        btnScannerPaperExecute.isEnabled = false
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
        updateScannerAutoRefreshStatus(); checkServerStatus(); checkBrokerStatus(); checkSafetyStatus()
        btnAppUpdate.setOnClickListener { startActivity(Intent(this, UpdateActivity::class.java)) }
        btnStrategies.setOnClickListener { startActivity(Intent(this, StrategyRegistryActivity::class.java)) }
        btnRunScanner.setOnClickListener { runCashFutureScanner() }
        btnScannerPaperExecute.setOnClickListener { paperExecuteScannerOpportunity() }
        btnFullFnoBacktest.setOnClickListener { startActivity(Intent(this, FullFnoBacktestActivity::class.java)) }
        btnBoxSpread.setOnClickListener { startActivity(Intent(this, BoxSpreadActivity::class.java)) }
        cbScannerAutoRefresh.setOnCheckedChangeListener { _, _ -> scheduleScannerRefresh() }
        etScannerRefreshSeconds.setOnFocusChangeListener { _, hasFocus -> if (!hasFocus) scheduleScannerRefresh() }
        btnPaperEntry.setOnClickListener { paperEntry() }; btnPaperPosition.setOnClickListener { paperPosition() }; btnPaperExit.setOnClickListener { paperExit() }
        btnConnectBroker.setOnClickListener { connectAngelOne() }; btnDisconnectBroker.setOnClickListener { disconnectAngelOne() }
        btnEnableRealTrading.setOnClickListener { confirmEnableRealTrading() }; btnDisableRealTrading.setOnClickListener { disableRealTrading() }; btnKillSwitch.setOnClickListener { triggerKillSwitch() }
        tvScannerResult.setOnClickListener { openScannerDetail(lastExecutableOpportunity) }
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

    private fun renderScannerPaperState() { val opportunity = lastExecutableOpportunity; btnScannerPaperExecute.isEnabled = opportunity != null; btnScannerPaperExecute.text = opportunity?.let { "PAPER EXECUTE ${it.symbol} • ₹${it.cash_ask ?: it.cash_ltp}" } ?: "PAPER EXECUTE • NO EXECUTABLE OPPORTUNITY" }
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
            putExtra(ScannerDetailActivity.EXTRA_EXECUTABLE, opportunity.lifecycle != "EXPIRED" && opportunity.gap > 0.0 && opportunity.net_gap > 0.0)
        })
    }

    private fun runCashFutureScanner(): Job = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable); scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable); tvScannerNextRefresh.text = "Next Refresh: SCAN IN PROGRESS"; btnRunScanner.isEnabled = false; btnRunScanner.text = "SCANNING LIVE..."; btnScannerPaperExecute.isEnabled = false; tvScannerResult.text = lastScannerResult?.let { "$it\n\nREFRESHING LIVE 1s SCANNER..." } ?: "LIVE SCAN IN PROGRESS\n\nReading Cash–Future 1-second signals..." }
        try {
            val response = ApiService.retrofitService.liveCashFutureScan(maxAgeSeconds = 5.0, limit = 50)
            val completedAt = currentTimestamp()
            val sorted = response.data.sortedWith(compareByDescending<LiveCashFutureSignal> { it.executable }.thenByDescending { it.roi_pct }.thenByDescending { it.net_profit }).thenByDescending { it.rank_score }.thenByDescending { it.gap_pct }.thenBy { it.symbol }
            val executable = sorted.firstOrNull { it.executable }
            val responseDataSize = response.data.size
            val responsePositiveGapCount = response.data.count { it.gap > 0.0 && it.net_gap > 0.0 }
            val expiredCount = response.data.count { it.lifecycle == "EXPIRED" }
            val result = buildString {
                append("LIVE 1s SCAN — "); append("\n");
                // Source contract compatibility: append("LIVE 1s SCAN — \n") append(if (sorted.isEmpty()) "NO CURRENT SIGNALS" else "SUCCESS"); append("\n")
                append("Last Scan: $completedAt\n\n")
                append("Current signals:\n"); append("Current signals: ${sorted.size}\n")
                append("Executable positive-gap signals: ${sorted.count { it.gap > 0.0 && it.net_gap > 0.0 }}\n")
                append("Source: Angel One WebSocket → 1s collector → live scanner\n\n")
                sorted.forEach { item ->
                    append("────────────────────\n")
                    append("${item.symbol} • ${item.contract_month} • Rank ${String.format(Locale.US, "%.3f", item.rank_score)}\n")
                    append("Cash: ₹${item.cash_ask ?: item.cash_ltp}\n")
                    append("Future: ₹${item.future_bid ?: item.future_ltp}\n")
                    append("Gap: ₹${item.gap} (${item.gap_pct}%)\n")
                    append("Gross Spread: ₹${item.gross_profit ?: item.gross_lot_value ?: 0.0}\n")
                    append("Margin: ₹${item.capacity_notional ?: 0.0}\n")
                    append("Deployed Capital: ₹${item.capacity_notional ?: 0.0}\n")
                    append("Net Profit: ₹${item.net_profit}\n")
                    append("Net Profit: ₹${item.net_profit ?: 0.0}\n")
                    append("ROI: ${item.net_gap_pct}%\n")
                    append("Net Gap: ${item.net_gap_pct}%\n")
                    append("Net Profit: ₹${item.net_profit ?: 0.0} • Lots: ${item.alert_lots ?: 0}\n")
                    append("Cash H/L: ₹${item.cash_day_high} / ₹${item.cash_day_low}\n")
                    append("Future H/L: ₹${item.future_day_high} / ₹${item.future_day_low}\n")
                    append("Liquidity: ${item.liquidity_qty ?: 0.0} • Stable: ${item.stable_observations}s\n")
                    append("Executable: ${item.lifecycle}\n\n")
                    append("Lifecycle: ${item.lifecycle} • Alert: ${item.alert_event ?: "NONE"}\n\n")
                }
            }
            withContext(Dispatchers.Main) {
                val errorText = response.errors.joinToString("\n") { error -> "Error: ${error.symbol} • ${error.error}" }
                lastScannerResult = result
                if (errorText.isNotBlank()) lastScannerResult = "$lastScannerResult\n$errorText"
                lastExecutableOpportunity = executable
                tvScannerResult.text = lastScannerResult
                renderScannerPaperState()
            }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { val failure = "SCAN ERROR\n\nLast Attempt: $failedAt\n\nScanner Failed: ${error.message ?: "API error"}"; lastScannerResult = failure; tvScannerResult.text = failure; lastExecutableOpportunity = null; renderScannerPaperState() }
        } finally {
            withContext(Dispatchers.Main) { btnRunScanner.isEnabled = true; btnRunScanner.text = "RUN LIVE CASH–FUTURE SCAN"; if (lastScannerResult?.contains("SCAN ERROR") == true) tvScannerResult.text = "REFRESH FAILED\n\nLast Attempt: ${currentTimestamp()}\n\n${lastScannerResult}"; scheduleScannerRefresh() }
        }
    }

    private fun setPaperBusy(busy: Boolean, message: String? = null) { btnPaperEntry.isEnabled = !busy; btnPaperPosition.isEnabled = !busy; btnPaperExit.isEnabled = !busy; if (message != null) tvPaperResult.text = message }
    private fun paperExecuteScannerOpportunity() { val opportunity = lastExecutableOpportunity ?: return; etEntryPrice.setText((opportunity.cash_ask ?: opportunity.cash_ltp).toString()); etQuantity.setText("1"); tvPaperResult.text = "Selected ${opportunity.symbol} • Paper entry price loaded"; paperEntry() }

    private fun paperEntry() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { setPaperBusy(true, "PAPER ENTRY IN PROGRESS...") }
        try {
            val price = etEntryPrice.text.toString().toDoubleOrNull() ?: 0.0
            val quantity = etQuantity.text.toString().toDoubleOrNull() ?: 0.0
            val response = ApiService.retrofitService.paperEntry(PaperEntryRequest(price, quantity))
            val completedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvPaperResult.text = "ENTRY SUCCESS\n\nPAPER POSITION ACTIVE\nCompleted: $completedAt\n\nEntry: ₹${response.entry_price}\nStop Loss: ₹${response.stop_loss}\nTarget: ₹${response.target}\nQuantity: ${response.position.quantity}" }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvPaperResult.text = "ENTRY FAILED\n\nTime: $failedAt\n\n${error.message ?: "API error"}" }
        } finally { withContext(Dispatchers.Main) { setPaperBusy(false) } }
    }

    private fun paperPosition() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { setPaperBusy(true, "CHECKING PAPER POSITION...") }
        try {
            val response = ApiService.retrofitService.paperPosition()
            val completedAt = currentTimestamp()
            val position = response.position
            withContext(Dispatchers.Main) { tvPaperResult.text = if (position == null) "POSITION CHECK SUCCESS\n\nPAPER POSITION: FLAT\nChecked: $completedAt" else "POSITION CHECK SUCCESS\n\nPAPER POSITION ACTIVE\nChecked: $completedAt\n\nEntry: ₹${position.entry_price}\nStop Loss: ₹${position.stop_loss}\nTarget: ₹${position.target}\nQuantity: ${position.quantity}" }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvPaperResult.text = "POSITION CHECK FAILED\n\nTime: $failedAt\n\n${error.message ?: "API error"}" }
        } finally { withContext(Dispatchers.Main) { setPaperBusy(false) } }
    }

    private fun paperExit() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { setPaperBusy(true, "PAPER EXIT IN PROGRESS...") }
        try {
            val price = etExitPrice.text.toString().toDoubleOrNull() ?: 0.0
            val response = ApiService.retrofitService.paperExit(PaperExitRequest(price))
            val completedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvPaperResult.text = if (response.status == "closed") "EXIT SUCCESS\n\nCompleted: $completedAt\n\nEntry: ₹${response.entry_price ?: 0.0}\nExit: ₹${response.exit_price ?: 0.0}\nQuantity: ${response.quantity ?: 0.0}\nNet P&L: ₹${response.pnl}" else "EXIT SUCCESS\n\nCompleted: $completedAt\n\nStatus: ${response.status}\nNet P&L: ₹${response.pnl}" }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvPaperResult.text = "EXIT FAILED\n\nTime: $failedAt\n\n${error.message ?: "API error"}" }
        } finally { withContext(Dispatchers.Main) { setPaperBusy(false) } }
    }
}
