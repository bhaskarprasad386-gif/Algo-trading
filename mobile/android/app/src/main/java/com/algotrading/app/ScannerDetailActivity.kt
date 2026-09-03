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
    private lateinit var tvEntryValue: TextView
    private lateinit var tvQuantityValue: TextView
    private lateinit var tvLivePnlValue: TextView
    private lateinit var tvNetPnlValue: TextView
    private lateinit var tvRiskValue: TextView

    private val quoteHandler = Handler(Looper.getMainLooper())
    private var quoteRunnable: Runnable? = null
    private var detailSymbol = ""
    private var scannerCash = 0.0
    private var scannerNet = 0.0
    private var scannerExecutable = false
    private var activePosition: PaperPosition? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_scanner_detail)

        findViewById<TextView>(R.id.tvDetailBack).setOnClickListener { finish() }
        tvCurrentLtpValue = findViewById(R.id.tvCurrentLtpValue)
        tvEntryValue = findViewById(R.id.tvEntryValue)
        tvQuantityValue = findViewById(R.id.tvQuantityValue)
        tvLivePnlValue = findViewById(R.id.tvLivePnlValue)
        tvNetPnlValue = findViewById(R.id.tvNetPnlValue)
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
        scannerExecutable = intent.getBooleanExtra(EXTRA_EXECUTABLE, false)

        findViewById<TextView>(R.id.tvDetailTitle).text = detailSymbol.ifBlank { "Scanner Opportunity" }
        findViewById<TextView>(R.id.tvDetailStatus).text = if (scannerExecutable) "EXECUTABLE • PAPER MODE" else "OBSERVATION ONLY"
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
        tvEntryValue.text = "Not executed"
        tvQuantityValue.text = "0"
        tvLivePnlValue.text = "₹0.00 • no active position"
        tvNetPnlValue.text = "Awaiting executed position"
        findViewById<TextView>(R.id.tvCashValue).text = money(scannerCash)
        findViewById<TextView>(R.id.tvFutureValue).text = money(future)
        findViewById<TextView>(R.id.tvGapValue).text = "${money(gap)} (${pct(gapPct)}%)"
        findViewById<TextView>(R.id.tvGrossValue).text = money(gross)
        findViewById<TextView>(R.id.tvMarginValue).text = money(margin)
        findViewById<TextView>(R.id.tvCapitalValue).text = money(capital)
        findViewById<TextView>(R.id.tvNetValue).text = money(scannerNet)
        findViewById<TextView>(R.id.tvRoiValue).text = "${pct(roi)}%"
        findViewById<TextView>(R.id.tvBreakevenValue).text = "Break-even: Awaiting executed strategy legs"
        findViewById<TextView>(R.id.tvMaxProfitValue).text = "Max Profit (scanner estimate): ${money(scannerNet)}"
        findViewById<TextView>(R.id.tvMaxLossValue).text = "Max Loss: Not available until strategy legs are defined"
        tvRiskValue.text = if (scannerExecutable) "Risk: paper execution eligible; loading actual paper position…" else "Risk: observation only; no executable position is active"
        findViewById<TextView>(R.id.tvAnalysisNote).text = if (scannerExecutable) "Scanner snapshot is separate from execution. Actual paper state and current market quote are refreshed independently; scanner estimates are never reused as executed P&L. Live order routing remains disabled." else "This result is for analysis only. No executable trade is suggested by the scanner."
    }

    private fun refreshExecutionAndQuote() {
        lifecycleScope.launch(Dispatchers.IO) {
            var position: PaperPosition? = null
            var positionError: String? = null
            var ltp: Double? = null
            var quoteError: String? = null
            try {
                position = ApiService.retrofitService.paperPosition().position
            } catch (error: Exception) {
                positionError = error.message ?: "Paper position API error"
            }
            if (detailSymbol.isNotBlank()) {
                try {
                    ltp = ApiService.retrofitService.ltpBySymbol(detailSymbol).ltp
                    if (ltp == null || ltp <= 0.0) quoteError = "LTP unavailable"
                } catch (error: Exception) {
                    quoteError = error.message ?: "Quote API error"
                }
            }
            withContext(Dispatchers.Main) {
                activePosition = if (position?.quantity ?: 0.0 > 0.0 && position?.symbol?.uppercase() == detailSymbol) position else null
                renderLiveState(ltp, positionError, quoteError)
            }
        }
    }

    private fun renderLiveState(ltp: Double?, positionError: String?, quoteError: String?) {
        val position = activePosition
        if (position == null) {
            tvEntryValue.text = "Not executed"
            tvQuantityValue.text = "0"
            tvLivePnlValue.text = "₹0.00 • no matching active position"
            tvNetPnlValue.text = "Awaiting executed position"
            tvCurrentLtpValue.text = if (ltp != null) "${money(ltp)} • live quote" else if (scannerCash > 0.0) "${money(scannerCash)} • scanner snapshot" else "Awaiting live quote"
            tvRiskValue.text = if (positionError != null) "Paper position unavailable • $positionError" else if (scannerExecutable) "Risk: paper execution eligible; no matching active position" else "Risk: observation only; no active position"
            return
        }

        tvEntryValue.text = money(position.entry_price)
        tvQuantityValue.text = formatQuantity(position.quantity)
        if (ltp != null) {
            val grossPnl = (ltp - position.entry_price) * position.quantity
            val pnlPct = if (position.entry_price > 0.0) ((ltp - position.entry_price) / position.entry_price) * 100.0 else 0.0
            tvCurrentLtpValue.text = "${money(ltp)} • live quote"
            tvLivePnlValue.text = "${signedMoney(grossPnl)} • ${signedPct(pnlPct)}"
            tvNetPnlValue.text = "Gross P&L ${signedMoney(grossPnl)} • charges not available"
            tvRiskValue.text = when {
                ltp <= position.stop_loss -> "RISK: BELOW STOP LOSS • ${money(position.stop_loss)}"
                ltp >= position.target -> "TARGET ZONE • ${money(position.target)}"
                else -> "PAPER POSITION ACTIVE • qty ${formatQuantity(position.quantity)} • target ${money(position.target)}"
            }
            findViewById<TextView>(R.id.tvAnalysisNote).text = "Live paper P&L = (current LTP − executed entry) × executed quantity. Net P&L is not fabricated: broker/applicable charges are not yet available from the execution response."
        } else {
            tvCurrentLtpValue.text = if (scannerCash > 0.0) "${money(scannerCash)} • scanner snapshot" else "Quote unavailable"
            tvLivePnlValue.text = "₹0.00 • ${quoteError ?: "quote unavailable"}"
            tvNetPnlValue.text = "Execution active • net P&L awaits quote/charges"
            tvRiskValue.text = "PAPER POSITION ACTIVE • quote unavailable"
        }
        findViewById<TextView>(R.id.tvBreakevenValue).text = "Break-even: ${money(position.entry_price)} • paper position"
        findViewById<TextView>(R.id.tvMaxLossValue).text = "Stop Loss: ${money(position.stop_loss)}"
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
        const val EXTRA_EXECUTABLE = "executable"
    }
}
