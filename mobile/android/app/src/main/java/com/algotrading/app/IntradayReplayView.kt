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
    private val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF9CB4CF.toInt(); textSize = 28f; typeface = Typeface.MONOSPACE }
    private val wickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 2f }
    private val bodyPaint = Paint(Paint.ANTI_ALIAS_FLAG)
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
            rootView.findViewById<Button>(id)?.alpha = if (seconds == chartIntervalSeconds) 1f else 0.62f
        }
    }

    fun setData(newPoints: List<IntradayReplayPoint>) {
        points = newPoints.sortedBy { it.timestamp }
        resetReplay()
    }

    fun setReplayMode(seconds: Int) {
        val supported = listOf(1L, 60L, 300L, 900L, 1800L)
        val value = supported.minByOrNull { kotlin.math.abs(it - seconds.toLong()) } ?: 900L
        chartIntervalSeconds = value
        replayStepSeconds = value
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

    private fun sessionBucketSeconds(totalSeconds: Long): Long {
        val sessionOpen = (9 * 60 + 15) * 60L
        val sessionClose = (15 * 60 + 30) * 60L
        if (totalSeconds < sessionOpen) return (totalSeconds / chartIntervalSeconds) * chartIntervalSeconds
        if (totalSeconds >= sessionClose) return sessionClose - chartIntervalSeconds
        return sessionOpen + ((totalSeconds - sessionOpen) / chartIntervalSeconds) * chartIntervalSeconds
    }

    private fun candles(): List<ReplayCandle> {
        val visible = points.take(visiblePoints)
        if (visible.isEmpty()) return emptyList()
        val groups = linkedMapOf<Long, MutableList<IntradayReplayPoint>>()
        visible.forEach { p ->
            val bucket = sessionBucketSeconds(parseEpochSeconds(p.timestamp))
            groups.getOrPut(bucket) { mutableListOf() }.add(p)
        }
        return groups.map { (bucket, bars) ->
            ReplayCandle(bucket, bars.first().open, bars.maxOf { it.high }, bars.minOf { it.low }, bars.last().close)
        }
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val cs = candles()
        if (cs.isEmpty()) {
            canvas.drawText("Select GRAPH from a calendar row", 18f, 42f, axisPaint)
            return
        }
        val left = 58f
        val right = width - 16f
        val top = 18f
        val bottom = height - 30f
        val minPrice = cs.minOf { it.low }
        val maxPrice = cs.maxOf { it.high }
        val range = max(0.000001, maxPrice - minPrice)
        fun y(price: Double): Float = (bottom - ((price - minPrice) / range * (bottom - top))).toFloat()
        canvas.drawText("${labelFor(chartIntervalSeconds.toInt())} • replay $replayTime", left, height - 8f, axisPaint)
        canvas.drawText(String.format("%.2f", maxPrice), 4f, top + 10f, axisPaint)
        canvas.drawText(String.format("%.2f", minPrice), 4f, bottom, axisPaint)

        val slot = max(5f, (right - left) / cs.size)
        val bodyWidth = min(24f, slot * 0.62f)
        cs.forEachIndexed { index, c ->
            val x = left + slot * index + slot / 2f
            val openY = y(c.open)
            val closeY = y(c.close)
            val highY = y(c.high)
            val lowY = y(c.low)
            val up = c.close >= c.open
            wickPaint.color = if (up) 0xFF38D39F.toInt() else 0xFFFF6B6B.toInt()
            bodyPaint.color = wickPaint.color
            canvas.drawLine(x, highY, x, lowY, wickPaint)
            val bodyTop = min(openY, closeY)
            val bodyBottom = max(openY, closeY)
            canvas.drawRect(x - bodyWidth / 2f, bodyTop, x + bodyWidth / 2f, max(bodyTop + 2f, bodyBottom), bodyPaint)
            if (index == cs.lastIndex) {
                val total = c.bucket
                val h = (total / 3600L) % 24L
                val m = (total / 60L) % 60L
                val s = total % 60L
                canvas.drawText(String.format("%02d:%02d:%02d", h, m, s), max(left, x - 28f), bottom + 22f, axisPaint)
            }
        }
    }
}