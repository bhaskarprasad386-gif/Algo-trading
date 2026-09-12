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
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import com.google.gson.Gson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

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
    private val runStrategyButton = terminalButton("RUN HISTORICAL STRATEGY", 0xFFFFC857.toInt())
    private var selectedMode = "CURRENT"
    private var selectedDate: String? = null
    private var selectedSymbol: String? = null
    private var selectedContract: String? = null

    init {
        orientation = VERTICAL
        setPadding(8, 8, 8, 8)
        setBackgroundColor(0xFF0D1928.toInt())
        addView(contractLabel)
        addRow(cashBuyButton, cashSellButton)
        addRow(currentButton, nearButton)
        addRow(futureBuyButton, futureSellButton)
        addRow(symbol, cashPrice)
        addRow(cashLots, futurePrice)
        addRow(futureLots, lotSize)
        addRow(cashQty, futureQty)
        addRow(capital, charges)
        addRow(stopLoss, target)
        addRow(slippage, TextView(context))
        addView(payoff, LayoutParams(LayoutParams.MATCH_PARENT, 180).apply { setMargins(0, 8, 0, 8) })
        addView(runStrategyButton, LayoutParams(LayoutParams.MATCH_PARENT, 52).apply { setMargins(0, 4, 0, 4) })
        addView(summary, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.WRAP_CONTENT))
        currentButton.setOnClickListener { selectedMode = "CURRENT"; contractLabel.text = "FUTURE LEG • CURRENT FUTURE" }
        nearButton.setOnClickListener { selectedMode = "NEAR"; contractLabel.text = "FUTURE LEG • NEAR FUTURE" }
        listOf(cashBuyButton, cashSellButton, futureBuyButton, futureSellButton).forEach { button -> button.setOnClickListener { updateSummary() } }
        val watcher = object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) = updateSummary()
            override fun afterTextChanged(s: Editable?) = Unit
        }
        listOf(cashPrice, futurePrice, cashLots, futureLots, lotSize, capital, stopLoss, target, charges, slippage).forEach { it.addTextChangedListener(watcher) }
    }

    fun bindHistoricalSelection(selectedSymbol: String, historicalCash: Double, historicalLot: Double, selectedDate: String, historicalFuture: Double? = null, currentContract: String? = null, nearContract: String? = null) {
        this.selectedSymbol = selectedSymbol
        this.selectedDate = selectedDate
        symbol.setText(selectedSymbol)
        cashPrice.setText("%.2f".format(historicalCash))
        lotSize.setText("%.0f".format(historicalLot))
        historicalFuture?.let { futurePrice.setText("%.2f".format(it)) }
        selectedContract = if (selectedMode == "NEAR") nearContract else currentContract
        contractLabel.text = "FUTURE LEG • ${if (selectedMode == "NEAR") "NEAR" else "CURRENT"} • ${selectedContract ?: "CONTRACT PENDING"}"
        updateSummary()
    }

    private fun updateSummary() {
        val cash = cashPrice.text.toString().toDoubleOrNull() ?: 0.0
        val future = futurePrice.text.toString().toDoubleOrNull() ?: 0.0
        val cashLot = cashLots.text.toString().toDoubleOrNull() ?: 1.0
        val futureLot = futureLots.text.toString().toDoubleOrNull() ?: 1.0
        val lot = lotSize.text.toString().toDoubleOrNull() ?: 0.0
        val qty = if (lot > 0) lot * min(cashLot, futureLot) else 0.0
        val spread = future - cash
        summary.text = "${symbol.text} • $selectedMode • ${selectedContract ?: "-"}\nCash ₹${"%.2f".format(cash)} • Future ₹${"%.2f".format(future)} • Spread ₹${"%.2f".format(spread)} • Qty ${"%.0f".format(qty)}\nHistorical date: ${selectedDate ?: "-"}"
        payoff.setScenario(cash, future, qty, spread)
    }

    private fun runHistoricalStrategy() {
        val selected = selectedSymbol ?: symbol.text.toString().trim()
        val date = selectedDate
        if (selected.isBlank() || date.isNullOrBlank()) {
            summary.text = "Select a historical stock/date first."
            return
        }
        runStrategyButton.isEnabled = false
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val body = mapOf(
                    "spot_instrument" to selected,
                    "underlying" to selected,
                    "date" to date,
                    "mode" to selectedMode,
                    "contract_month" to selectedContract,
                )
                val request = Request.Builder().url("${BuildConfig.BACKEND_BASE_URL}/api/v1/scanner/cash-future/backtest/strategy").post(Gson().toJson(body).toRequestBody("application/json".toMediaType())).build()
                val response = ApiService.client.newCall(request).execute()
                val text = response.body?.string().orEmpty()
                withContext(Dispatchers.Main) {
                    summary.text = "$selected • $date • $selectedMode • ${if (response.isSuccessful) "strategy submitted" else "strategy failed"}\n$text"
                    runStrategyButton.isEnabled = true
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    summary.text = "$selected • $date • strategy failed: ${e.message ?: "unknown error"}"
                    runStrategyButton.isEnabled = true
                }
            }
        }
    }

    private fun field(hint: String, integer: Boolean = false) = EditText(context).apply {
        this.hint = hint
        setTextColor(0xFFE8F1FF.toInt()); setHintTextColor(0xFF6F87A5.toInt()); textSize = 13f
        setPadding(12, 0, 12, 0)
        inputType = if (integer) android.text.InputType.TYPE_CLASS_NUMBER else android.text.InputType.TYPE_CLASS_NUMBER or android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL
        setBackgroundColor(0xFF14253A.toInt())
    }

    private fun quantityField(hint: String) = field(hint, integer = true).apply { isFocusable = false; isClickable = false }

    private fun addRow(left: View, right: View) {
        val row = LinearLayout(context).apply { orientation = HORIZONTAL }
        row.addView(left, LayoutParams(0, 50, 1f).apply { setMargins(0, 3, 5, 3) })
        row.addView(right, LayoutParams(0, 50, 1f).apply { setMargins(5, 3, 0, 3) })
        addView(row)
    }

    private fun terminalButton(textValue: String, background: Int) = Button(context).apply {
        text = textValue; setTextColor(0xFFFFFFFF.toInt()); textSize = 11f; typeface = Typeface.DEFAULT_BOLD
        setBackgroundColor(background); stateListAnimator = null
    }

    private class PayoffGraphView(context: Context) : View(context) {
        private var cash = 0.0
        private var future = 0.0
        private var qty = 0.0
        private var spread = 0.0
        private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 4f; style = Paint.Style.STROKE }
        private val axis = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 2f }

        fun setScenario(cash: Double, future: Double, qty: Double, spread: Double) {
            this.cash = cash; this.future = future; this.qty = qty; this.spread = spread; invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            val w = width.toFloat(); val h = height.toFloat()
            canvas.drawColor(0xFF0A1424.toInt())
            axis.color = 0xFF36506D.toInt()
            canvas.drawLine(30f, h / 2f, w - 20f, h / 2f, axis)
            canvas.drawLine(w / 2f, 20f, w / 2f, h - 20f, axis)
            line.color = if (spread >= 0) 0xFF16B886.toInt() else 0xFFD6455D.toInt()
            val path = Path()
            val center = h / 2f
            val scale = max(1f, abs(spread * qty) / max(1f, h / 2f))
            for (i in 0..100) {
                val x = 30f + (w - 50f) * i / 100f
                val p = spread * qty + ((i - 50) / 50f) * abs(spread * qty + 1.0)
                val y = center - (p / scale).toFloat()
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            canvas.drawPath(path, line)
        }
    }
}
