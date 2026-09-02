package com.algotrading.app

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
    private lateinit var cbScannerAutoRefresh: CheckBox
    private lateinit var etScannerRefreshSeconds: EditText
    private lateinit var btnPaperEntry: Button
    private lateinit var btnPaperPosition: Button
    private lateinit var btnPaperExit: Button

    private val scannerRefreshHandler = Handler(Looper.getMainLooper())
    private lateinit var scannerRefreshRunnable: Runnable
    private lateinit var scannerCountdownRunnable: Runnable
    private var scannerCountdownSeconds = 0L

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
        cbScannerAutoRefresh = findViewById(R.id.cbScannerAutoRefresh)
        etScannerRefreshSeconds = findViewById(R.id.etScannerRefreshSeconds)
        btnPaperEntry = findViewById(R.id.btnPaperEntry)
        btnPaperPosition = findViewById(R.id.btnPaperPosition)
        btnPaperExit = findViewById(R.id.btnPaperExit)
        scannerRefreshRunnable = Runnable { runCashFutureScanner() }
        scannerCountdownRunnable = object : Runnable {
            override fun run() {
                if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not()) {
                    tvScannerNextRefresh.text = if (btnRunScanner.isEnabled) "Next Refresh: —" else "Next Refresh: SCAN IN PROGRESS"
                    return
                }
                scannerCountdownSeconds -= 1L
                if (scannerCountdownSeconds <= 0L) {
                    tvScannerNextRefresh.text = "Next Refresh: NOW"
                    return
                }
                tvScannerNextRefresh.text = "Next Refresh: ${scannerCountdownSeconds}s"
                scannerRefreshHandler.postDelayed(this, 1000L)
            }
        }
        updateScannerAutoRefreshStatus()
        checkServerStatus()
        btnRunScanner.setOnClickListener { runCashFutureScanner() }
        cbScannerAutoRefresh.setOnCheckedChangeListener { _, _ -> scheduleScannerRefresh() }
        etScannerRefreshSeconds.setOnFocusChangeListener { _, hasFocus -> if (!hasFocus) scheduleScannerRefresh() }
        btnPaperEntry.setOnClickListener { paperEntry() }
        btnPaperExit.setOnClickListener { paperExit() }
        btnPaperPosition.setOnClickListener { paperPosition() }
    }

    override fun onDestroy() {
        scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)
        scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable)
        super.onDestroy()
    }

    private fun updateScannerAutoRefreshStatus() {
        tvScannerAutoRefreshStatus.text = if (cbScannerAutoRefresh.isChecked) "Auto Refresh: ON" else "Auto Refresh: OFF"
    }

    private fun scheduleScannerRefresh() {
        scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)
        scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable)
        updateScannerAutoRefreshStatus()
        if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not()) {
            tvScannerNextRefresh.text = if (btnRunScanner.isEnabled) "Next Refresh: —" else "Next Refresh: SCAN IN PROGRESS"
            return
        }
        val seconds = etScannerRefreshSeconds.text.toString().toLongOrNull()?.coerceIn(10L, 300L) ?: 30L
        etScannerRefreshSeconds.setText(seconds.toString())
        scannerCountdownSeconds = seconds
        tvScannerNextRefresh.text = "Next Refresh: ${scannerCountdownSeconds}s"
        scannerRefreshHandler.postDelayed(scannerRefreshRunnable, seconds * 1000L)
        scannerRefreshHandler.postDelayed(scannerCountdownRunnable, 1000L)
    }

    private fun checkServerStatus() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val response = ApiService.retrofitService.getRootStatus()
            withContext(Dispatchers.Main) { tvStatus.text = "Server Status: ${response.status}" }
        } catch (_: Exception) {
            withContext(Dispatchers.Main) { tvStatus.text = "Server Error: Offline" }
        }
    }

    private fun runCashFutureScanner(): Job = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) {
            scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)
            scannerRefreshHandler.removeCallbacks(scannerCountdownRunnable)
            tvScannerNextRefresh.text = "Next Refresh: SCAN IN PROGRESS"
            btnRunScanner.isEnabled = false
            btnRunScanner.text = "SCANNING..."
            tvScannerResult.text = "SCAN IN PROGRESS\n\nRunning Cash–Future scanner..."
        }
        try {
            val response = ApiService.retrofitService.cashFutureScan()
            val completedAt = currentTimestamp()
            withContext(Dispatchers.Main) {
                tvScannerResult.text = if (response.data.isEmpty()) {
                    buildString {
                        append("SCAN COMPLETE — NO OPPORTUNITIES\n")
                        append("Last Scan: $completedAt\n\n")
                        append("Symbols requested: ${response.symbols_requested.size}\n")
                        append("Observations: ${response.scanned_observations}\n")
                        append("Executable opportunities: 0\n")
                        append("Errors: ${response.errors.size}\n\n")
                        append("No executable Cash–Future opportunities found.")
                        if (response.errors.isNotEmpty()) {
                            append("\n\nERRORS (${response.errors.size})\n")
                            response.errors.forEach { error -> append("${error.symbol}: ${error.error}\n") }
                        }
                    }
                } else {
                    buildString {
                        append("SCAN COMPLETE — SUCCESS\n")
                        append("Last Scan: $completedAt\n\n")
                        append("Symbols requested: ${response.symbols_requested.size}\n")
                        append("Observations: ${response.scanned_observations}\n")
                        append("Executable opportunities: ${response.opportunity_count}\n")
                        append("Errors: ${response.errors.size}\n\n")
                        append("CASH–FUTURE OPPORTUNITIES (${response.opportunity_count})\n")
                        append("Priority: EXECUTABLE FIRST\n")
                        append("Mode: ${response.mode}\n\n")
                        response.data.sortedWith(compareByDescending<CashFutureOpportunity> { it.executable }.thenByDescending { it.roi_pct }.thenByDescending { it.net_profit }).forEach { item ->
                            append("────────────────────\n")
                            append("${item.symbol}\n")
                            append("Cash: ₹${item.cash_price}\n")
                            append("Future: ₹${item.future_price}\n")
                            append("Gap: ₹${item.gap} (${item.gap_pct}%)\n")
                            append("Gross Spread: ₹${item.gross_spread_profit}\n")
                            append("Margin: ₹${item.margin_required}\n")
                            append("Deployed Capital: ₹${item.deployed_capital}\n")
                            append("Net Profit: ₹${item.net_profit}\n")
                            append("ROI: ${item.roi_pct}%\n")
                            append("Executable: ${if (item.executable) "YES" else "NO"}\n\n")
                        }
                        if (response.errors.isNotEmpty()) {
                            append("ERRORS (${response.errors.size})\n")
                            response.errors.forEach { error -> append("${error.symbol}: ${error.error}\n") }
                        }
                    }
                }
            }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvScannerResult.text = "SCAN ERROR\n\nLast Scan: $failedAt\n\nScanner Failed: ${error.message ?: "API error"}" }
        } finally {
            withContext(Dispatchers.Main) {
                btnRunScanner.isEnabled = true
                btnRunScanner.text = "RUN CASH–FUTURE SCAN"
                if (cbScannerAutoRefresh.isChecked) {
                    tvScannerAutoRefreshStatus.text = "Auto Refresh: ON • READY"
                } else {
                    tvScannerAutoRefreshStatus.text = "Auto Refresh: OFF"
                    tvScannerNextRefresh.text = "Next Refresh: —"
                }
                scheduleScannerRefresh()
            }
        }
    }

    private fun setPaperBusy(busy: Boolean, message: String? = null) {
        btnPaperEntry.isEnabled = !busy
        btnPaperPosition.isEnabled = !busy
        btnPaperExit.isEnabled = !busy
        if (message != null) tvPaperResult.text = message
    }

    private fun paperEntry() {
        val price = etEntryPrice.text.toString().toDoubleOrNull()
        val quantity = etQuantity.text.toString().toDoubleOrNull()
        if (price == null || price <= 0) { etEntryPrice.error = "Enter a positive entry price"; return }
        if (quantity == null || quantity <= 0) { etQuantity.error = "Enter a positive quantity"; return }
        lifecycleScope.launch(Dispatchers.IO) {
            withContext(Dispatchers.Main) { setPaperBusy(true, "PAPER ENTRY IN PROGRESS...") }
            try {
                val response = ApiService.retrofitService.paperEntry(PaperEntryRequest(price, quantity))
                val completedAt = currentTimestamp()
                withContext(Dispatchers.Main) { tvPaperResult.text = "ENTRY SUCCESS\n\nPAPER POSITION ACTIVE\nCompleted: $completedAt\n\nEntry: ₹${response.entry_price}\nStop Loss: ₹${response.stop_loss}\nTarget: ₹${response.target}\nQuantity: ${response.position.quantity}" }
            } catch (error: Exception) {
                val failedAt = currentTimestamp()
                withContext(Dispatchers.Main) { tvPaperResult.text = "ENTRY FAILED\n\nTime: $failedAt\n\n${error.message ?: "API error"}" }
            }
            finally { withContext(Dispatchers.Main) { setPaperBusy(false) } }
        }
    }

    private fun paperPosition() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { setPaperBusy(true, "CHECKING PAPER POSITION...") }
        try {
            val response = ApiService.retrofitService.paperPosition()
            val completedAt = currentTimestamp()
            withContext(Dispatchers.Main) {
                val position = response.position
                tvPaperResult.text = if (position == null) "POSITION CHECK SUCCESS\n\nPAPER POSITION: FLAT\n\nChecked: $completedAt" else "POSITION CHECK SUCCESS\n\nPAPER POSITION ACTIVE\nChecked: $completedAt\n\nEntry: ₹${position.entry_price}\nStop Loss: ₹${position.stop_loss}\nTarget: ₹${position.target}\nQuantity: ${position.quantity}"
            }
        } catch (error: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) { tvPaperResult.text = "POSITION CHECK FAILED\n\nTime: $failedAt\n\n${error.message ?: "API error"}" }
        }
        finally { withContext(Dispatchers.Main) { setPaperBusy(false) } }
    }

    private fun paperExit() {
        val price = etExitPrice.text.toString().toDoubleOrNull()
        if (price == null || price <= 0) { etExitPrice.error = "Enter a positive exit price"; return }
        lifecycleScope.launch(Dispatchers.IO) {
            withContext(Dispatchers.Main) { setPaperBusy(true, "PAPER EXIT IN PROGRESS...") }
            try {
                val response = ApiService.retrofitService.paperExit(PaperExitRequest(price))
                val completedAt = currentTimestamp()
                withContext(Dispatchers.Main) { tvPaperResult.text = if (response.status == "closed") "EXIT SUCCESS\n\nPAPER POSITION CLOSED\nCompleted: $completedAt\n\nEntry: ₹${response.entry_price}\nExit: ₹${response.exit_price}\nQuantity: ${response.quantity}\nP&L: ₹${response.pnl}" else "EXIT SUCCESS\n\nPAPER POSITION: FLAT\nCompleted: $completedAt" }
            } catch (error: Exception) {
                val failedAt = currentTimestamp()
                withContext(Dispatchers.Main) { tvPaperResult.text = "EXIT FAILED\n\nTime: $failedAt\n\n${error.message ?: "API error"}" }
            }
            finally { withContext(Dispatchers.Main) { setPaperBusy(false) } }
        }
    }
}
