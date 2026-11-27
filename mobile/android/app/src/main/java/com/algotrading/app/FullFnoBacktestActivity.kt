package com.algotrading.app

import android.app.AlertDialog
import android.os.Bundle
import android.widget.Button
import android.widget.ProgressBar
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
    private lateinit var progress: ProgressBar
    private lateinit var btnStart: Button
    private lateinit var btnCancel: Button
    private lateinit var btnLoadMore: Button
    private lateinit var btnPurge: Button

    private var jobId: String? = null
    private var nextSequence: Int? = null
    private var loading = false

    companion object {
        private const val MAX_RESULT_TEXT_CHARS = 20000
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_full_fno_backtest)
        tvStatus = findViewById(R.id.tvFullFnoStatus)
        tvResults = findViewById(R.id.tvFullFnoResults)
        progress = findViewById(R.id.progressFullFno)
        btnStart = findViewById(R.id.btnFullFnoStart)
        btnCancel = findViewById(R.id.btnFullFnoCancel)
        btnLoadMore = findViewById(R.id.btnFullFnoLoadMore)
        btnPurge = findViewById(R.id.btnFullFnoPurge)

        btnCancel.isEnabled = false
        btnLoadMore.isEnabled = false
        btnPurge.isEnabled = false
        btnStart.setOnClickListener { startBacktest() }
        btnCancel.setOnClickListener { cancelBacktest() }
        btnLoadMore.setOnClickListener { loadNextPage() }
        btnPurge.setOnClickListener { confirmPurge() }
    }

    private fun startBacktest() {
        if (loading) return
        loading = true
        lifecycleScope.launch(Dispatchers.IO) {
            withContext(Dispatchers.Main) {
                btnStart.isEnabled = false
                btnCancel.isEnabled = true
                btnLoadMore.isEnabled = false
                btnPurge.isEnabled = false
                progress.visibility = ProgressBar.VISIBLE
                nextSequence = null
                tvResults.text = ""
                tvStatus.text = "Starting Full F&O backtest…"
            }
            try {
                val accepted = ApiService.retrofitService.startFullFnoJob()
                jobId = accepted.job
                pollJob(accepted.job)
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    tvStatus.text = "Start failed • ${e.message ?: "API error"}"
                    btnStart.isEnabled = true
                    btnCancel.isEnabled = false
                }
            } finally {
                loading = false
                withContext(Dispatchers.Main) { progress.visibility = ProgressBar.GONE }
            }
        }
    }

    private suspend fun pollJob(id: String) {
        try {
            while (true) {
                val response = ApiService.retrofitService.fullFnoJob(id)
                withContext(Dispatchers.Main) {
                    val job = response.job
                    tvStatus.text = "Full F&O: ${job.status.uppercase()} • ${job.symbols_processed}/${job.symbols_total} • ${job.progress_pct}%"
                    if (job.status == "completed" || job.status == "failed" || job.status == "cancelled") {
                        btnCancel.isEnabled = false
                        btnStart.isEnabled = true
                        btnLoadMore.isEnabled = job.result_chunks > 0
                        btnPurge.isEnabled = true
                    }
                }
                if (response.job.status == "completed" || response.job.status == "failed" || response.job.status == "cancelled") {
                    if (response.job.result_chunks > 0) loadNextPageInternal(id)
                    return
                }
                delay(1500)
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                btnStart.isEnabled = true
                btnCancel.isEnabled = false
                tvStatus.text = "Status polling failed • ${e.message ?: "API error"}"
            }
        }
    }

    private fun cancelBacktest() = lifecycleScope.launch(Dispatchers.IO) {
        val id = jobId ?: return@launch
        if (!loading) return@launch
        withContext(Dispatchers.Main) {
            btnCancel.isEnabled = false
            tvStatus.text = "Full F&O: CANCELLING…"
        }
        try {
            val response = ApiService.retrofitService.cancelFullFnoJob(id)
            withContext(Dispatchers.Main) {
                tvStatus.text = "Full F&O: ${response.job_status.uppercase()}"
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                btnCancel.isEnabled = true
                tvStatus.text = "Cancel failed • ${e.message ?: "API error"}"
            }
        }
    }

    private fun loadNextPage() = lifecycleScope.launch(Dispatchers.IO) {
        if (loading || jobId == null) return@launch
        loading = true
        try {
            loadNextPageInternal(jobId!!)
        } finally {
            loading = false
        }
    }

    private suspend fun loadNextPageInternal(id: String) {
        val page = ApiService.retrofitService.fullFnoResults(id, limit = 50, afterSequence = nextSequence)
        if (page.data.isEmpty()) {
            withContext(Dispatchers.Main) {
                btnLoadMore.isEnabled = false
                tvStatus.text = "Full F&O: no more results"
            }
            return
        }
        val pageText = buildString {
            page.data.forEach { chunk ->
                append("#${chunk.sequence}  ${chunk.symbol}\n")
                append(chunk.result.toString())
                append("\n────────────────────\n")
            }
        }
        withContext(Dispatchers.Main) {
            val combined = tvResults.text.toString() + pageText
            tvResults.text = if (combined.length <= MAX_RESULT_TEXT_CHARS) combined
                else "[Older results trimmed from device display to keep Android memory bounded]\n\n" + combined.takeLast(MAX_RESULT_TEXT_CHARS)
            nextSequence = page.next_after_sequence
            btnLoadMore.isEnabled = page.next_after_sequence != null
            btnPurge.isEnabled = true
            tvStatus.text = "Full F&O: loaded ${page.data.size} results • total ${page.total} • next ${page.next_after_sequence ?: "END"}"
        }
    }

    private fun confirmPurge() {
        val id = jobId ?: return
        AlertDialog.Builder(this)
            .setTitle("Purge Full F&O Results?")
            .setMessage("This permanently deletes the durable result chunks for job $id. The compact job summary is retained.")
            .setNegativeButton("CANCEL", null)
            .setPositiveButton("PURGE") { _, _ -> purgeResults(id) }
            .show()
    }

    private fun purgeResults(id: String) = lifecycleScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) {
            btnPurge.isEnabled = false
            tvStatus.text = "Full F&O: PURGING RESULTS…"
        }
        try {
            val response = ApiService.retrofitService.purgeFullFnoResults(id)
            withContext(Dispatchers.Main) {
                tvResults.text = ""
                nextSequence = null
                btnLoadMore.isEnabled = false
                tvStatus.text = "Full F&O: purged ${response.deleted_chunks} result chunks • summary retained"
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                btnPurge.isEnabled = true
                tvStatus.text = "Purge failed • ${e.message ?: "API error"}"
            }
        }
    }
}
