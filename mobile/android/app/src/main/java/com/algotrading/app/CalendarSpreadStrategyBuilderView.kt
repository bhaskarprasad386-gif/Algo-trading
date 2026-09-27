package com.algotrading.app

import android.content.Context
import android.util.AttributeSet
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Spinner
import android.widget.ArrayAdapter
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class CalendarSpreadStrategyBuilderView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : LinearLayout(context, attrs) {
    private val underlying = field("Underlying (e.g. SBIN)")
    private val startDate = field("Start date YYYY-MM-DD")
    private val endDate = field("End date YYYY-MM-DD")
    private val start = field("Start HH:mm:ss").apply { setText("09:15:00") }
    private val end = field("End HH:mm:ss").apply { setText("15:30:00") }
    private val near = field("Near contract YYYY-MM")
    private val far = field("Far contract YYYY-MM")
    private val instrumentSpinner = Spinner(context)
    private var universe = emptyList<CalendarSpreadInstrument>()
    private var selectedExchange = "AUTO"
    private var selectedInstrumentType = "AUTO"
    private val status = TextView(context).apply { setTextColor(0xFFE8F1FF.toInt()); textSize=11f; setPadding(10,8,10,8) }
    private val replay = TextView(context).apply { setTextColor(0xFFBFD4EF.toInt()); textSize=10f; setPadding(10,8,10,8) }
    private var direction="LONG_NEAR_SHORT_FAR"
    private var timeframe="1s"
    init {
        orientation=VERTICAL; setPadding(12,12,12,12); setBackgroundColor(0xFF0C1728.toInt())
        addView(label("CALENDAR SPREAD • POSITIONAL HISTORICAL STRATEGY")); addRow(underlying,startDate); addRow(startDate,endDate); addView(instrumentSpinner); addView(button("LOAD ALL INDEX / F&O UNIVERSE").apply{setOnClickListener{loadUniverse()}}); addRow(start,end); addRow(near,far)
        val row=LinearLayout(context).apply{orientation=HORIZONTAL}
        val long=button("LONG NEAR / SHORT FAR"); val short=button("SHORT NEAR / LONG FAR")
        row.addView(long,LayoutParams(0,44,1f)); row.addView(short,LayoutParams(0,44,1f)); addView(row)
        long.setOnClickListener{direction="LONG_NEAR_SHORT_FAR";long.alpha=1f;short.alpha=.55f}; short.setOnClickListener{direction="SHORT_NEAR_LONG_FAR";short.alpha=1f;long.alpha=.55f}; long.performClick()
        addView(button("AUTO-LOAD NEAR / FAR CONTRACTS").apply{setOnClickListener{loadContracts()}})\n        instrumentSpinner.onItemSelectedListener=object: android.widget.AdapterView.OnItemSelectedListener { override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {} override fun onItemSelected(parent: android.widget.AdapterView<*>?, view: android.view.View?, position: Int, id: Long){ applyInstrument(position) } }
        addView(button("RUN HISTORICAL CALENDAR SPREAD").apply{setOnClickListener{runStrategy()}})
        addView(label("REPLAY TIMEFRAME"))
        val tfRow=LinearLayout(context).apply{orientation=HORIZONTAL}
        listOf("1s","30s","1m","5m","15m","30m","1h").forEach{tf->tfRow.addView(button(tf).apply{setOnClickListener{timeframe=tf;update(tfRow,tf)}},LayoutParams(0,40,1f))}; addView(tfRow); update(tfRow,"1s")
        addView(button("LOAD HISTORICAL REPLAY").apply{setOnClickListener{loadReplay()}}); addView(status); addView(replay)
    }
    private fun loadUniverse(){
        val d=startDate.text.toString().trim()
        if(!Regex("^\\d{4}-\\d{2}-\\d{2}$").matches(d)){ status.text="Enter trading date first."; return }
        status.text="Loading Angel One historical Index / Stock F&O / Commodity universe..."
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val r=ApiService.retrofitService.calendarSpreadInstruments(mapOf("as_of" to d))
                withContext(Dispatchers.Main) {
                    universe=r.instruments
                    val labels=universe.map { it.underlying+" • "+it.instrument_type.replace("_"," ")+" • "+it.exchange+" • "+it.near_contract_month+"/"+(it.far_contract_month ?: "-") }
                    instrumentSpinner.adapter=ArrayAdapter(context, android.R.layout.simple_spinner_dropdown_item, labels)
                    status.text="Universe "+r.count+" • priority: INDEX > STOCK > COMMODITY. Select instrument to auto-fill Near/Far."
                    if(universe.isNotEmpty()) applyInstrument(0)
                }
            } catch(e:Exception) {
                withContext(Dispatchers.Main){ status.text="Universe load failed • "+(e.message ?: "API error") }
            }
        }
    }

    private fun applyInstrument(index:Int){
        if(index !in universe.indices) return
        val item=universe[index]
        underlying.setText(item.underlying)
        selectedExchange=item.exchange
        selectedInstrumentType=item.instrument_type
        if(item.far_contract_month != null){
            near.setText(item.near_contract_month)
            far.setText(item.far_contract_month)
        }
        status.text=item.instrument_type+" • "+item.exchange+" • "+item.near_symbol+" + "+(item.far_symbol ?: "no Far")+" • lot "+item.lot_size
    }
    private fun loadContracts(){
        val s=underlying.text.toString().trim().uppercase()
        val d=startDate.text.toString().trim()
        if(s.isBlank() || !Regex("^\\d{4}-\\d{2}-\\d{2}$").matches(d)){ status.text="Enter symbol and trading date first."; return }
        status.text="Loading point-in-time Near/Far contracts…"
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val r=ApiService.retrofitService.calendarSpreadContractMonths(mapOf("underlying" to s,"exchange" to selectedExchange,"instrument_type" to selectedInstrumentType,"as_of" to d))
                withContext(Dispatchers.Main) {
                    if(r.contracts.size>=2){
                        near.setText(r.contracts[0].contract_month)
                        far.setText(r.contracts[1].contract_month)
                        status.text="Auto-loaded "+r.contracts[0].symbol+" + "+r.contracts[1].symbol+" • lot "+r.contracts[0].lot_size
                    } else status.text="Fewer than two historical contracts available."
                }
            } catch(e:Exception) {
                withContext(Dispatchers.Main){ status.text="Contract discovery failed • "+(e.message ?: "API error") }
            }
        }
    }

    private fun runStrategy(){ val s=underlying.text.toString().trim().uppercase(); val sd=startDate.text.toString().trim(); val ed=endDate.text.toString().trim(); val n=near.text.toString().trim(); val f=far.text.toString().trim(); val a=stamp(sd,start.text.toString()); val z=stamp(ed,end.text.toString()); if(s.isBlank()||!date(sd)||!date(ed)||sd>ed||!month(n)||!month(f)||f<=n||a==null||z==null||a>z){status.text="Enter valid positional date range/time and Near < Far YYYY-MM.";return}; status.text="Running durable Calendar Spread…"; CoroutineScope(Dispatchers.IO).launch{try{val r=ApiService.retrofitService.calendarSpreadHistoricalStrategyRun(mapOf("underlying" to s,"exchange" to "AUTO","instrument_type" to "AUTO","start_date" to sd,"end_date" to ed,"near_contract_month" to n,"far_contract_month" to f,"source_timeframe" to "1s","replay_timeframe" to timeframe,"start_timestamp" to a,"end_timestamp" to z,"direction" to direction,"fees_per_unit" to 0.0,"initial_capital" to 100_000_000.0));withContext(Dispatchers.Main){status.text="${r.status.uppercase()} • ${r.run_id}\n${r.direction} • Trades ${r.trade_count}\nCompleted ${r.completed_trades} • Unresolved ${r.unresolved_trades}\nNet P&L ₹${"%.2f".format(r.net_profit)}\n"+r.trades.joinToString("\n"){t -> "TRADE ${t.entry_time} → ${t.exit_time} • qty ${t.quantity} • gross ₹${"%.2f".format(t.gross_profit)} • net ₹${"%.2f".format(t.net_profit)}"}; rootView.findViewById<IntradayReplayView>(R.id.intradayReplayView)?.setStrategyTrades(r.trades.map { t -> CashFutureTradeMarker(entry_time=t.entry_time, exit_time=t.exit_time, entry_cash_price=0.0, entry_future_price=t.entry_price, exit_cash_price=0.0, exit_future_price=t.exit_price, net_profit=t.net_profit) })}}catch(e:Exception){withContext(Dispatchers.Main){status.text="Calendar strategy failed • ${e.message ?: "API error"}"}}}}
    private fun loadReplay(){val s=underlying.text.toString().trim().uppercase();val sd=startDate.text.toString().trim();val ed=endDate.text.toString().trim();val n=near.text.toString().trim();val f=far.text.toString().trim();if(s.isBlank()||!date(sd)||!date(ed)||sd>ed||!month(n)||!month(f)||f<=n){replay.text="Enter positional date range and ordered Near/Far months.";return};CoroutineScope(Dispatchers.IO).launch{try{val r=ApiService.retrofitService.calendarSpreadHistoricalReplay(mapOf("underlying" to s,"exchange" to "AUTO","instrument_type" to "AUTO","start_date" to d,"end_date" to d,"near_contract_month" to n,"far_contract_month" to f,"source_timeframe" to "1s","replay_timeframe" to timeframe,"start_timestamp" to stamp(sd,start.text.toString()),"end_timestamp" to stamp(ed,end.text.toString())));withContext(Dispatchers.Main){replay.text="${r.replay_timeframe.uppercase()} • source ${r.source_timeframe}\nPoints ${r.count} • min interval ${r.source_min_interval_seconds ?: "-"} sec\nAvailable: ${r.available_replay_intervals.joinToString(", ")}"; rootView.findViewById<IntradayReplayView>(R.id.intradayReplayView)?.setCalendarSpreadData(r.series, r.available_replay_intervals); rootView.findViewById<IntradayReplayView>(R.id.intradayReplayView)?.setReplayMode(r.replay_timeframe.let { when(it){"1s"->1L;"30s"->30L;"1m"->60L;"5m"->300L;"15m"->900L;"30m"->1800L;"1h"->3600L;else->60L} })}}catch(e:Exception){withContext(Dispatchers.Main){replay.text="Replay failed • ${e.message ?: "API error"}"}}}}
    private fun update(row:LinearLayout,selected:String){for(i in 0 until row.childCount)row.getChildAt(i).alpha=if((row.getChildAt(i) as Button).text.toString()==selected)1f else .55f}
    private fun date(v:String)=Regex("^\\d{4}-\\d{2}-\\d{2}$").matches(v)
    private fun month(v:String)=Regex("^\\d{4}-\\d{2}$").matches(v)&&v.substring(5,7).toIntOrNull() in 1..12
    private fun stamp(d:String,t:String):String?{if(!Regex("^\\d{4}-\\d{2}-\\d{2}$").matches(d)||!Regex("^\\d{2}:\\d{2}:\\d{2}$").matches(t))return null;val p=t.split(":").mapNotNull{it.toIntOrNull()};return if(p.size==3&&p[0] in 0..23&&p[1] in 0..59&&p[2] in 0..59)"${d}T${t}" else null}
    private fun label(t:String)=TextView(context).apply{text=t;setTextColor(0xFF62B0FF.toInt());textSize=11f;gravity=Gravity.CENTER_VERTICAL}
    private fun field(h:String)=EditText(context).apply{hint=h;setTextColor(0xFFE8F1FF.toInt());textSize=12f;setBackgroundColor(0xFF14253A.toInt())}
    private fun button(t:String)=Button(context).apply{text=t;setTextColor(0xFFFFFFFF.toInt());textSize=9f;stateListAnimator=null}
    private fun addRow(a:EditText,b:EditText){val r=LinearLayout(context).apply{orientation=HORIZONTAL};r.addView(a,LayoutParams(0,50,1f));r.addView(b,LayoutParams(0,50,1f));addView(r)}
}
