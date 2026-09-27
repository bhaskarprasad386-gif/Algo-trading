package com.algotrading.app

import android.os.Bundle
import android.widget.TextView
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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_box_spread)
        tvSummary=findViewById(R.id.tvBoxSummary); tvOpportunities=findViewById(R.id.tvBoxOpportunities)
        tvPosition=findViewById(R.id.tvBoxPosition); tvJournal=findViewById(R.id.tvBoxJournal)
        findViewById<android.widget.Button>(R.id.btnBoxRefresh).setOnClickListener { loadOverview() }
        loadOverview()
    }
    private fun loadOverview() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val x=ApiService.retrofitService.boxSpreadOverview()
            withContext(Dispatchers.Main) {
                tvSummary.text="PAPER • Balance ₹"+(x.account?.virtual_balance ?: 0.0)+"\nRealized P&L ₹"+(x.account?.realized_pnl ?: 0.0)+" • Auto lots "+(x.account?.auto_cycle_lots ?: 1)
                tvPosition.text=x.open_position?.let { "OPEN: "+it.underlying+" "+it.direction+"\n"+it.low_strike+" / "+it.high_strike+" • "+it.lots+" lot(s)\nRealized P&L ₹"+it.realized_pnl } ?: "OPEN POSITION: NONE"
                tvOpportunities.text=if(x.live_opportunities.data.isEmpty()) "LIVE OPPORTUNITIES: NONE" else x.live_opportunities.data.joinToString("\n\n") { "${it.underlying} ${it.direction} • ${it.expiry}\n${it.low_strike} → ${it.high_strike} • Edge ₹${it.executable_edge}\nPer lot ₹${it.edge_per_lot} • Liquidity ${it.liquidity_qty}\nBid/Ask: LC ${it.low_call_bid}/${it.low_call_ask} LP ${it.low_put_bid}/${it.low_put_ask} HC ${it.high_call_bid}/${it.high_call_ask} HP ${it.high_put_bid}/${it.high_put_ask}" }
                tvJournal.text="LIFECYCLE\nHistory items: "+((x.history?.get("count") as? Number)?.toInt() ?: 0)+"\nJournal items: "+((x.journal?.get("count") as? Number)?.toInt() ?: 0)
            }
        } catch(e: Exception) { withContext(Dispatchers.Main) { tvSummary.text="LOAD FAILED\n"+(e.message ?: "API error") } }
    }
}
