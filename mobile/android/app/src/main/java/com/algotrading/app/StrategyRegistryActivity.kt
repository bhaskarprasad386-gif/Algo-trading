package com.algotrading.app

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class StrategyRegistryActivity : AppCompatActivity() {
    private val refreshHandler = Handler(Looper.getMainLooper())
    private val refreshRunnable = object : Runnable {
        override fun run() {
            refreshRegistry()
            refreshHandler.postDelayed(this, 30_000L)
        }
    }

    override fun onResume() {
        super.onResume()
        refreshRegistry()
        refreshHandler.removeCallbacks(refreshRunnable)
        refreshHandler.postDelayed(refreshRunnable, 30_000L)
    }

    override fun onPause() {
        refreshHandler.removeCallbacks(refreshRunnable)
        super.onPause()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        val title = TextView(this).apply { text = "STRATEGIES"; textSize = 24f }
        val status = TextView(this).apply { text = "Loading backend registry..."; textSize = 13f; setPadding(0, 8, 0, 16) }
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(title); root.addView(status); root.addView(list); setContentView(root)
        refreshRegistry()
    }

    private fun refreshRegistry() {
        val status = findViewById<TextView>(android.R.id.content)?.let { null }
        // Registry refresh is performed by the same coroutine path used at startup.
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val items = ApiService.retrofitService.appStrategies().strategies.filter { it.enabled }
                withContext(Dispatchers.Main) {
                    val root = (window.decorView.findViewById<LinearLayout>(android.R.id.content)?.getChildAt(0) as? LinearLayout)
                    val statusView = root?.getChildAt(1) as? TextView
                    val listView = root?.getChildAt(2) as? LinearLayout
                    statusView?.text = "Backend registry • " + items.size + " enabled strategies • LIVE refresh 30s"
                    listView?.removeAllViews()
                    items.forEach { item ->
                        val button = Button(this@StrategyRegistryActivity).apply {
                            text = item.name + " • v" + item.version + " • " + item.data_mode + " • " + item.execution_mode + " • ORDERS " + if (item.live_orders) "ON" else "OFF"
                            layoutParams = ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
                        }
                        button.setOnClickListener { openStrategy(item) }
                        listView?.addView(button)
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    val root = (window.decorView.findViewById<LinearLayout>(android.R.id.content)?.getChildAt(0) as? LinearLayout)
                    (root?.getChildAt(1) as? TextView)?.text =
                        "Strategy registry unavailable • " + (e.message ?: "network error")
                }
            }
        }
    }

    private fun openStrategy(item: StrategyRegistryItem) {
        when (item.screen) {
            "box_spread" -> startActivity(Intent(this, BoxSpreadActivity::class.java))
            "full_fno" -> startActivity(Intent(this, FullFnoBacktestActivity::class.java))
            "cash_future_scanner" -> startActivity(Intent(this, MainActivity::class.java))
            "backend" -> loadBackendStrategy(item)
            else -> AlertDialog.Builder(this).setTitle(item.name)
                .setMessage("Strategy v" + item.version + " is enabled by the backend. Its scanner/backtest API is available without an APK update.")
                .setPositiveButton("OK", null).show()
        }
    }

    private fun loadBackendStrategy(item: StrategyRegistryItem) = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val id = item.id
            val name = item.name
            val payload = when (id) {
                "calendar-spread" -> ApiService.retrofitService.calendarSpreadLive(50)
                "synthetic-future-cash-carry" -> ApiService.retrofitService.syntheticCashCarryLive(50)
                else -> emptyMap()
            }
            val rows = (payload["data"] as? List<*>)?.mapNotNull { it as? Map<*, *> } ?: emptyList()
            val opportunities = (payload["opportunity_count"] as? Number)?.toInt() ?: rows.count {
                number(it["executable_edge"] ?: it["long_edge"] ?: it["short_edge"]) > 0.0
            }
            val mode = payload["mode"]?.toString() ?: "live"
            val detail = buildString {
                append(name).append(" • v").append(item.version).append("\n")
                append("Data: ").append(item.data_mode).append(" • Execution: ").append(item.execution_mode).append("\n")
                    .append(" • Live orders: ").append(if (item.live_orders) "ON" else "OFF").append("\n")
                append("Capabilities: ").append(item.capabilities.joinToString(" • ")).append("\n")
                append("Mode: ").append(mode).append(" • Live rows: ").append(rows.size)
                    .append(" • Executable: ").append(opportunities).append("\n\n")
                if (id == "calendar-spread") {
                    append("Rank • Underlying • Near/Far • Long/Short Edge • Edge % • Liquidity\n")
                    rows.take(10).forEachIndexed { index, row ->
                        val underlying = row["underlying"] ?: "-"
                        val contract = (row["near_contract_month"] ?: "-").toString() + " / " + (row["far_contract_month"] ?: "-")
                        val longEdge = number(row["long_edge"]).format2()
                        val shortEdge = number(row["short_edge"]).format2()
                        val longPct = number(row["long_edge_pct"]).format2()
                        val shortPct = number(row["short_edge_pct"]).format2()
                        val liquidity = number(row["liquidity_qty"]).format2()
                        append(index + 1).append(" • ").append(underlying).append(" • ").append(contract)
                            .append(" • L ₹").append(longEdge).append(" / S ₹").append(shortEdge)
                            .append(" • L ").append(longPct).append("% / S ").append(shortPct).append("%")
                            .append(" • ").append(liquidity).append("\n")
                    }
                } else if (id == "synthetic-future-cash-carry") {
                    append("Rank • Underlying • Expiry • Strike • Direction • Future B/A • Call B/A • Put B/A • Edge • Gross P&L\n")
                    rows.take(10).forEachIndexed { index, row ->
                        append(index + 1).append(" • ").append(row["underlying"] ?: "-").append(" • ").append(row["expiry"] ?: "-")
                            .append(" • ").append(row["strike"] ?: "-").append(" • ").append(row["direction"] ?: "-")
                            .append(" • F ").append(number(row["future_bid"]).format2()).append("/").append(number(row["future_ask"]).format2())
                            .append(" • C ").append(number(row["call_bid"]).format2()).append("/").append(number(row["call_ask"]).format2())
                            .append(" • P ").append(number(row["put_bid"]).format2()).append("/").append(number(row["put_ask"]).format2())
                            .append(" • ₹").append(number(row["executable_edge"]).format2())
                            .append(" • ₹").append(number(row["gross_pnl"]).format2()).append("\n")
                    }
                } else {
                    append("Rank • Underlying • Expiry/Contract • Executable Edge • Gross P&L • Liquidity\n")
                    rows.take(10).forEachIndexed { index, row ->
                        val underlying = row["underlying"] ?: row["symbol"] ?: "-"
                        val expiry = row["expiry"] ?: "-"
                        val edge = row["executable_edge"] ?: 0
                        val gross = row["gross_pnl"] ?: 0
                        val liquidity = row["liquidity_qty"] ?: 0
                        append(index + 1).append(" • ").append(underlying).append(" • ").append(expiry)
                            .append(" • ₹").append(number(edge).format2()).append(" • ₹").append(number(gross).format2())
                            .append(" • ").append(number(liquidity).format2()).append("\n")
                    }
                }
                if (rows.isEmpty()) append("NO CURRENT LIVE ROWS\n")
                append("\nBid/ask executable scan • paper-safe • live broker orders OFF")
            }
            withContext(Dispatchers.Main) {
                AlertDialog.Builder(this@StrategyRegistryActivity)
                    .setTitle(name)
                    .setMessage(detail)
                    .setPositiveButton("OK", null)
                    .show()
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                AlertDialog.Builder(this@StrategyRegistryActivity)
                    .setTitle(name + " unavailable")
                    .setMessage(e.message ?: "Live scanner API error")
                    .setPositiveButton("OK", null)
                    .show()
            }
        }
    }

    private fun number(value: Any?): Double = when (value) {
        is Number -> value.toDouble()
        else -> value?.toString()?.toDoubleOrNull() ?: 0.0
    }

    private fun Double.format2(): String = String.format(java.util.Locale.US, "%.2f", this)

}