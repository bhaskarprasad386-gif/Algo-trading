package com.algotrading.app

import android.app.AlertDialog
import android.app.DatePickerDialog
import android.graphics.Color
import android.os.Bundle
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.Calendar
import java.util.Locale

class FullFnoBacktestActivity : AppCompatActivity() {
    private lateinit var tvStatus: TextView
    private lateinit var tvResults: TextView
    private lateinit var tvGapCalendar: TextView
    private lateinit var gapCalendarRows: LinearLayout
    private lateinit var etGapResultSearch: EditText
    private lateinit var btnGapResultSearch: Button
    private lateinit var replayStatus: TextView
    private lateinit var replayView: IntradayReplayView
    private lateinit var btnReplayPlay: Button
    private lateinit var btnReplayReset: Button
    private lateinit var btnStart: Button
    private lateinit var btnCancel: Button
    private lateinit var btnLoadMore: Button
    private lateinit var btnPurge: Button
    private lateinit var btnGapCalendar: Button
    private lateinit var strategyBuilder: CashFutureStrategyBuilderView
    private var jobId: String? = null
    private var nextSequence: Int? = null
    private var loading = false
    private var replayJob: Job? = null

    companion object { private const val MAX_RESULT_TEXT_CHARS = 20000 }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_full_fno_backtest)
        tvStatus = findViewById(R.id.tvFullFnoStatus)
        tvResults = findViewById(R.id.tvFullFnoResults)
        tvGapCalendar = findViewById(R.id.tvGapCalendar)
        gapCalendarRows = findViewById(R.id.llGapCalendarRows)
        etGapResultSearch = findViewById(R.id.etGapResultSearch)
        btnGapResultSearch = findViewById(R.id.btnGapResultSearch)
        replayStatus = findViewById(R.id.tvIntradayReplayStatus)
        replayView = findViewById(R.id.intradayReplayView)
        btnReplayPlay = findViewById(R.id.btnIntradayReplayPlay)
        replayView.setTimeframeChangedListener { timeframe ->\n            val symbol = replayStatus.text.toString().substringAfter(" • ").substringBefore(" • ").trim()\n            val date = replayStatus.text.toString().substringBefore(" • ").trim()\n            if (symbol.isNotBlank() && date.matches(Regex("\\d{4}-\\d{2}-\\d{2}"))) loadCashFutureReplay(date, symbol, timeframe)\n        }
        btnReplayReset = findViewById(R.id.btnIntradayReplayReset)
        btnStart = findViewById(R.id.btnFullFnoStart)
        btnCancel = findViewById(R.id.btnFullFnoCancel)
        btnLoadMore = findViewById(R.id.btnFullFnoLoadMore)
        btnPurge = findViewById(R.id.btnFullFnoPurge)
        btnGapCalendar = findViewById(R.id.btnGapCalendar)
        btnGapResultSearch.setOnClickListener { searchGapResults() }
        strategyBuilder = findViewById(R.id.cashFutureStrategyBuilder)
        btnLoadMore.isEnabled = false
        btnCancel.isEnabled = false
        btnPurge.isEnabled = false
        btnReplayPlay.isEnabled = false
        btnReplayReset.isEnabled = false
        btnStart.setOnClickListener { startBacktest() }
        btnCancel.setOnClickListener { cancelBacktest() }
        btnLoadMore.setOnClickListener { loadNextPage() }
        btnPurge.setOnClickListener { confirmPurge() }
        btnGapCalendar.setOnClickListener { openGapCalendar() }
        btnGapResultSearch.setOnClickListener { searchGapResults() }
        btnReplayPlay.setOnClickListener { toggleReplay() }
        btnReplayReset.setOnClickListener { replayJob?.cancel(); replayView.resetReplay(); btnReplayPlay.text = "PLAY 1 MIN"; updateReplayStatus() }
    }

    private fun startBacktest() = lifecycleScope.launch(Dispatchers.IO) {
        if (loading) return@launch
        loading = true
        withContext(Dispatchers.Main) {
            btnStart.isEnabled = false; btnCancel.isEnabled = true; btnLoadMore.isEnabled = false; btnPurge.isEnabled = false
            nextSequence = null; tvResults.text = ""; tvStatus.text = "Starting Full-F&O backtest…"
        }
        try { val accepted = ApiService.retrofitService.startFullFnoJob(); jobId = accepted.job; pollJob(accepted.job) }
        catch (e: Exception) { withContext(Dispatchers.Main) { tvStatus.text = "Start failed • ${e.message ?: "API error"}"; btnStart.isEnabled = true; btnCancel.isEnabled = false } }
        finally { loading = false }
    }

    private suspend fun pollJob(id: String) {
        while (true) {
            val job = ApiService.retrofitService.fullFnoJob(id)
            withContext(Dispatchers.Main) { tvStatus.text = "Full-F&O: ${job.job.status.uppercase()} • ${job.job.symbols_processed}/${job.job.symbols_total} • ${job.job.progress_pct}%" }
            if (job.job.status == "completed" || job.job.status == "failed" || job.job.status == "cancelled") {
                withContext(Dispatchers.Main) { btnCancel.isEnabled = false; btnStart.isEnabled = true; btnLoadMore.isEnabled = job.job.result_chunks > 0; btnPurge.isEnabled = true }
                if (job.job.result_chunks > 0) loadNextPageInternal(id)
                return
            }
            delay(1500)
        }
    }

    private fun cancelBacktest() = lifecycleScope.launch(Dispatchers.IO) {
        val id = jobId ?: return@launch
        if (!loading) return@launch
        withContext(Dispatchers.Main) { btnCancel.isEnabled = false; tvStatus.text = "Full-F&O: CANCELLING…" }
        try { val response = ApiService.retrofitService.cancelFullFnoJob(id); withContext(Dispatchers.Main) { tvStatus.text = "Full-F&O: ${response.job_status.uppercase()}" } }
        catch (e: Exception) { withContext(Dispatchers.Main) { btnCancel.isEnabled = true; tvStatus.text = "Cancel failed • ${e.message ?: "API error"}" } }
    }

    private fun loadNextPage() = lifecycleScope.launch(Dispatchers.IO) {
        if (loading || jobId == null) return@launch
        loading = true
        try { loadNextPageInternal(jobId!!) } finally { loading = false }
    }

    private suspend fun loadNextPageInternal(id: String) {
        val page = ApiService.retrofitService.fullFnoResults(id, limit = 50, afterSequence = nextSequence)
        if (page.data.isEmpty()) { withContext(Dispatchers.Main) { btnLoadMore.isEnabled = false; tvStatus.text = "Full-F&O: no more results" }; return }
        val text = buildString { page.data.forEach { chunk -> append("#${chunk.sequence}  ${chunk.symbol}\n"); append(chunk.result.toString()); append("\n────────────────────\n") } }
        withContext(Dispatchers.Main) {
            val combined = tvResults.text.toString() + text
            tvResults.text = if (combined.length <= MAX_RESULT_TEXT_CHARS) combined else "[Older results trimmed from device display to keep Android memory bounded]\n\n" + combined.takeLast(MAX_RESULT_TEXT_CHARS)
            nextSequence = page.next_after_sequence
            btnLoadMore.isEnabled = page.next_after_sequence != null && page.data.isNotEmpty()
            tvStatus.text = "Full-F&O: loaded ${page.data.size} results • total ${page.total} • next ${page.next_after_sequence ?: "END"}"
        }
    }

    private fun openGapCalendar() {
        val today = Calendar.getInstance()
        DatePickerDialog(this, { _, year, month, day -> loadGapForDate(year, month + 1, day) }, today.get(Calendar.YEAR), today.get(Calendar.MONTH), today.get(Calendar.DAY_OF_MONTH)).show()
    }

    private fun loadGapForDate(year: Int, month: Int, day: Int) = lifecycleScope.launch(Dispatchers.IO) {
        val tradingDate = "%04d-%02d-%02d".format(year, month, day)
        withContext(Dispatchers.Main) { tvGapCalendar.text = "$tradingDate • loading High−Open × historical lot ranking…"; gapCalendarRows.removeAllViews() }
        try {
            val response = ApiService.retrofitService.dateGapRanking(tradingDate, mode = "shorting", instrumentType = "STOCK", limit = 10)
            val prior = runCatching { ApiService.retrofitService.priorGapComparison(tradingDate, mode = "shorting", instrumentType = "STOCK", limit = 5) }.getOrNull()
            val top = response.top
            withContext(Dispatchers.Main) {
                tvGapCalendar.text = buildString {
                    append(if (top == null) "$tradingDate\nNo historical OHLC data found." else "$tradingDate • TOP GAP BY HIGH−OPEN × HISTORICAL LOT\n${top.symbol} • Gap ₹${"%.2f".format(top.gap)} • Gap×Lot ₹${"%.2f".format(top.weighted_gap)} • O ₹${"%.2f".format(top.open)} H ₹${"%.2f".format(top.high)} L ₹${"%.2f".format(top.low)} C ₹${"%.2f".format(top.close)} • Lot ${"%.0f".format(top.lot_size)}")
                    append("\n\nPRIOR HIGHER GAPS")
                    if (prior == null || !prior.has_larger_prior_gap) append("\nNone — this selected date is at/above all earlier available gaps.")
                    else prior.prior_larger.forEach { p -> append("\n${p.trading_date} • ${p.symbol} • Gap ₹${"%.2f".format(p.gap)} • Gap×Lot ₹${"%.2f".format(p.weighted_gap)} • O ${"%.2f".format(p.open)} H ${"%.2f".format(p.high)}") }
                }
                response.data.forEach { item -> addCalendarRow(tradingDate, item) }
            }
        } catch (e: Exception) { withContext(Dispatchers.Main) { tvGapCalendar.text = "$tradingDate • Gap calendar failed • ${e.message ?: "API error"}" } }
    }

    private fun addCalendarRow(tradingDate: String, item: DailyGapCalendarItem) {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL; setPadding(8, 6, 4, 6) }
        val details = TextView(this).apply {
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f); setTextColor(Color.WHITE); textSize = 11f
            text = "${item.symbol}  Gap ₹${"%.2f".format(item.gap)} × Lot ${"%.0f".format(item.lot_size)} = ₹${"%.2f".format(item.weighted_gap)}\nO ${"%.2f".format(item.open)}  H ${"%.2f".format(item.high)}  L ${"%.2f".format(item.low)}  C ${"%.2f".format(item.close)}"
        }
        val builder = Button(this).apply { text = "BUILD"; textSize = 10f; setOnClickListener {
            strategyBuilder.bindHistoricalSelection(item.symbol, item.open, item.lot_size)
            tvStatus.text = "Cash-Future strategy loaded • ${item.symbol} • ${tradingDate} • lot ${item.lot_size.toLong()} • qty ${item.lot_size.toLong()}"
        } }
        val graph = Button(this).apply { text = "GRAPH"; textSize = 10f; setOnClickListener { loadIntradayReplay(tradingDate, item.symbol) } }
        row.addView(details)
        row.addView(builder, LinearLayout.LayoutParams(86, 44).apply { setMargins(4, 0, 4, 0) })
        row.addView(graph, LinearLayout.LayoutParams(82, 44))
        gapCalendarRows.addView(row)
    }

    private fun loadIntradayReplay(tradingDate: String, symbol: String) = loadCashFutureReplay(tradingDate, symbol, "1m")

    private fun loadCashFutureReplay(tradingDate: String, symbol: String, timeframe: String) = lifecycleScope.launch(Dispatchers.IO) {
        replayJob?.cancel()
        withContext(Dispatchers.Main) { replayStatus.text = "$tradingDate • $symbol • loading paired $timeframe history…"; btnReplayPlay.isEnabled=false; btnReplayReset.isEnabled=false }
        try {
            val response=ApiService.retrofitService.cashFutureReplay(tradingDate,symbol,timeframe=timeframe,mode="CURRENT")
            withContext(Dispatchers.Main) {
                replayView.setCashFutureData(response.series,response.available_replay_intervals)
                replayStatus.text="$tradingDate • $symbol • ${response.count} paired rows • ${response.timeframe} • intervals ${response.available_replay_intervals.joinToString(" | ")}"
                btnReplayPlay.isEnabled=true; btnReplayReset.isEnabled=true; updateReplayStatus()
            }
        } catch(e:Exception) { withContext(Dispatchers.Main) { replayStatus.text="$tradingDate • $symbol • $timeframe replay failed • ${e.message ?: "API error"}" } }
    }
    private fun toggleReplay() {
        if (replayView.isComplete()) { replayView.resetReplay(); btnReplayPlay.text = "PLAY ${replayView.replayIntervalSeconds()} SEC"; updateReplayStatus(); return }
        if (replayJob?.isActive == true) { replayJob?.cancel(); btnReplayPlay.text = "PLAY ${replayView.replayIntervalSeconds()} SEC"; return }
        btnReplayPlay.text = "PAUSE"
        replayJob = lifecycleScope.launch {
            while (true) {
                val hasMore = withContext(Dispatchers.Main) { replayView.stepOneMinute() }
                withContext(Dispatchers.Main) { updateReplayStatus() }
                if (!hasMore) break
                delay(500)
            }
            btnReplayPlay.text = "REPLAY"
        }
    }

    private fun updateReplayStatus() { replayStatus.text = replayStatus.text.toString().substringBefore(" • replay") + " • replay ${replayView.currentTime()}" }

    private fun searchGapResults() = lifecycleScope.launch(Dispatchers.IO) {
        val query = etGapResultSearch.text.toString().trim(); val normalized = query.lowercase(Locale.ROOT); val today = Calendar.getInstance()
        val exactDate = Regex("^(\\d{4})-(\\d{2})-(\\d{2})$").matchEntire(query); val monthQuery = Regex("^(\\d{4})-(\\d{2})$").matchEntire(query)
        val monthMode = query.isBlank() || normalized.contains("month") || normalized.contains("महीना") || normalized.contains("इस महीने") || monthQuery != null
        withContext(Dispatchers.Main) { btnGapResultSearch.isEnabled = false; tvResults.text = "Searching high-based gap…" }
        try {
            if (exactDate != null) loadGapSearchDate(exactDate.groupValues[1].toInt(), exactDate.groupValues[2].toInt(), exactDate.groupValues[3].toInt())
            else if (monthMode) { val y = monthQuery?.groupValues?.get(1)?.toInt() ?: today.get(Calendar.YEAR); val m = monthQuery?.groupValues?.get(2)?.toInt()?.minus(1) ?: today.get(Calendar.MONTH); loadGapSearchMonth(y, m) }
            else withContext(Dispatchers.Main) { tvResults.text = "Use: this month, YYYY-MM, or YYYY-MM-DD" }
        } finally { withContext(Dispatchers.Main) { btnGapResultSearch.isEnabled = true } }
    }

    private suspend fun loadGapSearchDate(year: Int, month: Int, day: Int) {
        val tradingDate = "%04d-%02d-%02d".format(year, month, day)
        val response = ApiService.retrofitService.dateGapRanking(tradingDate, mode = "shorting", instrumentType = "STOCK", limit = 1)
        val top = response.top
        val prior = runCatching { ApiService.retrofitService.priorGapComparison(tradingDate, mode = "shorting", instrumentType = "STOCK", limit = 5) }.getOrNull()
        withContext(Dispatchers.Main) {
            tvResults.text = buildString {
                append(if (top == null) "$tradingDate\nNo historical OHLC data found." else formatGapResult("$tradingDate • TOP HIGH-BASED GAP", top.symbol, top.direction, top.gap, top.gap_percent, top.weighted_gap, top.previous_close, top.open, top.high, top.low, top.close, top.lot_size))
                append("\n\nPRIOR HIGHER GAPS")
                if (prior == null || !prior.has_larger_prior_gap) append("\nNone found.") else prior.prior_larger.forEach { p -> append("\n${p.trading_date} • ${p.symbol} • Gap ₹${"%.2f".format(p.gap)} • Gap×Lot ₹${"%.2f".format(p.weighted_gap)}") }
            }
        }
    }

    private suspend fun loadGapSearchMonth(year: Int, month: Int) {
        val result = ApiService.retrofitService.monthlyGapSearch(year, month + 1, mode = "shorting", instrumentType = "STOCK"); val top = result.result
        withContext(Dispatchers.Main) { tvResults.text = if (top == null) "%04d-%02d\nNo historical high-based gap data found.".format(year, month + 1) else formatGapResult("%04d-%02d • MONTH TOP HIGH-BASED GAP".format(year, month + 1), top.symbol, "UP", top.gap, if (top.open != 0.0) top.gap / top.open * 100.0 else 0.0, top.gap_value, top.previous_close ?: 0.0, top.open, top.high, top.low, top.close, top.lot_size) }
    }

    private fun formatGapResult(title: String, symbol: String, direction: String, gap: Double, gapPercent: Double, weightedGap: Double, previousClose: Double, open: Double, high: Double, low: Double, close: Double, lotSize: Double): String = buildString {
        append("$title\n$symbol • $direction\nGap: ₹${"%.2f".format(gap)} (${"%.2f".format(gapPercent)}%)\nGap × Lot: ₹${"%.2f".format(weightedGap)}\nPrev Close: ₹${"%.2f".format(previousClose)}\nOpen: ₹${"%.2f".format(open)}\nHigh: ₹${"%.2f".format(high)}\nLow: ₹${"%.2f".format(low)}\nClose: ₹${"%.2f".format(close)}\nLot Size: ${"%.0f".format(lotSize)}")
    }

    private fun confirmPurge() {
        val id = jobId ?: return
        AlertDialog.Builder(this).setTitle("Purge Full-F&O Results?").setMessage("This permanently deletes the durable result chunks for job $id. The compact job summary is retained.").setNegativeButton("CANCEL", null).setPositiveButton("PURGE") { _, _ -> purgeResults(id) }.show()
    }

    private fun purgeResults(id: String) = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { btnPurge.isEnabled = false; tvStatus.text = "Full-F&O: PURGING RESULTS…" }
        try { val response = ApiService.retrofitService.purgeFullFnoResults(id); withContext(Dispatchers.Main) { tvResults.text = ""; nextSequence = null; btnLoadMore.isEnabled = false; tvStatus.text = "Full-F&O: purged ${response.deleted_chunks} result chunks • summary retained" } }
        catch (e: Exception) { withContext(Dispatchers.Main) { btnPurge.isEnabled = true; tvStatus.text = "Purge failed • ${e.message ?: "API error"}" } }
    }
}
