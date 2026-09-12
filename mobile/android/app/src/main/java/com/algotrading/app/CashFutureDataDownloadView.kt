package com.algotrading.app

import android.content.Context
import android.graphics.Color
import android.util.AttributeSet
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale

class CashFutureDataDownloadView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : LinearLayout(context, attrs) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var pollJob: Job? = null
    private val symbol = EditText(context); private val start = EditText(context); private val end = EditText(context); private val timeframe = EditText(context)
    private val status = TextView(context); private val coverage = TextView(context); private val download = Button(context); private val resume = Button(context); private val openCalendar = Button(context)

    init {
        orientation = VERTICAL; setPadding(11,11,11,11); setBackgroundColor(Color.rgb(13,27,46))
        addView(TextView(context).apply { text="06  •  HISTORICAL DATA DOWNLOAD"; setTextColor(Color.rgb(92,225,230)); textSize=11f; setTypeface(typeface,android.graphics.Typeface.BOLD) })
        symbol.hint="Stock symbol (e.g. RELIANCE)"; start.hint="Start YYYY-MM-DD"; end.hint="End YYYY-MM-DD"; timeframe.hint="Timeframe: 1m"
        val fmt=SimpleDateFormat("yyyy-MM-dd",Locale.US); val today=Calendar.getInstance(); end.setText(fmt.format(today.time)); today.add(Calendar.DAY_OF_YEAR,-365); start.setText(fmt.format(today.time)); timeframe.setText("1m")
        listOf(symbol,start,end,timeframe).forEach { it.setTextColor(Color.WHITE); it.setHintTextColor(Color.rgb(120,144,173)); it.textSize=12f; addView(it,LayoutParams(LayoutParams.MATCH_PARENT,45).apply{topMargin=4}) }
        val buttons=LinearLayout(context).apply{orientation=HORIZONTAL;gravity=Gravity.CENTER_VERTICAL}; download.text="DOWNLOAD / REPAIR";resume.text="RESUME";listOf(download,resume).forEach{it.setTextColor(Color.WHITE)};download.setBackgroundColor(Color.rgb(36,107,255));resume.setBackgroundColor(Color.rgb(70,90,115));buttons.addView(download,LayoutParams(0,46,1f).apply{rightMargin=4});buttons.addView(resume,LayoutParams(0,46,1f).apply{leftMargin=4});addView(buttons,LayoutParams(LayoutParams.MATCH_PARENT,LayoutParams.WRAP_CONTENT).apply{topMargin=6})
        openCalendar.text="REFRESH CALENDAR FROM DOWNLOADED DATA";openCalendar.setTextColor(Color.WHITE);openCalendar.setBackgroundColor(Color.rgb(22,184,134));addView(openCalendar,LayoutParams(LayoutParams.MATCH_PARENT,46).apply{topMargin=6})
        status.text="Durable SQLite • real Angel One data • no synthetic records";status.setTextColor(Color.rgb(221,235,255));status.textSize=10f;status.setPadding(8,8,8,4);addView(status)
        coverage.text="Calendar source: downloaded paired Cash + Future coverage";coverage.setTextColor(Color.rgb(140,190,225));coverage.textSize=10f;coverage.setPadding(8,2,8,8);addView(coverage)
        download.setOnClickListener{startDownload()};resume.setOnClickListener{resumeDownload()};openCalendar.setOnClickListener{openCalendarScreen()}
    }
    private fun startDownload(){val stock=symbol.text.toString().trim().uppercase(Locale.US);val from=start.text.toString().trim();val to=end.text.toString().trim();val tf=timeframe.text.toString().trim().ifBlank{"1m"};if(stock.isBlank()||from.isBlank()||to.isBlank()){status.text="Enter symbol, start date and end date";return};pollJob?.cancel();setBusy(true);scope.launch{try{val accepted=withContext(Dispatchers.IO){ApiService.retrofitService.startCashFutureDownload(CashFutureDownloadRequest(spot_instrument=stock,underlying=stock,start="${from}T09:15:00+05:30",end="${to}T15:30:00+05:30",timeframe=tf,mode="BOTH"))};status.tag=accepted.job_id;status.text="Download queued • job ${accepted.job_id.take(8)}…";pollStatus(accepted.job_id)}catch(e:Exception){status.text="Download failed • ${e.message?:"API error"}";setBusy(false)}}}
    private fun resumeDownload(){val id=status.tag as? String;if(id.isNullOrBlank()){status.text="No paused job in this screen. Start a download first.";return};pollJob?.cancel();setBusy(true);scope.launch{try{withContext(Dispatchers.IO){ApiService.retrofitService.resumeCashFutureDownload(id)};status.text="Resuming • job ${id.take(8)}…";pollStatus(id)}catch(e:Exception){status.text="Resume failed • ${e.message?:"API error"}";setBusy(false)}}}
    private suspend fun pollStatus(id:String){status.tag=id;pollJob=scope.launch{while(true){try{val job=withContext(Dispatchers.IO){ApiService.retrofitService.cashFutureDownloadStatus(id).job};val total=job.requested_chunks.coerceAtLeast(1);val done=job.completed_chunks+job.skipped_chunks;val pct=(done*100.0/total).coerceIn(0.0,100.0);status.text=buildString{append("${job.status} • ${"%.1f".format(Locale.US,pct)}% • chunks $done/$total\n");append("Fetched ${job.fetched_records} • Inserted ${job.inserted_records} • Catalog ${job.catalog_count}");if(!job.error.isNullOrBlank())append("\nError: ${job.error}")};coverage.text=if(job.status=="COMPLETE")"Calendar source READY • ${job.inserted_records} records inserted • ${job.catalog_count} coverage entries" else "Calendar source: download ${job.status.lowercase(Locale.US)} • durable data retained";if(job.status=="COMPLETE"||job.status=="FAILED"){setBusy(false);return@launch}}catch(e:Exception){status.text="Status check failed • ${e.message?:"API error"}";setBusy(false);return@launch};delay(1500)}};pollJob?.join()}
    private fun openCalendarScreen(){context.startActivity(android.content.Intent(context,FullFnoBacktestActivity::class.java).apply{putExtra("OPEN_CASH_FUTURE_CALENDAR",true)})}
    private fun setBusy(busy:Boolean){download.isEnabled=!busy;resume.isEnabled=!busy}
    override fun onDetachedFromWindow(){pollJob?.cancel();scope.cancel();super.onDetachedFromWindow()}
}
