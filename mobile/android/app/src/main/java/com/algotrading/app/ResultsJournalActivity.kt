package com.algotrading.app

import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.content.Intent
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ResultsJournalActivity : AppCompatActivity() {
    private lateinit var status: TextView
    private lateinit var output: TextView
    private lateinit var input: EditText
    private lateinit var loadMore: Button
    private var currentRunId: String? = null
    private var tradeCursor = -1
    private var fillCursor = -1
    private var eventCursor = -1
    private var equityTimestamp: Long? = null
    private var equityId: Long? = null
    private var cashFutureMode = false
    private var tradeRows = mutableListOf<Map<*, *>>()
    private var fillRows = mutableListOf<Map<*, *>>()
    private var eventRows = mutableListOf<Map<*, *>>()
    private var equityRows = mutableListOf<Map<*, *>>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(20, 20, 20, 20)
        }
        val title = TextView(this).apply { text = "RESULTS / JOURNAL"; textSize = 22f }
        status = TextView(this).apply { text = "Enter a Universal run ID."; textSize = 13f }
        input = EditText(this).apply { hint = "Universal run ID"; singleLine = true }
        val load = Button(this).apply {
            text = "LOAD RESULT"
            layoutParams = ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        loadMore = Button(this).apply {
            text = "LOAD MORE JOURNAL"
            isEnabled = false
            layoutParams = ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
        }
        output = TextView(this).apply { textSize = 12f; setPadding(0, 16, 0, 0) }
        root.addView(title)
        root.addView(status)
        root.addView(input)
        root.addView(load)
        root.addView(loadMore)
        root.addView(output)
        setContentView(root)

        intent.getStringExtra("RUN_ID")?.takeIf { it.isNotBlank() }?.let { input.setText(it); loadRun(it) }

        load.setOnClickListener {
            val id = input.text.toString().trim()
            if (id.isNotEmpty()) loadRun(id)
        }
        loadMore.setOnClickListener { loadNextPage() }
    }

    private fun loadRun(id: String) = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { status.text = "Loading durable result..." }
        try {
            currentRunId = id; tradeCursor = -1; fillCursor = -1; eventCursor = -1; equityTimestamp = null; equityId = null; cashFutureMode = false
            tradeRows.clear(); fillRows.clear(); eventRows.clear(); equityRows.clear()
            val run: Map<String, Any?>
            try {
                val cashRun = ApiService.retrofitService.cashFutureStrategyRun(id)
                if (cashRun.run_id == id) {
                    cashFutureMode = true
                    run = mapOf("status" to cashRun.status, "run_id" to cashRun.run_id, "strategy_id" to cashRun.strategy_id, "initial_capital" to cashRun.initial_capital, "final_capital" to cashRun.final_capital, "net_profit" to cashRun.net_profit)
                    appendCashFuturePages(
                        ApiService.retrofitService.cashFutureStrategyResultPage(id, "trade", 50, null),
                        ApiService.retrofitService.cashFutureStrategyResultPage(id, "signal", 50, null),
                        ApiService.retrofitService.cashFutureStrategyResultPage(id, "equity", 50, null)
                    )
                } else throw IllegalStateException("run not found")
            } catch (_: Exception) {
                cashFutureMode = false
                run = ApiService.retrofitService.universalRun(id)
                appendPages(ApiService.retrofitService.universalTrades(id, 50, -1), ApiService.retrofitService.universalFills(id, 50, -1), ApiService.retrofitService.universalEvents(id, 50, -1), ApiService.retrofitService.universalEquityPage(id, 50))
            }
            withContext(Dispatchers.Main) { status.text = if (cashFutureMode) "CASH-FUTURE RESULT LOADED • " + id else "UNIVERSAL RESULT LOADED • " + id; loadMore.isEnabled = hasMore(); output.text = formatResult(id, run) }
        } catch (e: Exception) { withContext(Dispatchers.Main) { status.text = "RESULT LOAD FAILED"; loadMore.isEnabled = false; output.text = e.message ?: "API error" } }
    }

    private fun loadNextPage() = lifecycleScope.launch(Dispatchers.IO) {
        val id = currentRunId ?: return@launch
        withContext(Dispatchers.Main) { status.text = "Loading next journal page..." }
        try {
            if (cashFutureMode) {
                appendCashFuturePages(
                    ApiService.retrofitService.cashFutureStrategyResultPage(id, "trade", 50, if (tradeCursor >= 0) tradeCursor else null),
                    ApiService.retrofitService.cashFutureStrategyResultPage(id, "signal", 50, if (eventCursor >= 0) eventCursor else null),
                    ApiService.retrofitService.cashFutureStrategyResultPage(id, "equity", 50, if (equityId != null) equityId!!.toInt() else null)
                )
            } else {
                appendPages(ApiService.retrofitService.universalTrades(id, 50, tradeCursor), ApiService.retrofitService.universalFills(id, 50, fillCursor), ApiService.retrofitService.universalEvents(id, 50, eventCursor), ApiService.retrofitService.universalEquityPage(id, 50, equityTimestamp, equityId))
            }
            withContext(Dispatchers.Main) { status.text = "JOURNAL PAGE LOADED • " + tradeRows.size + " trades • " + fillRows.size + " fills • " + eventRows.size + " events • " + equityRows.size + " equity"; loadMore.isEnabled = hasMore(); output.text = formatJournal(id) }
        } catch (e: Exception) { withContext(Dispatchers.Main) { status.text = "NEXT PAGE FAILED"; output.text = e.message ?: "API error" } }
    }

    private fun appendCashFuturePages(trades: Map<String, Any?>, signals: Map<String, Any?>, equity: Map<String, Any?>) {
        tradeRows.addAll((trades["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        eventRows.addAll((signals["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        equityRows.addAll((equity["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        tradeCursor = (trades["next_cursor"] as? Number)?.toInt() ?: -1
        eventCursor = (signals["next_cursor"] as? Number)?.toInt() ?: -1
        equityTimestamp = null
        equityId = (equity["next_cursor"] as? Number)?.toLong()
        fillCursor = -1
    }

    private fun appendPages(trades: Map<String, Any?>, fills: Map<String, Any?>, events: Map<String, Any?>, equity: Map<String, Any?>) {
        tradeRows.addAll((trades["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        fillRows.addAll((fills["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        eventRows.addAll((events["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        equityRows.addAll((equity["data"] as? List<*>)?.filterIsInstance<Map<*, *>>() ?: emptyList())
        tradeCursor = (trades["next_cursor"] as? Number)?.toInt() ?: -1; fillCursor = (fills["next_cursor"] as? Number)?.toInt() ?: -1; eventCursor = (events["next_cursor"] as? Number)?.toInt() ?: -1
        val cursor = equity["next_cursor"] as? Map<*, *>; equityTimestamp = (cursor?.get("timestamp_ns") as? Number)?.toLong(); equityId = (cursor?.get("equity_id") as? Number)?.toLong()
    }

    private fun hasMore(): Boolean = tradeCursor >= 0 || fillCursor >= 0 || eventCursor >= 0 || (equityTimestamp != null && equityId != null)

    private fun formatResult(id: String, run: Map<String, Any?>): String = buildString {
        append("RUN: ").append(id).append("\n\n"); append("Status: ").append(run["status"] ?: "-").append("\n"); append("Strategy: ").append(run["strategy_id"] ?: run["strategy"] ?: "-").append("\n"); append("Initial Capital: ₹").append(run["initial_capital"] ?: "-").append("\n"); append("Final Equity: ₹").append(run["final_equity"] ?: run["final_capital"] ?: "-").append("\n"); append("Net P&L: ₹").append(run["net_pnl"] ?: run["net_profit"] ?: "-").append("\n"); append("ROI: ").append(run["roi"] ?: "-").append("\n"); append("Max Drawdown: ").append(run["max_drawdown"] ?: "-").append("\n\n"); append(formatJournal(id))
    }

    private fun formatJournal(id: String): String = buildString {
        append("JOURNAL • RUN ").append(id).append("\n"); append("Trades loaded: ").append(tradeRows.size).append("\n"); append("Fills loaded: ").append(fillRows.size).append("\n"); append("Events loaded: ").append(eventRows.size).append("\n"); append("Equity points loaded: ").append(equityRows.size).append("\n\n")
        if (tradeRows.isNotEmpty()) { append("TRADES\n"); tradeRows.takeLast(10).forEach { append(it).append("\n") } }
        if (fillRows.isNotEmpty()) { append("\nFILLS\n"); fillRows.takeLast(10).forEach { append(it).append("\n") } }
        if (eventRows.isNotEmpty()) { append("\nEVENTS\n"); eventRows.takeLast(10).forEach { append(it).append("\n") } }
        if (equityRows.isNotEmpty()) { append("\nEQUITY\n"); equityRows.takeLast(10).forEach { append(it).append("\n") } }
        append("\nDurable ledger • cursor pagination • bounded pages\nPaper-safe • LIVE BROKER ORDERS OFF")
    }
}
