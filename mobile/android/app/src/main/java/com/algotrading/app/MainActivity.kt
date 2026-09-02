package com.algotrading.app

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
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
        btnRunScanner = findViewById(R.id.btnRunScanner)
        checkServerStatus()
        btnRunScanner.setOnClickListener { runCashFutureScanner() }
        findViewById<Button>(R.id.btnPaperEntry).setOnClickListener { paperEntry() }
        findViewById<Button>(R.id.btnPaperExit).setOnClickListener { paperExit() }
        findViewById<Button>(R.id.btnPaperPosition).setOnClickListener { paperPosition() }
    }

    private fun checkServerStatus() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val response = ApiService.retrofitService.getRootStatus()
            withContext(Dispatchers.Main) { tvStatus.text = "Server Status: ${response.status}" }
        } catch (_: Exception) {
            withContext(Dispatchers.Main) { tvStatus.text = "Server Error: Offline" }
        }
    }

    private fun runCashFutureScanner() = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) {
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
            }
        }
    }

    private fun paperEntry() {
        val price = etEntryPrice.text.toString().toDoubleOrNull()
        val quantity = etQuantity.text.toString().toDoubleOrNull()
        if (price == null || price <= 0) { etEntryPrice.error = "Enter a positive entry price"; return }
        if (quantity == null || quantity <= 0) { etQuantity.error = "Enter a positive quantity"; return }
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val response = ApiService.retrofitService.paperEntry(PaperEntryRequest(price, quantity))
                withContext(Dispatchers.Main) { tvPaperResult.text = "PAPER POSITION ACTIVE\n\nEntry: ₹${response.entry_price}\nStop Loss: ₹${response.stop_loss}\nTarget: ₹${response.target}\nQuantity: ${response.position.quantity}" }
            } catch (error: Exception) { withContext(Dispatchers.Main) { tvPaperResult.text = "Paper Entry Failed: ${error.message ?: "API error"}" } }
        }
    }

    private fun paperPosition() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val response = ApiService.retrofitService.paperPosition()
            withContext(Dispatchers.Main) {
                val position = response.position
                tvPaperResult.text = if (position == null) "PAPER POSITION: FLAT" else "PAPER POSITION ACTIVE\n\nEntry: ₹${position.entry_price}\nStop Loss: ₹${position.stop_loss}\nTarget: ₹${position.target}\nQuantity: ${position.quantity}"
            }
        } catch (error: Exception) { withContext(Dispatchers.Main) { tvPaperResult.text = "Position Check Failed: ${error.message ?: "API error"}" } }
    }

    private fun paperExit() {
        val price = etExitPrice.text.toString().toDoubleOrNull()
        if (price == null || price <= 0) { etExitPrice.error = "Enter a positive exit price"; return }
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val response = ApiService.retrofitService.paperExit(PaperExitRequest(price))
                withContext(Dispatchers.Main) { tvPaperResult.text = if (response.status == "closed") "PAPER POSITION CLOSED\n\nEntry: ₹${response.entry_price}\nExit: ₹${response.exit_price}\nQuantity: ${response.quantity}\nP&L: ₹${response.pnl}" else "PAPER POSITION: FLAT" }
            } catch (error: Exception) { withContext(Dispatchers.Main) { tvPaperResult.text = "Paper Exit Failed: ${error.message ?: "API error"}" } }
        }
    }
}
