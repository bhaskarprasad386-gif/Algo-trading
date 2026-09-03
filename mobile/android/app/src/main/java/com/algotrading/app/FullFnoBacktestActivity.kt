package com.algotrading.app

import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class FullFnoBacktestActivity : AppCompatActivity() {
    private lateinit var tvStatus: TextView
    private lateinit var tvResults: TextView
    private lateinit var btnStart: Button
    private lateinit var btnLoadMore: Button
    private var jobId: String? = null
    private var nextSequence: Int? = null
    private var loading = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_full_fno_backtest)
        tvStatus = findViewById(R.id.tvFullFnoStatus)
        tvResults = findViewById(R.id.tvFullFnoResults)
        btnStart = findViewById(R.id.btnFullFnoStart)
        btnLoadMore = findViewById(R.id.btnFullFnoLoadMore)
        btnLoadMore.isEnabled = false
        btnStart.setOnClickListener { startBacktest() }
        btnLoadMore.setOnClickListener { loadNextPage() }
    }

    private fun startBacktest() = lifecycleScope.launch(Dispatchers.IO) {
        if (loading) return@launch
        loading = true
        withContext(Dispatchers.Main) {
            btnStart.isEnabled = false
            btnLoadMore.isEnabled = false
            nextSequence = null
            tvResults.text = ""
            tvStatus.text = "Starting Full-F&O backtest…"
        }
        try {
            val accepted = ApiService.retrofitService.startFullFnoJob()
            jobId = accepted.job
            pollJob(accepted.job)
        } catch (e: Exception) {
            withContext(Dispatchers.Main) { tvStatus.text = "Start failed • ${e.message ?: "API error"}"; btnStart.isEnabled = true }
        } finally { loading = false }
    }

    private suspend fun pollJob(id: String) {
        while (true) {
            val job = ApiService.retrofitService.fullFnoJob(id)
            withContext(Dispatchers.Main) {
                tvStatus.text = "Full-F&O: ${job.job.status.uppercase()} • ${job.job.symbols_processed}/${job.job.symbols_total} • ${job.job.progress_pct}%"
            }
            if (job.job.status == "completed" || job.job.status == "failed" || job.job.status == "cancelled") {
                withContext(Dispatchers.Main) { btnLoadMore.isEnabled = job.job.status != "failed" }
                if (job.job.status == "completed") loadNextPageInternal(id)
                return
            }
            delay(1500)
        }
    }

    private fun loadNextPage() = lifecycleScope.launch(Dispatchers.IO) {
        if (loading || jobId == null) return@launch
        loading = true
        try { loadNextPageInternal(jobId!!) } finally { loading = false }
    }

    private suspend fun loadNextPageInternal(id: String) {
        val page = ApiService.retrofitService.fullFnoResults(id, limit = 50, afterSequence = nextSequence)
        if (page.data.isEmpty()) {
            withContext(Dispatchers.Main) { btnLoadMore.isEnabled = false; tvStatus.text = "Full-F&O: no more results" }
            return
        }
        val text = buildString {
            page.data.forEach { chunk ->
                append("#${chunk.sequence}  ${chunk.symbol}\n")
                append(chunk.result.toString())
                append("\n────────────────────\n")
            }
        }
        withContext(Dispatchers.Main) {
            tvResults.append(text)
            nextSequence = page.next_after_sequence
            btnLoadMore.isEnabled = page.next_after_sequence != null && page.data.size >= page.limit
            tvStatus.text = "Full-F&O: loaded ${page.data.size} results • total ${page.total} • next ${page.next_after_sequence ?: "END"}"
        }
    }
}
