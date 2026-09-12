package com.algotrading.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.util.AttributeSet
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.max

/** First Cash-Future strategy-builder UI increment: broker-free historical payoff preview. */
class CashFutureStrategyBuilderView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {
    private val symbol = EditText(context).apply { hint = "Selected stock (e.g. SBIN)"; setSingleLine() }
    private val cashPrice = EditText(context).apply { hint = "Cash BUY price"; inputType = 2 or 8192; setSingleLine() }
    private val futurePrice = EditText(context).apply { hint = "Future SELL price"; inputType = 2 or 8192; setSingleLine() }
    private val lotSize = EditText(context).apply { hint = "Historical lot size"; inputType = 2; setSingleLine() }
    private val lots = EditText(context).apply { hint = "Lots"; inputType = 2; setSingleLine(); setText("1") }
    private val capital = EditText(context).apply { hint = "Capital ₹"; inputType = 2 or 8192; setSingleLine(); setText("10000000") }
    private val summary = TextView(context).apply { setTextColor(0xFFDCE6F5.toInt()); textSize = 12f }
    private val payoff = PayoffGraphView(context)

    init {
        orientation = VERTICAL
        setPadding(12, 12, 12, 12)
        setBackgroundColor(0xFF0D1A2A.toInt())
        addView(TextView(context).apply {
            text = "STRATEGY BUILDER • CASH BUY + CURRENT / NEAR FUTURE SELL"
            setTextColor(0xFFFFFFFF.toInt()); textSize = 15f; gravity = Gravity.CENTER_VERTICAL
        }, LayoutParams(-1, 44))
        addRow(symbol, cashPrice)
        addRow(futurePrice, lotSize)
        addRow(lots, capital)
        addView(Button(context).apply {
            text = "BUILD PAYOFF"
            setOnClickListener { calculatePayoff() }
        }, LayoutParams(-1, 44))
        addView(summary, LayoutParams(-1, 72))
        addView(payoff, LayoutParams(-1, 230))
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
            payoff.setScenario(0.0, 0.0, 0.0)
            return
        }
        val basis = future - cash
        val spreadValue = basis * qty
        val capitalPct = if (capitalValue > 0.0) spreadValue / capitalValue * 100.0 else 0.0
        summary.text = "${symbol.text.ifBlank { "Selected stock" }} • Qty ${qty.toLong()} • Basis ₹${"%.2f".format(basis)} • Spread ₹${"%.2f".format(spreadValue)} • Capital impact ${"%.2f".format(capitalPct)}%"
        payoff.setScenario(cash, future, qty)
    }

    private class PayoffGraphView(context: Context) : androidx.appcompat.widget.AppCompatTextView(context) {
        private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF39D98A.toInt(); strokeWidth = 5f; style = Paint.Style.STROKE }
        private val zero = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF70839D.toInt(); strokeWidth = 2f }
        private var cash = 0.0
        private var future = 0.0
        private var qty = 0.0
        init { setBackgroundColor(0xFF07101C.toInt()); setTextColor(0xFF70839D.toInt()); textSize = 10f; text = "PAYOFF PREVIEW • build a scenario"; gravity = Gravity.CENTER }
        fun setScenario(cashPrice: Double, futurePrice: Double, quantity: Double) { cash = cashPrice; future = futurePrice; qty = quantity; invalidate() }
        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            if (cash <= 0.0 || future <= 0.0 || qty <= 0.0) return
            val w = width.toFloat(); val h = height.toFloat(); val midY = h / 2f
            canvas.drawLine(24f, midY, w - 18f, midY, zero)
            val minPrice = cash * 0.85; val maxPrice = cash * 1.15
            val baseBasis = future - cash
            val scale = max(1.0, kotlin.math.abs(baseBasis * qty) + cash * qty * 0.05)
            val path = Path()
            for (i in 0..80) {
                val p = minPrice + (maxPrice - minPrice) * i / 80.0
                val pnl = ((p - cash) - (p - future)) * qty
                val y = midY - (pnl - baseBasis * qty) / scale * (h * 0.35f)
                val x = 24f + (w - 42f) * i / 80f
                if (i == 0) path.moveTo(x, y.toFloat()) else path.lineTo(x, y.toFloat())
            }
            canvas.drawPath(path, line)
        }
    }
}
