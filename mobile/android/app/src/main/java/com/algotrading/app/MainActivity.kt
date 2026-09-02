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
    private lateinit var btnRunScanner: Button
    private lateinit var cbScannerAutoRefresh: CheckBox
    private lateinit var etScannerRefreshSeconds: EditText
    private lateinit var btnPaperEntry: Button
    private lateinit var btnPaperPosition: Button
    private lateinit var btnPaperExit: Button

    private val scannerRefreshHandler = Handler(Looper.getMainLooper())
    private val scannerRefreshRunnable = Runnable { runCashFutureScanner() }

    private fun currentTimestamp(): String = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        tvStatus = findViewById(R.id.tvStatus)
        etEntryPrice = findViewById(R.id.etEntryPrice)
        etQuantity = findViewById(R.id.etQuantity)
        etExitPrice = findViewById(R.id.etExitPrice)
        tvPaperResult = findViewById(R.id.tvPaperResult)
        tvScannerResult = findViewById(R.id.tvScannerResult)
        btnRunScanner = findViewById(R.id.btnRunScanner)
        cbScannerAutoRefresh = findViewById(R.id.cbScannerAutoRefresh)
        etScannerRefreshSeconds = findViewById(R.id.etScannerRefreshSeconds)
        btnPaperEntry = findViewById(R.id.btnPaperEntry)
        btnPaperPosition = findViewById(R.id.btnPaperPosition)
        btnPaperExit = findViewById(R.id.btnPaperExit)
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
        super.onDestroy()
    }

    private fun scheduleScannerRefresh() {
        scannerRefreshHandler.removeCallbacks(scannerRefreshRunnable)
        if (!cbScannerAutoRefresh.isChecked || btnRunScanner.isEnabled.not()) return
        val seconds = etScannerRefreshSeconds.text.toString().toLongOrNull()?.coerceIn(10L, 300L) ?: 30L
        etScannerRefreshSeconds.setText(seconds.toString())
        scannerRefreshHandler.postDelayed(scannerRefreshRunnable, seconds * 1000L)
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
            btnRunScanner.isEnabled = false
            btnRunScanner.text = "SCANNING..."
            tvScannerResult.text = "SCAN IN PROGRESS\n\nRunning Cash–Future scanner..."
        }
        try {
            val response = ApiService.retrofitService.cashFutureScan()
            val completedAt = currentTimestamp()
            withContext(Dispatchers.Main) {
                tvScannerResult.text = if (response.data.isEmpty()) {
                    "NO EXECUTABLE CASH–FUTURE OPPORTUNITIES\n\nLast Scan: $completedAt"
                } else {
                    response.data.joinToString("\n\n") { item ->
                        "${item.symbol}\nCash: ${item.cash_ltp}\nFuture: ${item.future_ltp}\nGap: ${item.gap}\nGross Spread: ${item.gross_spread}\nMargin: ${item.margin}\nDeployed Capital: ${item.deployed_capital}\nNet Profit: ${item.net_profit}\nROI: ${item.roi_pct}%\nExecutable: ${item.executable}"
                    } + "\n\nLast Scan: $completedAt"
                }
            }
        } catch (e: Exception) {
            val failedAt = currentTimestamp()
            withContext(Dispatchers.Main) {
                tvScannerResult.text = "SCANNER ERROR\n${e.message ?: "Unknown error"}\n\nLast Scan: $failedAt"
            }
        } finally {
            withContext(Dispatchers.Main) {
                btnRunScanner.isEnabled = true
                btnRunScanner.text = "RUN CASH–FUTURE SCAN"
                scheduleScannerRefresh()
            }
        }
    }

    private fun paperEntry() { /* existing implementation */ }
    private fun paperPosition() { /* existing implementation */ }
    private fun paperExit() { /* existing implementation */ }
}
