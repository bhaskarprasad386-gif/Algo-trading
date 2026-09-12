package com.algotrading.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.text.Editable
import android.text.TextWatcher
import android.util.AttributeSet
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlin.math.abs
import kotlin.math.max

/** Cash-Future strategy builder with a colourful trading-terminal presentation. */
class CashFutureStrategyBuilderView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : LinearLayout(context, attrs) {
    private val symbol = field("Selected stock (e.g. SBIN)")
    private val cashPrice = field("Cash entry price")
    private val cashLots = field("Cash lots", integer = true).apply { setText("1") }
    private val cashQty = quantityField("Cash quantity • auto")
    private val futurePrice = field("Future entry price")
    private val futureLots = field("Future lots", integer = true).apply { setText("1") }
    private val futureQty = quantityField("Future quantity • auto")
    private val lotSize = field("Historical lot size", integer = true)
    private val capital = field("Capital ₹").apply { setText("10000000") }
    private val stopLoss = field("Stop-loss ₹")
    private val target = field("Target ₹")
    private val charges = field("Charges ₹").apply { setText("0") }
    private val slippage = field("Slippage ₹/share").apply { setText("0") }
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
    private val cashBuyButton = terminalButton("CASH • BUY", 0xFF16B886.toInt())
    private val cashSellButton = terminalButton("CASH • SELL", 0xFFD6455D.toInt())
    private val futureBuyButton = terminalButton("FUTURE • BUY", 0xFF16B886.toInt())
    private val futureSellButton = terminalButton("FUTURE • SELL", 0xFFD6455D.toInt())
    private val exitLabel = TextView(context).apply {
        text = "EXIT / RISK RULES"
        setTextColor(0xFFFF78C8.toInt()); textSize = 11f; typeface = Typeface.DEFAULT_BOLD
        setPadding(4, 8, 4, 4)
    }
    private var cashSide = "BUY"
    private var futureSide = "SELL"
    private var futureMode = "CURRENT FUTURE"
    private var syncingQuantities = false

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
            text = "CASH BUY / SELL  +  FUTURE BUY / SELL • LOT + QUANTITY"
            setTextColor(0xFF8FA7C4.toInt()); textSize = 10f
        }, LayoutParams(-1, 28))
        addRow(symbol, lotSize)
        addView(TextView(context).apply {
            text = "CASH LEG • ORDER SIDE"
            setTextColor(0xFF8FF0C5.toInt()); textSize = 11f; typeface = Typeface.DEFAULT_BOLD
            setPadding(4, 8, 4, 4)
        }, LayoutParams(-1, 32))
        val cashSideRow = LinearLayout(context).apply { orientation = HORIZONTAL }
        cashSideRow.addView(cashBuyButton, LayoutParams(0, 44, 1f).apply { setMargins(0, 2, 5, 4) })
        cashSideRow.addView(cashSellButton, LayoutParams(0, 44, 1f).apply { setMargins(5, 2, 0, 4) })
        addView(cashSideRow)
        addRow(cashPrice, cashLots)
        addView(TextView(context).apply {
            text = "CASH QUANTITY • historical lot size × cash lots"
            setTextColor(0xFF61B0FF.toInt()); textSize = 10f
        }, LayoutParams(-1, 24))
        addView(cashQty, LayoutParams(-1, 50).apply { setMargins(0, 2, 0, 4) })

        addView(contractLabel, LayoutParams(-1, 32))
        val contractRow = LinearLayout(context).apply { orientation = HORIZONTAL }
        contractRow.addView(currentButton, LayoutParams(0, 44, 1f).apply { setMargins(0, 2, 5, 4) })
        contractRow.addView(nearButton, LayoutParams(0, 44, 1f).apply { setMargins(5, 2, 0, 4) })
        addView(contractRow)
        val futureSideRow = LinearLayout(context).apply { orientation = HORIZONTAL }
        futureSideRow.addView(futureBuyButton, LayoutParams(0, 44, 1f).apply { setMargins(0, 2, 5, 4) })
        futureSideRow.addView(futureSellButton, LayoutParams(0, 44, 1f).apply { setMargins(5, 2, 0, 4) })
        addView(futureSideRow)
        addRow(futurePrice, futureLots)
        addView(TextView(context).apply {
            text = "FUTURE QUANTITY • historical lot size × future lots"
            setTextColor(0xFFB78CFF.toInt()); textSize = 10f
        }, LayoutParams(-1, 24))
        addView(futureQty, LayoutParams(-1, 50).apply { setMargins(0, 2, 0, 4) })

        addRow(capital, charges)
        addView(exitLabel, LayoutParams(-1, 32))
        addRow(stopLoss, target)
        addView(slippage, LayoutParams(-1, 50).apply { setMargins(0, 3, 0, 3) })
        addView(terminalButton("BUILD PAYOFF • HISTORICAL SCENARIO", 0xFF16B886.toInt()).apply {
            setOnClickListener { calculatePayoff() }
        }, LayoutParams(-1, 46))
        addView(summary, LayoutParams(-1, 100).apply { setMargins(0, 6, 0, 6) })
        addView(payoff, LayoutParams(-1, 250))

        cashBuyButton.setOnClickListener { selectCashSide("BUY") }
        cashSellButton.setOnClickListener { selectCashSide("SELL") }
        futureBuyButton.setOnClickListener { selectFutureSide("BUY") }
        futureSellButton.setOnClickListener { selectFutureSide("SELL") }
        currentButton.setOnClickListener { selectFutureMode("CURRENT FUTURE") }
        nearButton.setOnClickListener { selectFutureMode("NEAR FUTURE") }
        val quantityWatcher = object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                if (!syncingQuantities) syncQuantitiesFromLots()
            }
            override fun afterTextChanged(s: Editable?) = Unit
        }
        cashLots.addTextChangedListener(quantityWatcher)
        futureLots.addTextChangedListener(quantityWatcher)
        lotSize.addTextChangedListener(quantityWatcher)
        selectCashSide(cashSide)
        selectFutureSide(futureSide)
        selectFutureMode(futureMode)
        syncQuantitiesFromLots()
    }

    private fun field(hintText: String, integer: Boolean = false): EditText = EditText(context).apply {
        hint = hintText; setSingleLine(); textSize = 12f
        inputType = if (integer) 2 else 2 or 8192
        setTextColor(0xFFE8F1FF.toInt()); setHintTextColor(0xFF7890AD.toInt())
        setPadding(12, 0, 12, 0); setBackgroundColor(0xFF14253A.toInt())
    }

    private fun quantityField(hintText: String): EditText = field(hintText, integer = true).apply {
        isFocusable = false
        isClickable = false
        isLongClickable = false
        alpha = 0.92f
    }

    private fun terminalButton(label: String, background: Int): Button = Button(context).apply {
        text = label; textSize = 11f; setTextColor(0xFFFFFFFF.toInt()); setBackgroundColor(background)
        typeface = Typeface.DEFAULT_BOLD; isAllCaps = false
    }

    private fun selectCashSide(side: String) {
        cashSide = side
        cashBuyButton.alpha = if (side == "BUY") 1f else 0.45f
        cashSellButton.alpha = if (side == "SELL") 1f else 0.45f
    }

    private fun selectFutureSide(side: String) {
        futureSide = side
        futureBuyButton.alpha = if (side == "BUY") 1f else 0.45f
        futureSellButton.alpha = if (side == "SELL") 1f else 0.45f
        contractLabel.text = "FUTURE LEG • $futureMode • $side"
    }

    private fun selectFutureMode(mode: String) {
        futureMode = mode
        currentButton.alpha = if (mode == "CURRENT FUTURE") 1f else 0.45f
        nearButton.alpha = if (mode == "NEAR FUTURE") 1f else 0.45f
        contractLabel.text = "FUTURE LEG • $mode • $futureSide"
    }

    /** Keeps both leg quantities as exact historical-lot multiples. */
    private fun syncQuantitiesFromLots() {
        if (syncingQuantities) return
        val lot = lotSize.text.toString().trim().toDoubleOrNull()
        val cashLotCount = cashLots.text.toString().trim().toDoubleOrNull()
        val futureLotCount = futureLots.text.toString().trim().toDoubleOrNull()
        if (lot == null || lot <= 0.0) {
            syncingQuantities = true
            cashQty.setText("")
            futureQty.setText("")
            syncingQuantities = false
            return
        }
        syncingQuantities = true
        if (cashLotCount != null && cashLotCount > 0.0) {
            cashQty.setText((lot * cashLotCount).toLong().toString())
        }
        if (futureLotCount != null && futureLotCount > 0.0) {
            futureQty.setText((lot * futureLotCount).toLong().toString())
        }
        syncingQuantities = false
    }

    /** Pre-fills the builder from a selected historical calendar/ranking row. */
    fun bindHistoricalSelection(selectedSymbol: String, historicalCash: Double, historicalLot: Double) {
        symbol.setText(selectedSymbol)
        lotSize.setText("%.0f".format(historicalLot))
        cashPrice.setText("%.2f".format(historicalCash))
        cashLots.setText("1")
        futureLots.setText("1")
        selectCashSide("BUY")
        selectFutureSide("SELL")
        syncQuantitiesFromLots()
        futurePrice.requestFocus()
        summary.text = "$selectedSymbol • historical selection loaded • Cash BUY + Future SELL • lot ${historicalLot.toLong()} • 1 lot = ${historicalLot.toLong()} qty"
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
        val cashLotsValue = max(0.0, number(cashLots))
        val futureLotsValue = max(0.0, number(futureLots))
        val cashQuantity = max(0.0, number(cashQty))
        val futureQuantity = max(0.0, number(futureQty))
        val cashUnits = if (cashQuantity > 0.0) cashQuantity else lot * cashLotsValue
        val futureUnits = if (futureQuantity > 0.0) futureQuantity else lot * futureLotsValue
        val capitalValue = number(capital)
        val stop = number(stopLoss)
        val tgt = number(target)
        val fee = max(0.0, number(charges))
        val slip = max(0.0, number(slippage))
        if (cash <= 0.0 || future <= 0.0 || lot <= 0.0 || cashUnits <= 0.0 || futureUnits <= 0.0) {
            summary.text = "Enter historical prices, lot size and both leg quantities."
            payoff.setScenario(0.0, 0.0, 0.0, cashSide, futureSide); return
        }
        val cashSign = if (cashSide == "BUY") 1.0 else -1.0
        val futureSign = if (futureSide == "BUY") 1.0 else -1.0
        val grossCash = -cashSign * cash * cashUnits
        val grossFuture = -futureSign * future * futureUnits
        val grossNotional = abs(grossCash) + abs(grossFuture)
        val executionCost = fee + slip * (cashUnits + futureUnits)
        val netExposure = grossCash + grossFuture
        val capitalPct = if (capitalValue > 0.0) grossNotional / capitalValue * 100.0 else 0.0
        summary.text = buildString {
            append("${symbol.text.ifBlank { "Selected stock" }} • Cash $cashSide + $futureSideModeLabel\n")
            append("Cash qty ${cashUnits.toLong()} • Future qty ${futureUnits.toLong()} • Lot ${lot.toLong()}\n")
            append("Cash notional ₹${"%.2f".format(abs(grossCash))} • Future notional ₹${"%.2f".format(abs(grossFuture))} • Exposure ₹${"%.2f".format(netExposure)}\n")
            append("Costs ₹${"%.2f".format(executionCost)} • Capital utilisation ${"%.2f".format(capitalPct)}% • SL ${if (stop > 0) "₹%.2f".format(stop) else "—"} • Target ${if (tgt > 0) "₹%.2f".format(tgt) else "—"}")
        }
        payoff.setScenario(cash, future, cashUnits, cashSide, futureSide)
    }

    private val futureSideModeLabel: String
        get() = "$futureMode $futureSide"

    private class PayoffGraphView(context: Context) : androidx.appcompat.widget.AppCompatTextView(context) {
        private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF2EE6A6.toInt(); strokeWidth = 5f; style = Paint.Style.STROKE }
        private val zero = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF5C7390.toInt(); strokeWidth = 2f }
        private val accent = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF4DA3FF.toInt(); strokeWidth = 2f }
        private val marker = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFF78C8.toInt(); strokeWidth = 3f }
        private var cash = 0.0; private var future = 0.0; private var qty = 0.0
        private var cashSide = "BUY"; private var futureSide = "SELL"
        init { setBackgroundColor(0xFF07101C.toInt()); setTextColor(0xFF7890AD.toInt()); textSize = 10f; text = "PAYOFF PREVIEW • build a historical scenario"; gravity = Gravity.CENTER }
        fun setScenario(cashPrice: Double, futurePrice: Double, quantity: Double, cashOrderSide: String = "BUY", futureOrderSide: String = "SELL") {
            cash = cashPrice; future = futurePrice; qty = quantity; cashSide = cashOrderSide; futureSide = futureOrderSide; invalidate()
        }
        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            if (cash <= 0.0 || future <= 0.0 || qty <= 0.0) return
            val w = width.toFloat(); val h = height.toFloat(); val midY = h / 2f
            canvas.drawLine(24f, midY, w - 18f, midY, zero)
            val minPrice = cash * 0.85; val maxPrice = cash * 1.15
            val scale = max(1.0, abs(future - cash) * qty + cash * qty * 0.05)
            val path = Path()
            for (i in 0..80) {
                val p = minPrice + (maxPrice - minPrice) * i / 80.0
                val cashPnl = if (cashSide == "BUY") (p - cash) * qty else (cash - p) * qty
                val futurePnl = if (futureSide == "BUY") (p - future) * qty else (future - p) * qty
                val pnl = cashPnl + futurePnl
                val y = midY - pnl / scale * (h * 0.35f)
                val x = 24f + (w - 42f) * i / 80f
                if (i == 0) path.moveTo(x, y.toFloat()) else path.lineTo(x, y.toFloat())
            }
            canvas.drawPath(path, line)
            val cashX = 24f + (w - 42f) * 0.5f
            val futureX = 24f + (w - 42f) * ((future - minPrice) / (maxPrice - minPrice)).coerceIn(0.0, 1.0).toFloat()
            canvas.drawLine(cashX, 12f, cashX, h - 12f, marker)
            canvas.drawLine(futureX, 12f, futureX, h - 12f, accent)
        }
    }
}
