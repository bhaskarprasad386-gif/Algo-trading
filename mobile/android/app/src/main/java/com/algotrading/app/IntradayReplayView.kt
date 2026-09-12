package com.algotrading.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import android.widget.Button
import kotlin.math.max
import kotlin.math.min

/** Synchronized Cash/Future/Gap historical replay. */
class IntradayReplayView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : View(context, attrs) {
    private val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF9CB4CF.toInt(); textSize = 24f; typeface = Typeface.MONOSPACE }
    private val cashPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF38D39F.toInt(); strokeWidth = 4f; style = Paint.Style.STROKE }
    private val futurePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF62B0FF.toInt(); strokeWidth = 4f; style = Paint.Style.STROKE }
    private val gapPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFFC857.toInt(); strokeWidth = 3f; style = Paint.Style.STROKE }
    private val highlightPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFF5C5C.toInt(); strokeWidth = 3f; style = Paint.Style.FILL; typeface = Typeface.DEFAULT_BOLD }
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF243A55.toInt(); strokeWidth = 1f }
    private var points: List<CashFutureReplayPoint> = emptyList()
    private var visiblePoints = 0
    private var replayTime = "--:--:--"
    private var replayStepSeconds = 60L
    private var availableIntervals: Set<String> = emptySet()
    private var timeframeChangedListener: ((String) -> Unit)? = null
    private var focusTimestamp: String? = null

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        bindModeButton(R.id.btnReplay1s, 1L)
        bindModeButton(R.id.btnReplay1m, 60L)
        bindModeButton(R.id.btnReplay5m, 300L)
        bindModeButton(R.id.btnReplay15m, 900L)
        bindModeButton(R.id.btnReplay30m, 1800L)
        refreshModeButtons()
    }

    private fun bindModeButton(id: Int, seconds: Long) {
        rootView.findViewById<Button>(id)?.setOnClickListener {
            if (availableIntervals.isEmpty() || labelFor(seconds) in availableIntervals) setReplayMode(seconds)
        }
    }

    private fun labelFor(seconds: Long): String = when (seconds) {
        1L -> "1 SEC"
        60L -> "1 MIN"
        300L -> "5 MIN"
        900L -> "15 MIN"
        1800L -> "30 MIN"
        else -> "${seconds}s"
    }

    private fun refreshModeButtons() {
        listOf(
            R.id.btnReplay1s to "1 SEC",
            R.id.btnReplay1m to "1 MIN",
            R.id.btnReplay5m to "5 MIN",
            R.id.btnReplay15m to "15 MIN",
            R.id.btnReplay30m to "30 MIN"
        ).forEach { (id, label) ->
            rootView.findViewById<Button>(id)?.apply {
                alpha = if (labelFor(replayStepSeconds) == label) 1f else 0.62f
                isEnabled = availableIntervals.isEmpty() || label in availableIntervals
            }
        }
    }

    fun setTimeframeChangedListener(listener: (String) -> Unit) { timeframeChangedListener = listener }

    fun setFocusTimestamp(timestamp: String?) {
        focusTimestamp = timestamp?.takeIf { it.isNotBlank() }
        invalidate()
    }

    fun focusedTimestamp(): String? = focusTimestamp

    fun setCashFutureData(newPoints: List<CashFutureReplayPoint>, intervals: List<String>) {
        points = newPoints.sortedBy { it.timestamp }
        availableIntervals = intervals.toSet()
        resetReplay()
        refreshModeButtons()
    }

    fun setData(newPoints: List<IntradayReplayPoint>) {
        points = newPoints.map {
            CashFutureReplayPoint(timestamp = it.timestamp, cash_price = it.close, future_price = it.close, gap = 0.0)
        }.sortedBy { it.timestamp }
        availableIntervals = setOf("1 MIN", "5 MIN", "15 MIN", "30 MIN")
        resetReplay()
        refreshModeButtons()
    }

    fun setReplayMode(seconds: Long) {
        val v = listOf(1L, 60L, 300L, 900L, 1800L).minByOrNull { kotlin.math.abs(it - seconds) } ?: 60L
        if (availableIntervals.isNotEmpty() && labelFor(v) !in availableIntervals) return
        replayStepSeconds = v
        resetReplay()
        refreshModeButtons()
        timeframeChangedListener?.invoke(
            when (v) {
                1L -> "1s"
                60L -> "1m"
                300L -> "5m"
                900L -> "15m"
                1800L -> "30m"
                else -> "1m"
            }
        )
    }

    fun setReplayMode(seconds: Int) = setReplayMode(seconds.toLong())
    fun replayIntervalSeconds() = replayStepSeconds
    fun replayIntervalMinutes() = (replayStepSeconds / 60L).toInt()

    fun resetReplay() {
        visiblePoints = if (points.isEmpty()) 0 else 1
        replayTime = points.getOrNull(visiblePoints - 1)?.let(::timeOf) ?: "--:--:--"
        rootView.findViewById<Button>(R.id.btnIntradayReplayPlay)?.text = "PLAY ${labelFor(replayStepSeconds)}"
        invalidate()
    }

    fun stepReplay(): Boolean {
        if (visiblePoints >= points.size) return false
        val current = parseEpochSeconds(points[visiblePoints - 1].timestamp)
        val target = current + replayStepSeconds
        var next = visiblePoints
        while (next < points.size && parseEpochSeconds(points[next].timestamp) < target) next++
        visiblePoints = if (next < points.size) next + 1 else points.size
        replayTime = points.getOrNull(visiblePoints - 1)?.let(::timeOf) ?: "--:--:--"
        invalidate()
        return visiblePoints < points.size
    }

    fun stepOneMinute() = stepReplay()
    fun currentTime() = replayTime
    fun isComplete() = points.isNotEmpty() && visiblePoints >= points.size

    private fun timeOf(p: CashFutureReplayPoint): String {
        val s = p.timestamp
        if (s.length < 16) return "--:--:--"
        return if (s.length >= 19) s.substring(11, 19) else s.substring(11, 16) + ":00"
    }

    private fun parseEpochSeconds(s: String): Long {
        if (s.length < 16) return 0L
        val h = s.substring(11, 13).toLongOrNull() ?: 0L
        val m = s.substring(14, 16).toLongOrNull() ?: 0L
        val sec = if (s.length >= 19) s.substring(17, 19).toLongOrNull() ?: 0L else 0L
        return h * 3600L + m * 60L + sec
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val visible = points.take(visiblePoints)
        if (visible.isEmpty()) {
            canvas.drawText("Select GRAPH from a calendar row", 18f, 42f, axisPaint)
            return
        }
        drawPaired(canvas, visible)
    }

    private fun drawPaired(canvas: Canvas, v: List<CashFutureReplayPoint>) {
        val left = 72f
        val right = width - 18f
        val top = 38f
        val bottom = height - 48f
        val gapTop = bottom - 72f
        val priceBottom = gapTop - 18f
        val pMin = v.minOf { min(it.cash_price, it.future_price) }
        val pMax = v.maxOf { max(it.cash_price, it.future_price) }
        val pRange = max(0.000001, pMax - pMin)
        val gMin = v.minOf { it.gap }
        val gMax = v.maxOf { it.gap }
        val gRange = max(0.000001, gMax - gMin)
        fun x(i: Int) = if (v.size == 1) left else left + (right - left) * i / (v.size - 1)
        fun py(n: Double) = (priceBottom - (n - pMin) / pRange * (priceBottom - top)).toFloat()
        fun gy(n: Double) = (bottom - 4f - (n - gMin) / gRange * (bottom - gapTop)).toFloat()
        canvas.drawLine(left, top, right, top, gridPaint)
        canvas.drawLine(left, priceBottom, right, priceBottom, gridPaint)
        canvas.drawLine(left, gapTop, right, gapTop, gridPaint)
        canvas.drawText("CASH", left, 24f, axisPaint)
        canvas.drawText("FUTURE", left + 100f, 24f, axisPaint)
        canvas.drawText("GAP", left + 220f, 24f, axisPaint)
        canvas.drawText(String.format("%.2f", pMax), 4f, top + 10f, axisPaint)
        canvas.drawText(String.format("%.2f", pMin), 4f, priceBottom, axisPaint)
        canvas.drawText("Gap ₹" + String.format("%.2f", gMax) + " → ₹" + String.format("%.2f", gMin), left, bottom + 20f, axisPaint)
        canvas.drawText(labelFor(replayStepSeconds) + " • " + replayTime, left, height - 8f, axisPaint)
        val cp = Path()
        val fp = Path()
        val gp = Path()
        v.forEachIndexed { i, p ->
            val px = x(i)
            if (i == 0) {
                cp.moveTo(px, py(p.cash_price))
                fp.moveTo(px, py(p.future_price))
                gp.moveTo(px, gy(p.gap))
            } else {
                cp.lineTo(px, py(p.cash_price))
                fp.lineTo(px, py(p.future_price))
                gp.lineTo(px, gy(p.gap))
            }
        }
        canvas.drawPath(cp, cashPaint)
        canvas.drawPath(fp, futurePaint)
        canvas.drawPath(gp, gapPaint)

        val fallbackIndex = points.indices.maxByOrNull { index -> points[index].gap }
        val focusedIndex = focusTimestamp?.let { timestamp -> points.indexOfFirst { it.timestamp == timestamp }.takeIf { it >= 0 } }
        val gapHighIndex = focusedIndex ?: fallbackIndex
        if (gapHighIndex != null && gapHighIndex < visiblePoints) {
            val highPoint = points[gapHighIndex]
            val visibleIndex = v.indexOfFirst { it.timestamp == highPoint.timestamp }
            if (visibleIndex >= 0) {
                val px = x(visibleIndex)
                val gyHigh = gy(highPoint.gap)
                canvas.drawCircle(px, gyHigh, 8f, highlightPaint)
                val labelX = min(px + 10f, right - 210f)
                canvas.drawText("GAP HIGH ₹${String.format("%.2f", highPoint.gap)}", labelX, gyHigh - 30f, highlightPaint)
                canvas.drawText("${timeOf(highPoint)}  CASH ₹${String.format("%.2f", highPoint.cash_price)}", labelX, gyHigh - 8f, highlightPaint)
                canvas.drawText("FUTURE ₹${String.format("%.2f", highPoint.future_price)}", labelX, gyHigh + 14f, highlightPaint)
                canvas.drawText("MARGIN ₹${String.format("%.2f", highPoint.margin_required)}", labelX, gyHigh + 36f, highlightPaint)
            }
        }
    }
}
