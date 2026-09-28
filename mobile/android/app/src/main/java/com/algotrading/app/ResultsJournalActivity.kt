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
        output = TextView(this).apply { textSize = 12f; setPadding(0, 16, 0, 0) }
        root.addView(title)
        root.addView(status)
        root.addView(input)
        root.addView(load)
        root.addView(output)
        setContentView(root)

        intent.getStringExtra("RUN_ID")?.takeIf { it.isNotBlank() }?.let { input.setText(it); loadRun(it) }

        load.setOnClickListener {
            val id = input.text.toString().trim()
            if (id.isNotEmpty()) loadRun(id)
        }
    }

    private fun loadRun(id: String) = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { status.text = "Loading durable result..." }
        try {
            val run = ApiService.retrofitService.universalRun(id)
            val trades = ApiService.retrofitService.universalTrades(id, 50, -1)
            val fills = ApiService.retrofitService.universalFills(id, 50, -1)
            val events = ApiService.retrofitService.universalEvents(id, 50, -1)
            val equity = ApiService.retrofitService.universalEquity(id, 50)
            val text = buildString {
                append("RUN: ").append(id).append("\n\n")
                append("Status: ").append(run["status"] ?: "-").append("\n")
                append("Strategy: ").append(run["strategy_id"] ?: run["strategy"] ?: "-").append("\n")
                append("Initial Capital: ₹").append(run["initial_capital"] ?: "-").append("\n")
                append("Final Equity: ₹").append(run["final_equity"] ?: run["final_capital"] ?: "-").append("\n")
                append("Net P&L: ₹").append(run["net_pnl"] ?: run["net_profit"] ?: "-").append("\n")
                append("ROI: ").append(run["roi"] ?: "-").append("\n")
                append("Max Drawdown: ").append(run["max_drawdown"] ?: "-").append("\n\n")
                append("JOURNAL\n")
                append("Trades: ").append((trades["data"] as? List<*>)?.size ?: 0).append("\n")
                append("Fills: ").append((fills["data"] as? List<*>)?.size ?: 0).append("\n")
                append("Events: ").append((events["data"] as? List<*>)?.size ?: 0).append("\n")
                append("Equity points: ").append((equity["data"] as? List<*>)?.size ?: 0).append("\n\n")
                append("Durable ledger • bounded pages\n")
                append("Paper-safe • LIVE BROKER ORDERS OFF")
            }
            withContext(Dispatchers.Main) {
                status.text = "RESULT LOADED"
                output.text = text
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                status.text = "RESULT LOAD FAILED"
                output.text = e.message ?: "API error"
            }
        }
    }
}
