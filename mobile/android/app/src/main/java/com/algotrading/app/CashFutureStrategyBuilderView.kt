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
    private val runStrategyButton = terminalButton("RUN HISTORICAL STRATEGY", 0xFF2673FF.toInt())
    private val exitLabel = TextView(context).apply {
        text = "EXIT / RISK RULES"
        setTextColor(0xFFFF78C8.toInt()); textSize = 11f; typeface = Typeface.DEFAULT_BOLD
        setPadding(4, 8, 4, 4)
    }
    private var cashSide = "BUY"
    private var futureSide = "SELL"
    private var futureMode = "CURRENT FUTURE"
    private var syncingQuantities = false
    private var selectedDate = ""
    private var currentContract: String? = null
    private var nearContract: String? = null

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
        addView(runStrategyButton, LayoutParams(-1, 46).apply { setMargins(0, 4, 0, 0) })
        addView(summary, LayoutParams(-1, 100).apply { setMargins(0, 6, 0, 6) })
        addView(payoff, LayoutParams(-1, 250))

        cashBuyButton.setOnClickListener { selectCashSide("BUY") }
        cashSellButton.setOnClickListener { selectCashSide("SELL") }
        futureBuyButton.setOnClickListener { selectFutureSide("BUY") }
        futureSellButton.setOnClickListener { selectFutureSide("SELL") }
        currentButton.setOnClickListener { selectFutureMode("CURRENT FUTURE") }
        nearButton.setOnClickListener { selectFutureMode("NEAR FUTURE") }
        runStrategyButton.setOnClickListener { runHistoricalStrategy() }
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

    fun bindHistoricalSelection(
        selectedSymbol: String,
        historicalCash: Double,
        historicalLot: Double,
        selectedDate: String = "",
        historicalFuture: Double? = null,
        currentContract: String? = null,
        nearContract: String? = null,
    ) {
        rootView.findViewById<IntradayReplayView>(R.id.intradayReplayView)?.clearStrategyTrades()
        symbol.setText(selectedSymbol)
        lotSize.setText("%.0f".format(historicalLot))
        cashPrice.setText("%.2f".format(historicalCash))
        if (historicalFuture != null && historicalFuture > 0.0) futurePrice.setText("%.2f".format(historicalFuture)) else futurePrice.setText("")
        cashLots.setText("1")
        futureLots.setText("1")
        this.selectedDate = selectedDate
        this.currentContract = currentContract
        this.nearContract = nearContract
        selectCashSide("BUY")
        selectFutureSide("SELL")
        selectFutureMode("CURRENT FUTURE")
        syncQuantitiesFromLots()
        val contractText = buildString {
            append("CURRENT=${currentContract ?: "-"}")
            append(" • NEAR=${nearContract ?: "-"}")
        }
        contractLabel.text = "FUTURE LEG • $contractText"
        summary.text = buildString {
            append("$selectedSymbol • historical selection loaded")
            if (selectedDate.isNotBlank()) append(" • date $selectedDate")
            append("\nCash ₹${"%.2f".format(historicalCash)} • Future ₹${historicalFuture?.let { "%.2f".format(it) } ?: "-"}")
            append(" • lot ${historicalLot.toLong()} • 1 lot = ${historicalLot.toLong()} qty")
            append("\nCash BUY + Future SELL • CURRENT/NEAR contracts preserved")
        }
        futurePrice.requestFocus()
    }

    private fun syncQuantitiesFromLots() {
        syncingQuantities = true
        val lot = lotSize.text.toString().toDoubleOrNull() ?: 0.0
        val cashLotsValue = cashLots.text.toString().toDoubleOrNull() ?: 0.0
        val futureLotsValue = futureLots.text.toString().toDoubleOrNull() ?: 0.0
        cashQty.setText(if (lot > 0.0) "%.0f".format(lot * cashLotsValue) else "")
        futureQty.setText(if (lot > 0.0) "%.0f".format(lot * futureLotsValue) else "")
        syncingQuantities = false
    }

    private fun selectCashSide(side: String) { cashSide = side; cashBuyButton.alpha = if (side == "BUY") 1f else .55f; cashSellButton.alpha = if (side == "SELL") 1f else .55f }
    private fun selectFutureSide(side: String) { futureSide = side; futureBuyButton.alpha = if (side == "BUY") 1f else .55f; futureSellButton.alpha = if (side == "SELL") 1f else .55f }
    private fun selectFutureMode(mode: String) { futureMode = mode; currentButton.alpha = if (mode == "CURRENT FUTURE") 1f else .55f; nearButton.alpha = if (mode == "NEAR FUTURE") 1f else .55f }

    private fun calculatePayoff() {
        val cash = cashPrice.text.toString().toDoubleOrNull() ?: return
        val future = futurePrice.text.toString().toDoubleOrNull() ?: return
        val lot = lotSize.text.toString().toDoubleOrNull() ?: return
        val qty = lot * max(1.0, futureLots.text.toString().toDoubleOrNull() ?: 1.0)
        val spread = if (futureSide == "SELL") future - cash else cash - future
        val capitalValue = capital.text.toString().toDoubleOrNull() ?: 0.0
        summary.text = "$symbol • $futureMode • Cash $cashSide ₹${"%.2f".format(cash)} • Future $futureSide ₹${"%.2f".format(future)} • Spread ₹${"%.2f".format(spread)} • Qty ${qty.toLong()} • Capital ₹${"%.0f".format(capitalValue)}"
        payoff.setScenario(cash, future, qty, spread)
    }

    private fun runHistoricalStrategy() {
        val selected = symbol.text.toString().trim().uppercase()
        val date = selectedDate.trim()
        if (selected.isBlank() || date.isBlank()) {
            summary.text = "Select a historical calendar row first so symbol + trading date are known."
            return
        }
        val mode = if (futureMode == "NEAR FUTURE") "NEAR" else "CURRENT"
        val contract = if (mode == "NEAR") nearContract else currentContract
        val capitalValue = capital.text.toString().toDoubleOrNull() ?: 100_000_000.0
        val chargesValue = charges.text.toString().toDoubleOrNull() ?: 0.0
        val stopLossValue = stopLoss.text.toString().toDoubleOrNull()
        val targetValue = target.text.toString().toDoubleOrNull()
        val slippageValue = slippage.text.toString().toDoubleOrNull() ?: 0.0
        runStrategyButton.isEnabled = false
        summary.text = "$selected • $date • $mode • running durable historical strategy…"
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val payload = mapOf(
                    "strategy_id" to "gap_threshold",
                    "strategy_version" to "1",
                    "start_date" to date,
                    "end_date" to date,
                    "contract_month" to contract,
                    "execution_model" to "gap",
                    "charges_per_trade" to chargesValue,
                    "funding_cost_per_trade" to 0.0,
                    "initial_capital" to capitalValue,
                    "spot_instrument" to selected,
                    "exchange" to "NFO",
                    "underlying" to selected,
                    "timeframe" to "1m",
                    "mode" to mode,
                    "source" to "angelone",
                    "cash_side" to cashSide,
                    "future_side" to futureSide,
                    "stop_loss" to stopLossValue,
                    "target" to targetValue,
                    "slippage_per_share" to slippageValue,
                )
                val json = Gson().toJson(payload)
                val body = json.toRequestBody("application/json".toMediaType())
                val request = Request.Builder()
                    .url(BuildConfig.BACKEND_BASE_URL + "api/v1/backtesting/cash-future/strategy-run")
                    .post(body)
                    .apply {
                        ApiService.getToken(context)?.takeIf { it.isNotBlank() }?.let { addHeader("Authorization", "Bearer $it") }
                    }
                    .build()
                val client = okhttp3.OkHttpClient.Builder().build()
                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) throw IllegalStateException("strategy API ${response.code}: ${response.body?.string() ?: "error"}")
                    val run = Gson().fromJson(response.body?.string().orEmpty(), CashFutureStrategyRunResponse::class.java)
                    val replay = ApiService.retrofitService.cashFutureReplay(date, selected, contractMonth = contract, timeframe = "1m", mode = mode)
                    withContext(Dispatchers.Main) {
                        val replayView = rootView.findViewById<IntradayReplayView>(R.id.intradayReplayView)
                        replayView.setFocusTimestamp(null)
                        replayView.setCashFutureData(replay.series, replay.available_replay_intervals)
                        replayView.setStrategyTrades(run.trades.filter { trade ->
                            trade.symbol.equals(selected, ignoreCase = true) &&
                                (contract.isNullOrBlank() || trade.contract_month == contract)
                        })
                        summary.text = "$selected • $date • $mode • run ${run.run_id}\nTrades ${run.trade_count} • Signals ${run.signal_count} • Net P&L ₹${"%.2f".format(run.net_profit)}\nActual historical replay + BUY/SELL/P&L markers loaded"
                        runStrategyButton.isEnabled = true
                    }
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
            val spreadQty = (spread * qty).toFloat()
            val scale = max(1f, abs(spreadQty) / max(1f, h / 2f))
            for (i in 0..100) {
                val x = 30f + (w - 50f) * i / 100f
                val p = spreadQty + ((i - 50) / 50f) * abs(spreadQty + 1f)
                val y = center - (p / scale)
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            canvas.drawPath(path, line)
        }
    }
}