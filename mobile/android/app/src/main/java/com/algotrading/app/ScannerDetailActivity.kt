package com.algotrading.app

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ScannerDetailActivity : AppCompatActivity() {
    private lateinit var tvCurrentLtpValue: TextView
    private lateinit var tvRiskValue: TextView

    private val quoteHandler = Handler(Looper.getMainLooper())
    private var quoteRunnable: Runnable? = null
    private var detailSymbol = ""
    private var scannerCash = 0.0
    private var scannerNet = 0.0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_scanner_detail)

        findViewById<TextView>(R.id.tvDetailBack).setOnClickListener { finish() }
        tvCurrentLtpValue = findViewById(R.id.tvCurrentLtpValue)
        tvRiskValue = findViewById(R.id.tvRiskValue)

        detailSymbol = intent.getStringExtra(EXTRA_SYMBOL).orEmpty().trim().uppercase()
        scannerCash = intent.getDoubleExtra(EXTRA_CASH_PRICE, 0.0)
        val future = intent.getDoubleExtra(EXTRA_FUTURE_PRICE, 0.0)
        val gap = intent.getDoubleExtra(EXTRA_GAP, 0.0)
        val gapPct = intent.getDoubleExtra(EXTRA_GAP_PCT, 0.0)
        val gross = intent.getDoubleExtra(EXTRA_GROSS_SPREAD_PROFIT, 0.0)
        val margin = intent.getDoubleExtra(EXTRA_MARGIN_REQUIRED, 0.0)
        val capital = intent.getDoubleExtra(EXTRA_DEPLOYED_CAPITAL, 0.0)
        scannerNet = intent.getDoubleExtra(EXTRA_NET_PROFIT, 0.0)
        val roi = intent.getDoubleExtra(EXTRA_ROI_PCT, 0.0)

        findViewById<TextView>(R.id.tvDetailTitle).text = detailSymbol.ifBlank { "Scanner Opportunity" }
        findViewById<TextView>(R.id.tvDetailStatus).text = "SCANNER SNAPSHOT • NO EXECUTION"
        renderScannerSnapshot(future, gap, gapPct, gross, margin, capital, roi)
    }

    override fun onStart() {
        super.onStart()
        refreshExecutionAndQuote()
        startQuoteRefresh()
    }

    override fun onStop() {
        stopQuoteRefresh()
        super.onStop()
    }

    private fun startQuoteRefresh() {
        stopQuoteRefresh()
        quoteRunnable = object : Runnable {
            override fun run() {
                refreshExecutionAndQuote()
                quoteHandler.postDelayed(this, 3000L)
            }
        }.also { quoteHandler.post(it) }
    }

    private fun stopQuoteRefresh() {
        quoteRunnable?.let(quoteHandler::removeCallbacks)
        quoteRunnable = null
    }

    private fun renderScannerSnapshot(future: Double, gap: Double, gapPct: Double, gross: Double, margin: Double, capital: Double, roi: Double) {
        tvCurrentLtpValue.text = if (scannerCash > 0.0) "${money(scannerCash)} • scanner snapshot" else "Awaiting live quote"
        findViewById<TextView>(R.id.tvCashValue).text = money(scannerCash)
        findViewById<TextView>(R.id.tvFutureValue).text = money(future)
        findViewById<TextView>(R.id.tvGapValue).text = "${money(gap)} (${pct(gapPct)}%)"
        findViewById<TextView>(R.id.tvGrossValue).text = money(gross)
        findViewById<TextView>(R.id.tvMarginValue).text = money(margin)
        findViewById<TextView>(R.id.tvCapitalValue).text = money(capital)
        findViewById<TextView>(R.id.tvNetValue).text = money(scannerNet)
        findViewById<TextView>(R.id.tvRoiValue).text = "${pct(roi)}%"
        findViewById<TextView>(R.id.tvBreakevenValue).text = "Break-even: Not calculated without an executed strategy"
        findViewById<TextView>(R.id.tvMaxProfitValue).text = "Max Profit (scanner estimate): ${money(scannerNet)}"
        findViewById<TextView>(R.id.tvMaxLossValue).text = "Max Loss: Not available until strategy legs are defined"
        tvRiskValue.text = "Observation only • Paper execution removed"
        findViewById<TextView>(R.id.tvAnalysisNote).text = "Scanner estimates are for analysis only. No paper order or broker order is created by this screen."
    }

    private fun refreshExecutionAndQuote() {
        if (detailSymbol.isBlank()) {
            tvCurrentLtpValue.text = if (scannerCash > 0.0) "${money(scannerCash)} • scanner snapshot" else "Awaiting live quote"
            return
        }
        lifecycleScope.launch(Dispatchers.IO) {
            var ltp: Double? = null
            var quoteError: String? = null
            try {
                ltp = ApiService.retrofitService.ltpBySymbol(detailSymbol).ltp
                if (ltp == null || ltp <= 0.0) quoteError = "LTP unavailable"
            } catch (error: Exception) {
                quoteError = error.message ?: "Quote API error"
            }
            withContext(Dispatchers.Main) {
                tvCurrentLtpValue.text = if (ltp != null) "${money(ltp)} • live quote" else if (scannerCash > 0.0) "${money(scannerCash)} • scanner snapshot" else "Quote unavailable • ${quoteError ?: "unknown error"}"
                tvRiskValue.text = "Observation only • no paper position"
                findViewById<TextView>(R.id.tvAnalysisNote).text = "Current quote is shown for analysis. This app no longer creates, manages, or tracks paper trades."
            }
        }
    }

    private fun money(value: Double): String = "₹" + String.format("%,.2f", value)
    private fun signedMoney(value: Double): String = if (value >= 0.0) "+${money(value)}" else "-${money(kotlin.math.abs(value))}"
    private fun pct(value: Double): String = String.format("%.2f", value)
    private fun signedPct(value: Double): String = if (value >= 0.0) "+${pct(value)}%" else "-${pct(kotlin.math.abs(value))}%"
    private fun formatQuantity(value: Double): String = if (value % 1.0 == 0.0) value.toLong().toString() else String.format("%.2f", value)

    companion object {
        const val EXTRA_SYMBOL = "symbol"
        const val EXTRA_CASH_PRICE = "cash_price"
        const val EXTRA_FUTURE_PRICE = "future_price"
        const val EXTRA_GAP = "gap"
        const val EXTRA_GAP_PCT = "gap_pct"
        const val EXTRA_GROSS_SPREAD_PROFIT = "gross_spread_profit"
        const val EXTRA_MARGIN_REQUIRED = "margin_required"
        const val EXTRA_DEPLOYED_CAPITAL = "deployed_capital"
        const val EXTRA_NET_PROFIT = "net_profit"
        const val EXTRA_ROI_PCT = "roi_pct"
    }
}
