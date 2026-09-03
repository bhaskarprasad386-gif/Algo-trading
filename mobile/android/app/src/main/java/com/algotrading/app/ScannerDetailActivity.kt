package com.algotrading.app

import android.os.Bundle
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

    private var scannerCash = 0.0
    private var scannerNet = 0.0
    private var scannerExecutable = false

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

        val symbol = intent.getStringExtra(EXTRA_SYMBOL).orEmpty()
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

        findViewById<TextView>(R.id.tvDetailTitle).text = symbol.ifBlank { "Scanner Opportunity" }
        findViewById<TextView>(R.id.tvDetailStatus).text = if (scannerExecutable) "EXECUTABLE • PAPER MODE" else "OBSERVATION ONLY"
        renderScannerSnapshot(future, gap, gapPct, gross, margin, capital, roi)
        loadPaperPosition()
    }

    override fun onResume() {
        super.onResume()
        if (::tvCurrentLtpValue.isInitialized) loadPaperPosition()
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
        findViewById<TextView>(R.id.tvAnalysisNote).text = if (scannerExecutable) "Scanner snapshot is separate from execution. The screen now refreshes the durable paper-position state; actual P&L is never inferred from the scanner estimate. Live order routing remains disabled." else "This result is for analysis only. No executable trade is suggested by the scanner."
    }

    private fun loadPaperPosition() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val response = ApiService.retrofitService.paperPosition()
            withContext(Dispatchers.Main) {
                val position = response.position
                if (position == null || position.quantity <= 0.0) {
                    tvCurrentLtpValue.text = if (scannerCash > 0.0) "${money(scannerCash)} • scanner snapshot" else "Awaiting live quote"
                    tvEntryValue.text = "Not executed"
                    tvQuantityValue.text = "0"
                    tvLivePnlValue.text = "₹0.00 • no active position"
                    tvNetPnlValue.text = "Awaiting executed position"
                    tvRiskValue.text = if (scannerExecutable) "Risk: paper execution eligible; no active paper position" else "Risk: observation only; no active position"
                    return@withContext
                }

                tvCurrentLtpValue.text = if (scannerCash > 0.0) "${money(scannerCash)} • scanner quote" else "Quote unavailable"
                tvEntryValue.text = money(position.entry_price)
                tvQuantityValue.text = formatQuantity(position.quantity)
                tvLivePnlValue.text = "₹0.00 • current quote feed not connected"
                tvNetPnlValue.text = "Execution active • net P&L awaits current quote/charges"
                findViewById<TextView>(R.id.tvBreakevenValue).text = "Break-even: ${money(position.entry_price)} • paper position"
                findViewById<TextView>(R.id.tvMaxLossValue).text = "Stop Loss: ${money(position.stop_loss)}"
                findViewById<TextView>(R.id.tvRiskValue).text = "Risk: PAPER POSITION ACTIVE • qty ${formatQuantity(position.quantity)} • target ${money(position.target)}"
                findViewById<TextView>(R.id.tvAnalysisNote).text = "Actual paper position loaded from execution state. Live P&L remains zero/unavailable until a current market quote is supplied; scanner estimates are not reused as executed P&L."
            }
        } catch (error: Exception) {
            withContext(Dispatchers.Main) {
                tvRiskValue.text = "Paper position: unavailable • ${error.message ?: "API error"}"
            }
        }
    }

    private fun money(value: Double): String = "₹" + String.format("%,.2f", value)
    private fun pct(value: Double): String = String.format("%.2f", value)
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
