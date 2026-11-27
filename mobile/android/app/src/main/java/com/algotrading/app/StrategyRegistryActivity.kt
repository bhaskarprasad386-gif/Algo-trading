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
    private lateinit var statusView: TextView
    private lateinit var listView: LinearLayout
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
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(24, 24, 24, 24)
        }
        val title = TextView(this).apply { text = "STRATEGIES"; textSize = 24f }
        statusView = TextView(this).apply {
            text = "Loading backend registry..."
            textSize = 13f
            setPadding(0, 8, 0, 16)
        }
        listView = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(title)
        root.addView(statusView)
        root.addView(listView)
        setContentView(root)
    }

    private fun refreshRegistry() {
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val items = ApiService.retrofitService.appStrategies().strategies.filter { it.enabled }
                withContext(Dispatchers.Main) {
                    statusView.text = "Backend registry • ${items.size} enabled strategies • refresh 30s"
                    listView.removeAllViews()
                    items.forEach { item ->
                        val button = Button(this@StrategyRegistryActivity).apply {
                            text = item.name + " • v" + item.version + " • " + item.data_mode +
                                " • " + item.execution_mode + " • ORDERS " +
                                (if (item.live_orders) "ON" else "OFF") + " • " +
                                item.capabilities.joinToString(",")
                            layoutParams = ViewGroup.LayoutParams(-1, ViewGroup.LayoutParams.WRAP_CONTENT)
                        }
                        button.setOnClickListener { openStrategy(item) }
                        listView.addView(button)
                    }
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    statusView.text = "Strategy registry unavailable • " + (e.message ?: "network error")
                }
            }
        }
    }

    private fun openStrategy(item: StrategyRegistryItem) {
        when (item.screen) {
            "box_spread" -> startActivity(Intent(this, BoxSpreadActivity::class.java))
            "full_fno" -> startActivity(Intent(this, FullFnoBacktestActivity::class.java))
            else -> AlertDialog.Builder(this)
                .setTitle(item.name)
                .setMessage("This workspace is not available in this app build.")
                .setPositiveButton("OK", null)
                .show()
        }
    }
}
