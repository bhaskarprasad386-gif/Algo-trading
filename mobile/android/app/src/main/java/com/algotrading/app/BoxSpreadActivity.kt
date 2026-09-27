package com.algotrading.app

import android.os.Bundle
import android.widget.TextView
import android.widget.EditText
import android.widget.Button
import android.os.Handler
import android.os.Looper
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class BoxSpreadActivity : AppCompatActivity() {
    private lateinit var tvSummary: TextView
    private lateinit var tvOpportunities: TextView
    private lateinit var tvPosition: TextView
    private lateinit var tvJournal: TextView
    private lateinit var etLots: EditText
    private lateinit var etMinPnl: EditText
    private lateinit var tvAction: TextView
    private val refreshHandler = Handler(Looper.getMainLooper())
    private val refreshTask = object : Runnable { override fun run() { loadOverview(); refreshHandler.postDelayed(this, 5000L) } }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_box_spread)
        tvSummary=findViewById(R.id.tvBoxSummary); tvOpportunities=findViewById(R.id.tvBoxOpportunities)
        tvPosition=findViewById(R.id.tvBoxPosition); tvJournal=findViewById(R.id.tvBoxJournal)
        etLots=findViewById(R.id.etBoxLots); etMinPnl=findViewById(R.id.etBoxMinPnl)
        tvAction=TextView(this); tvAction.setTextColor(android.graphics.Color.WHITE)
        (tvJournal.parent as android.view.ViewGroup).addView(tvAction)
        findViewById<Button>(R.id.btnBoxAutoEntry).setOnClickListener { runAction { ApiService.retrofitService.boxSpreadAutoEntry(lots()) } }
        findViewById<Button>(R.id.btnBoxAutoExit).setOnClickListener { runAction { ApiService.retrofitService.boxSpreadAutoExit(minPnl()) } }
        findViewById<Button>(R.id.btnBoxCycle).setOnClickListener { runAction { ApiService.retrofitService.boxSpreadCycle(lots(), minPnl()) } }
        findViewById<android.widget.Button>(R.id.btnBoxRefresh).setOnClickListener { loadOverview() }
        loadOverview()
        refreshHandler.postDelayed(refreshTask, 5000L)
    }
    private fun lots(): Int = etLots.text.toString().toIntOrNull()?.coerceAtLeast(1) ?: 1
    private fun minPnl(): Double = etMinPnl.text.toString().toDoubleOrNull()?.coerceAtLeast(0.0) ?: 0.0
    private fun runAction(block: suspend () -> Map<String, Any?>) = lifecycleScope.launch(Dispatchers.IO) { try { val r=block(); withContext(Dispatchers.Main) { tvAction.text="ACTION: "+(r["status"] ?: "success")+"\n"+(r["gross_pnl"] ?: r["realized_pnl"] ?: r["reason"] ?: "completed"); loadOverview() } } catch(e: Exception) { withContext(Dispatchers.Main) { tvAction.text="ACTION FAILED\n"+(e.message ?: "API error") } } }

    private fun loadOverview() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val x=ApiService.retrofitService.boxSpreadOverview()
            withContext(Dispatchers.Main) {
                tvSummary.text="PAPER • Balance ₹"+(x.account?.virtual_balance ?: 0.0)+"\nRealized P&L ₹"+(x.account?.realized_pnl ?: 0.0)+" • Auto lots "+(x.account?.auto_cycle_lots ?: 1)
                tvPosition.text=x.open_position?.let { "OPEN: "+it.underlying+" "+it.direction+"\n"+it.low_strike+" / "+it.high_strike+" • "+it.lots+" lot(s)\nRealized P&L ₹"+it.realized_pnl } ?: "OPEN POSITION: NONE"
                tvOpportunities.text=if(x.live_opportunities.data.isEmpty()) "LIVE OPPORTUNITIES: NONE" else x.live_opportunities.data.joinToString("\n\n") { "${it.underlying} ${it.direction} • ${it.expiry}\n${it.low_strike} → ${it.high_strike} • Edge ₹${it.executable_edge}\nPer lot ₹${it.edge_per_lot} • Liquidity ${it.liquidity_qty}\nBid/Ask: LC ${it.low_call_bid}/${it.low_call_ask} LP ${it.low_put_bid}/${it.low_put_ask} HC ${it.high_call_bid}/${it.high_call_ask} HP ${it.high_put_bid}/${it.high_put_ask}" }
                tvJournal.text=buildString {\n                    append("HISTORY (").append(x.history.count).append(")\\n")\n                    x.history.items.take(10).forEach { h ->\n                        append("#").append(h.id).append(" ").append(h.underlying).append(" ").append(h.direction)\n                            .append(" ").append(h.low_strike).append("→").append(h.high_strike)\n                            .append(" • ").append(h.lots).append(" lot(s) • P&L ₹").append(h.realized_pnl)\n                            .append(if (h.is_open) " • OPEN" else " • CLOSED").append("\\n")\n                    }\n                    append("\\nJOURNAL (").append(x.journal.count).append(")\\n")\n                    x.journal.items.take(10).forEach { j ->\n                        append("#").append(j["id"] ?: "-").append(" ").append(j["event"] ?: "EVENT")\n                            .append(" • ").append(j["created_at"] ?: "").append("\\n")\n                    }\n                }
            }
        } catch(e: Exception) { withContext(Dispatchers.Main) { tvSummary.text="LOAD FAILED\n"+(e.message ?: "API error") } }
    }
}
