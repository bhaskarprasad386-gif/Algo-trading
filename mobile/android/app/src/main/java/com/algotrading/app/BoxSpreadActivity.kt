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

class BoxSpreadActivity : AppCompatActivity() {
    private lateinit var tvSummary: TextView
    private lateinit var tvOpportunities: TextView
    private val refreshHandler = Handler(Looper.getMainLooper())
    private val refreshTask = object : Runnable {
        override fun run() {
            loadOverview()
            refreshHandler.postDelayed(this, 5000L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_box_spread)
        tvSummary = findViewById(R.id.tvBoxSummary)
        tvOpportunities = findViewById(R.id.tvBoxOpportunities)
        findViewById<android.widget.Button>(R.id.btnBoxRefresh).setOnClickListener { loadOverview() }
        loadOverview()
        refreshHandler.postDelayed(refreshTask, 5000L)
    }

    private fun loadOverview() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val response = ApiService.retrofitService.boxSpreadLive()
            val rows = response["data"] as? List<*> ?: emptyList<Any?>()
            val count = (response["count"] as? Number)?.toInt() ?: rows.size
            val opportunities = (response["opportunity_count"] as? Number)?.toInt() ?: 0
            val rendered = rows.take(30).mapNotNull { item ->
                val row = item as? Map<*, *> ?: return@mapNotNull null
                fun value(key: String) = row[key]?.toString() ?: "—"
                "${value("symbol")} ${value("direction")} • ${value("expiry")}\n" +
                    "${value("low_strike")} → ${value("high_strike")} • Edge ₹${value("executable_edge")}\n" +
                    "Per lot ₹${value("edge_per_lot")} • Liquidity ${value("liquidity_qty")}\n" +
                    "Bid/Ask: LC ${value("low_call_bid")}/${value("low_call_ask")} " +
                    "LP ${value("low_put_bid")}/${value("low_put_ask")} " +
                    "HC ${value("high_call_bid")}/${value("high_call_ask")} " +
                    "HP ${value("high_put_bid")}/${value("high_put_ask")}"
            }
            withContext(Dispatchers.Main) {
                tvSummary.text = "LIVE BOX SPREAD SCANNER • $count rows • $opportunities positive-edge opportunities\nRead-only scanner; no order execution."
                tvOpportunities.text = if (rendered.isEmpty()) "No current live opportunities." else rendered.joinToString("\n\n")
            }
        } catch (error: Exception) {
            withContext(Dispatchers.Main) {
                tvSummary.text = "LIVE SCANNER UNAVAILABLE"
                tvOpportunities.text = error.message ?: "Scanner API error"
            }
        }
    }

    override fun onDestroy() {
        refreshHandler.removeCallbacks(refreshTask)
        super.onDestroy()
    }
}
