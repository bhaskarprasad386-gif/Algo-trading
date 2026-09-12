package com.algotrading.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.max

/** Cash-Future strategy builder with a colourful trading-terminal presentation. */
class CashFutureStrategyBuilderView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {
    private val symbol = field("Selected stock (e.g. SBIN)")
    private val cashPrice = field("Cash BUY price")
    private val futurePrice = field("Future SELL price")
    private val lotSize = field("Historical lot size", integer = true)
    private val lots = field("Lots", integer = true).apply { setText("1") }
    private val capital = field("Capital ₹").apply { setText("10000000") }
    private val summary = TextView(context).apply {
        setTextColor(0xFFE8F1FF.toInt()); textSize = 12f; setPadding(12, 8, 12, 8)
        setBackgroundColor(0xFF14253A.toInt())
    }
    private val payoff = PayoffGraphView(context)
    private val contractLabel = TextView(context).apply {
        text = "FUTURE LEG • CURRENT / NEAR FUTURE"
        setTextColor(0xFFFFC857.toInt()); textSize = 11f; typeface = Typeface.DEFAULT_BOLD
        setPadding(4, 8, 4, 4)
    }
    private val currentButton = terminalButton("CURRENT FUTURE", 0xFF2673FF.toInt())
    private val nearButton = terminalButton("NEAR FUTURE", 0xFF7B4DFF.toInt())
    private var futureMode = "CURRENT FUTURE"

    init {
        orientation = VERTICAL
        setPadding(12, 12, 12, 12)
        setBackgroundColor(0xFF0C1728.toInt())
        addView(TextView(context).apply {
            text = "STRATEGY BUILDER"
            setTextColor(0xFF62B0FF.toInt()); textSize = 17f; typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER_VERTICAL
        }, LayoutParams(-1, 40))
        addView(TextView(context).apply {
            text = "CASH BUY  +  FUTURE SELL  •  HISTORICAL CONTRACT / LOT"
            setTextColor(0xFF8FA7C4.toInt()); textSize = 10f
        }, LayoutParams(-1, 28))
        addRow(symbol, cashPrice)
        addView(contractLabel, LayoutParams(-1, 32))
        val contractRow = LinearLayout(context).apply { orientation = HORIZONTAL }
        contractRow.addView(currentButton, LayoutParams(0, 44, 1f).apply { setMargins(0, 2, 5, 4) })
        contractRow.addView(nearButton, LayoutParams(0, 44, 1f).apply { setMargins(5, 2, 0, 4) })
        addView(contractRow)
        addRow(futurePrice, lotSize)
        addRow(lots, capital)
        addView(terminalButton("BUILD PAYOFF", 0xFF16B886.toInt()).apply {
            setOnClickListener { calculatePayoff() }
        }, LayoutParams(-1, 46))
        addView(summary, LayoutParams(-1, 72).apply { setMargins(0, 6, 0, 6) })
        addView(payoff, LayoutParams(-1, 230))
        currentButton.setOnClickListener { selectFutureMode("CURRENT FUTURE") }
        nearButton.setOnClickListener { selectFutureMode("NEAR FUTURE") }
        selectFutureMode(futureMode)
    }

    private fun field(hintText: String, integer: Boolean = false): EditText = EditText(context).apply {
        hint = hintText; setSingleLine(); textSize = 12f
        inputType = if (integer) 2 else 2 or 8192
        setTextColor(0xFFE8F1FF.toInt()); setHintTextColor(0xFF7890AD.toInt())
        setPadding(12, 0, 12, 0); setBackgroundColor(0xFF14253A.toInt())
    }

    private fun terminalButton(label: String, background: Int): Button = Button(context).apply {
        text = label; textSize = 11f; setTextColor(0xFFFFFFFF.toInt()); setBackgroundColor(background)
        typeface = Typeface.DEFAULT_BOLD; isAllCaps = false
    }

    private fun selectFutureMode(mode: String) {
        futureMode = mode
        currentButton.alpha = if (mode == "CURRENT FUTURE") 1f else 0.45f
        nearButton.alpha = if (mode == "NEAR FUTURE") 1f else 0.45f
        contractLabel.text = "FUTURE LEG • $mode • SELL"
    }

    private fun addRow(left: EditText, right: EditText) {
        val row = LinearLayout(context).apply { orientation = HORIZONTAL }
        row.addView(left, LayoutParams(0, 50, 1f).apply { setMargins(0, 3, 6, 3) })
        row.addView(right, LayoutParams(0, 50, 1f).apply { setMargins(6, 3, 0, 3) })
        addView(row, LayoutParams(-1, 56))
    }

    private fun number(view: EditText): Double = view.text.toString().trim().toDoubleOrNull() ?: 0.0

    private fun calculatePayoff() {
        val cash = number(cashPrice)
        val future = number(futurePrice)
        val lot = number(lotSize)
        val qty = lot * max(1.0, number(lots))
        val capitalValue = number(capital)
        if (cash <= 0.0 || future <= 0.0 || lot <= 0.0) {
            summary.text = "Enter historical cash price, future price and lot size."
            payoff.setScenario(0.0, 0.0, 0.0); return
        }
        val basis = future - cash
        val spreadValue = basis * qty
        val capitalPct = if (capitalValue > 0.0) spreadValue / capitalValue * 100.0 else 0.0
        summary.text = "${symbol.text.ifBlank { "Selected stock" }} • $futureMode SELL • Qty ${qty.toLong()} • Basis ₹${"%.2f".format(basis)} • Spread ₹${"%.2f".format(spreadValue)} • Capital impact ${"%.2f".format(capitalPct)}%"
        payoff.setScenario(cash, future, qty)
    }

    private class PayoffGraphView(context: Context) : androidx.appcompat.widget.AppCompatTextView(context) {
        private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF2EE6A6.toInt(); strokeWidth = 5f; style = Paint.Style.STROKE }
        private val zero = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF5C7390.toInt(); strokeWidth = 2f }
        private val accent = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF4DA3FF.toInt(); strokeWidth = 2f }
        private var cash = 0.0; private var future = 0.0; private var qty = 0.0
        init { setBackgroundColor(0xFF07101C.toInt()); setTextColor(0xFF7890AD.toInt()); textSize = 10f; text = "PAYOFF PREVIEW • build a scenario"; gravity = Gravity.CENTER }
        fun setScenario(cashPrice: Double, futurePrice: Double, quantity: Double) { cash = cashPrice; future = futurePrice; qty = quantity; invalidate() }
        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            if (cash <= 0.0 || future <= 0.0 || qty <= 0.0) return
            val w = width.toFloat(); val h = height.toFloat(); val midY = h / 2f
            canvas.drawLine(24f, midY, w - 18f, midY, zero)
            val minPrice = cash * 0.85; val maxPrice = cash * 1.15
            val scale = max(1.0, kotlin.math.abs((future - cash) * qty) + cash * qty * 0.05)
            val path = Path()
            for (i in 0..80) {
                val p = minPrice + (maxPrice - minPrice) * i / 80.0
                val pnl = ((p - cash) - (p - future)) * qty
                val y = midY - (pnl - (future - cash) * qty) / scale * (h * 0.35f)
                val x = 24f + (w - 42f) * i / 80f
                if (i == 0) path.moveTo(x, y.toFloat()) else path.lineTo(x, y.toFloat())
            }
            canvas.drawPath(path, line)
            val entryX = 24f + (w - 42f) * ((future - cash) / (cash * 0.30)).coerceIn(0.0, 1.0).toFloat()
            canvas.drawLine(entryX, 12f, entryX, h - 12f, accent)
        }
    }
}
