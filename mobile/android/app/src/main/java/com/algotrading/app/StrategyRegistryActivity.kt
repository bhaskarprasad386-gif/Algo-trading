package com.algotrading.app

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        val title = TextView(this).apply { text = "STRATEGIES"; textSize = 24f }
        val status = TextView(this).apply { text = "Loading backend registry..."; textSize = 13f; setPadding(0, 8, 0, 16) }
        val list = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(title); root.addView(status); root.addView(list); setContentView(root)
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val items = ApiService.retrofitService.appStrategies().strategies.filter { it.enabled }
                withContext(Dispatchers.Main) {
                    status.text = "Backend registry • " + items.size + " enabled strategies"
                    items.forEach { item ->
                        val button = Button(this@StrategyRegistryActivity).apply {
                            text = item.name + " • v" + item.version
                            layoutParams = ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
                        }
                        button.setOnClickListener { openStrategy(item) }
                        list.addView(button)
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) { status.text = "Strategy registry unavailable • " + (e.message ?: "network error") }
            }
        }
    }

    private fun openStrategy(item: StrategyRegistryItem) {
        when (item.screen) {
            "box_spread" -> startActivity(Intent(this, BoxSpreadActivity::class.java))
            "full_fno" -> startActivity(Intent(this, FullFnoBacktestActivity::class.java))
            "cash_future_scanner" -> startActivity(Intent(this, MainActivity::class.java))
            "backend" -> loadBackendStrategy(item.id, item.name)
            else -> AlertDialog.Builder(this).setTitle(item.name)
                .setMessage("Strategy v" + item.version + " is enabled by the backend. Its scanner/backtest API is available without an APK update.")
                .setPositiveButton("OK", null).show()
        }
    }

    private fun loadBackendStrategy(id: String, name: String) = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val payload = when (id) {
                "calendar-spread" -> ApiService.retrofitService.calendarSpreadLive(50)
                "synthetic-future-cash-carry" -> ApiService.retrofitService.syntheticCashCarryLive(50)
                else -> emptyMap()
            }
            val rows = (payload["data"] as? List<*>) ?: emptyList<Any?>()
            val opportunities = (payload["opportunity_count"] as? Number)?.toInt() ?: rows.size
            val mode = payload["mode"]?.toString() ?: "live"
            withContext(Dispatchers.Main) {
                AlertDialog.Builder(this@StrategyRegistryActivity)
                    .setTitle(name)
                    .setMessage(name + "\nMode: " + mode + "\nLive rows: " + rows.size + "\nExecutable opportunities: " + opportunities + "\n\nBid/ask executable scan • paper-safe • live broker orders OFF")
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

}