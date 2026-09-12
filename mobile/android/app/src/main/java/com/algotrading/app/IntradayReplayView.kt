package com.algotrading.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import android.widget.Button
import kotlin.math.max
import kotlin.math.min

data class ReplayCandle(val bucket: Long, val open: Double, val high: Double, val low: Double, val close: Double)

class IntradayReplayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    private val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF9CB4CF.toInt(); textSize = 24f; typeface = Typeface.MONOSPACE }
    private val gridPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF203248.toInt(); strokeWidth = 1f }
    private val cashPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF55E6B0.toInt(); strokeWidth = 4f }
    private val futurePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF62B0FF.toInt(); strokeWidth = 4f }
    private val gapPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFFC857.toInt(); strokeWidth = 4f }
    private val markerPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFFFFFFFF.toInt(); strokeWidth = 2f }
    private var points: List<IntradayReplayPoint> = emptyList()
    private var visiblePoints = 0
    private var replayTime = "--:--:--"
    private var chartIntervalSeconds = 15 * 60L
    private var replayStepSeconds = 60L

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        bindModeButton(R.id.btnReplay1s, 1)
        bindModeButton(R.id.btnReplay1m, 60)
        bindModeButton(R.id.btnReplay5m, 5 * 60)
        bindModeButton(R.id.btnReplay15m, 15 * 60)
        bindModeButton(R.id.btnReplay30m, 30 * 60)
        refreshModeButtons()
    }

    private fun bindModeButton(id: Int, seconds: Int) {
        rootView.findViewById<Button>(id)?.setOnClickListener {
            setReplayMode(seconds)
            rootView.findViewById<Button>(R.id.btnIntradayReplayPlay)?.text = "PLAY ${labelFor(seconds)}"
        }
    }

    private fun labelFor(seconds: Int): String = when (seconds) {
        1 -> "1 SEC"
        60 -> "1 MIN"
        300 -> "5 MIN"
        900 -> "15 MIN"
        1800 -> "30 MIN"
        else -> "${seconds}s"
    }

    private fun refreshModeButtons() {
        val ids = listOf(
            R.id.btnReplay1s to 1L,
            R.id.btnReplay1m to 60L,
            R.id.btnReplay5m to 300L,
            R.id.btnReplay15m to 900L,
            R.id.btnReplay30m to 1800L,
        )
        ids.forEach { (id, seconds) ->
            rootView.findViewById<Button>(id)?.alpha = if (seconds == replayStepSeconds) 1f else 0.62f
        }
    }

    fun setData(newPoints: List<IntradayReplayPoint>) {
        points = newPoints.sortedBy { it.timestamp }
        resetReplay()
    }

    fun setReplayMode(seconds: Int) {
        val supported = listOf(1L, 60L, 300L, 900L, 1800L)
        replayStepSeconds = supported.minByOrNull { kotlin.math.abs(it - seconds.toLong()) } ?: 900L
        chartIntervalSeconds = replayStepSeconds
        resetReplay()
        refreshModeButtons()
    }

    fun replayIntervalSeconds(): Long = replayStepSeconds
    fun replayIntervalMinutes(): Int = (replayStepSeconds / 60L).toInt()

    fun resetReplay() {
        visiblePoints = if (points.isEmpty()) 0 else 1
        replayTime = timeOf(points.getOrNull(visiblePoints - 1)?.timestamp)
        rootView.findViewById<Button>(R.id.btnIntradayReplayPlay)?.text = "PLAY ${labelFor(replayStepSeconds.toInt())}"
        invalidate()
    }

    fun stepReplay(): Boolean {
        if (visiblePoints >= points.size) return false
        val currentEpoch = parseEpochSeconds(points.getOrNull(visiblePoints - 1)?.timestamp)
        val target = currentEpoch + replayStepSeconds
        var next = visiblePoints
        while (next < points.size && parseEpochSeconds(points[next].timestamp) < target) next += 1
        visiblePoints = if (next < points.size) next + 1 else points.size
        replayTime = timeOf(points.getOrNull(visiblePoints - 1)?.timestamp)
        invalidate()
        return visiblePoints < points.size
    }

    fun stepOneMinute(): Boolean = stepReplay()
    fun currentTime(): String = replayTime
    fun isComplete(): Boolean = points.isNotEmpty() && visiblePoints >= points.size

    private fun timeOf(value: String?): String {
        if (value == null || value.length < 16) return "--:--:--"
        return if (value.length >= 19) value.substring(11, 19) else value.substring(11, 16) + ":00"
    }

    private fun parseEpochSeconds(value: String?): Long {
        if (value == null || value.length < 16) return 0L
        val h = value.substring(11, 13).toIntOrNull() ?: return 0L
        val m = value.substring(14, 16).toIntOrNull() ?: return 0L
        val s = if (value.length >= 19) value.substring(17, 19).toIntOrNull() ?: 0 else 0
        return h * 3600L + m * 60L + s
    }

    private fun bucketPoints(): List<IntradayReplayPoint> {
        if (points.isEmpty()) return emptyList()
        if (chartIntervalSeconds <= 1L) return points.take(visiblePoints)
        val visible = points.take(visiblePoints)
        val groups = linkedMapOf<Long, MutableList<IntradayReplayPoint>>()
        visible.forEach { point ->
            val bucket = sessionBucketSeconds(parseEpochSeconds(point.timestamp))
            groups.getOrPut(bucket) { mutableListOf() }.add(point)
        }
        return groups.values.map { bars ->
            val first = bars.first()
            val last = bars.last()
            last.copy(
                timestamp = last.timestamp,
                cash_price = bars.map { it.cash_price }.average(),
                future_price = bars.map { it.future_price }.average(),
                gap = bars.map { it.gap }.average(),
                gap_pct = bars.map { it.gap_pct }.average(),
                open = first.open,
                high = bars.maxOf { it.high },
                low = bars.minOf { it.low },
                close = last.close,
            )
        }
    }

    private fun sessionBucketSeconds(totalSeconds: Long): Long {
        val sessionOpen = (9 * 60 + 15) * 60L
        val sessionClose = (15 * 60 + 30) * 60L
        if (totalSeconds < sessionOpen) return (totalSeconds / chartIntervalSeconds) * chartIntervalSeconds
        if (totalSeconds >= sessionClose) return sessionClose - chartIntervalSeconds
        return sessionOpen + ((totalSeconds - sessionOpen) / chartIntervalSeconds) * chartIntervalSeconds
    }

    private fun drawSeries(canvas: Canvas, values: List<Double>, paint: Paint, top: Float, bottom: Float, left: Float, right: Float) {
        if (values.isEmpty()) return
        val minValue = values.minOrNull() ?: return
        val maxValue = values.maxOrNull() ?: return
        val range = max(0.000001, maxValue - minValue)
        fun y(value: Double): Float = (bottom - ((value - minValue) / range * (bottom - top))).toFloat()
        var previousX = left
        var previousY = y(values.first())
        values.forEachIndexed { index, value ->
            val x = if (values.size == 1) left else left + (right - left) * index / (values.size - 1f)
            val currentY = y(value)
            if (index > 0) canvas.drawLine(previousX, previousY, x, currentY, paint)
            previousX = x
            previousY = currentY
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val visible = bucketPoints()
        if (visible.isEmpty()) {
            canvas.drawText("Select GRAPH from a calendar row", 18f, 42f, axisPaint)
            return
        }

        val left = 58f
        val right = width - 16f
        val panelHeight = (height - 66f) / 3f
        val panels = listOf(
            "CASH PRICE" to cashPaint,
            "FUTURE PRICE" to futurePaint,
            "GAP = FUTURE − CASH" to gapPaint,
        )
        val values = listOf(
            visible.map { it.cash_price },
            visible.map { it.future_price },
            visible.map { it.gap },
        )

        panels.forEachIndexed { index, (title, paint) ->
            val top = 18f + index * panelHeight
            val bottom = top + panelHeight - 18f
            canvas.drawLine(left, bottom, right, bottom, gridPaint)
            canvas.drawText(title, left, top + 16f, paint)
            drawSeries(canvas, values[index], paint, top + 24f, bottom - 4f, left, right)
            val minValue = values[index].minOrNull() ?: 0.0
            val maxValue = values[index].maxOrNull() ?: 0.0
            canvas.drawText(String.format("%.2f", maxValue), 4f, top + 16f, axisPaint)
            canvas.drawText(String.format("%.2f", minValue), 4f, bottom, axisPaint)
        }

        val lastIndex = visible.lastIndex
        if (lastIndex >= 0) {
            val x = if (lastIndex == 0) left else left + (right - left) * lastIndex / (visible.size - 1f)
            canvas.drawLine(x, 18f, x, height - 32f, markerPaint)
            canvas.drawText("${labelFor(replayStepSeconds.toInt())} • $replayTime", left, height - 8f, axisPaint)
        }
    }
}
