package com.algotrading.app

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class ScannerDetailActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_scanner_detail)

        findViewById<TextView>(R.id.tvDetailBack).setOnClickListener { finish() }

        val symbol = intent.getStringExtra(EXTRA_SYMBOL).orEmpty()
        val cash = intent.getDoubleExtra(EXTRA_CASH, 0.0)
        val future = intent.getDoubleExtra(EXTRA_FUTURE, 0.0)
        val gap = intent.getDoubleExtra(EXTRA_GAP, 0.0)
        val gapPct = intent.getDoubleExtra(EXTRA_GAP_PCT, 0.0)
        val gross = intent.getDoubleExtra(EXTRA_GROSS, 0.0)
        val margin = intent.getDoubleExtra(EXTRA_MARGIN, 0.0)
        val capital = intent.getDoubleExtra(EXTRA_CAPITAL, 0.0)
        val net = intent.getDoubleExtra(EXTRA_NET, 0.0)
        val roi = intent.getDoubleExtra(EXTRA_ROI, 0.0)
        val executable = intent.getBooleanExtra(EXTRA_EXECUTABLE, false)

        findViewById<TextView>(R.id.tvDetailTitle).text = symbol.ifBlank { "Scanner Opportunity" }
        findViewById<TextView>(R.id.tvDetailStatus).text = if (executable) "EXECUTABLE • PAPER MODE" else "OBSERVATION ONLY"
        findViewById<TextView>(R.id.tvCashValue).text = money(cash)
        findViewById<TextView>(R.id.tvFutureValue).text = money(future)
        findViewById<TextView>(R.id.tvGapValue).text = "${money(gap)}  (${pct(gapPct)}%)"
        findViewById<TextView>(R.id.tvGrossValue).text = money(gross)
        findViewById<TextView>(R.id.tvMarginValue).text = money(margin)
        findViewById<TextView>(R.id.tvCapitalValue).text = money(capital)
        findViewById<TextView>(R.id.tvNetValue).text = money(net)
        findViewById<TextView>(R.id.tvRoiValue).text = "${pct(roi)}%"
        findViewById<TextView>(R.id.tvAnalysisNote).text =
            if (executable) "The scanner marked this opportunity executable. Live order routing remains disabled; use paper execution until live-trading safety and reconciliation are fully approved."
            else "This result is for analysis only. No executable trade is suggested by the scanner."
    }

    private fun money(value: Double): String = "₹" + String.format("%,.2f", value)
    private fun pct(value: Double): String = String.format("%.2f", value)

    companion object {
        const val EXTRA_SYMBOL = "symbol"
        const val EXTRA_CASH = "cash_price"
        const val EXTRA_FUTURE = "future_price"
        const val EXTRA_GAP = "gap"
        const val EXTRA_GAP_PCT = "gap_pct"
        const val EXTRA_GROSS = "gross_spread_profit"
        const val EXTRA_MARGIN = "margin_required"
        const val EXTRA_CAPITAL = "deployed_capital"
        const val EXTRA_NET = "net_profit"
        const val EXTRA_ROI = "roi_pct"
        const val EXTRA_EXECUTABLE = "executable"
    }
}
