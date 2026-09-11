package com.algotrading.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import kotlin.math.max
import kotlin.math.min

data class ReplayCandle(val bucket: Int, val open: Double, val high: Double, val low: Double, val close: Double)

class IntradayReplayView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {
    private val axisPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = 0xFF7F93AD.toInt(); textSize = 28f; typeface = Typeface.MONOSPACE }
    private val wickPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeWidth = 2f }
    private val bodyPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private var points: List<IntradayReplayPoint> = emptyList()
    private var visibleMinutes = 0
    private var replayTime = "--:--"

    fun setData(newPoints: List<IntradayReplayPoint>) {
        points = newPoints.sortedBy { it.timestamp }
        visibleMinutes = if (points.isEmpty()) 0 else 1
        replayTime = timeOf(points.getOrNull(visibleMinutes - 1)?.timestamp)
        invalidate()
    }

    fun resetReplay() {
        visibleMinutes = if (points.isEmpty()) 0 else 1
        replayTime = timeOf(points.getOrNull(visibleMinutes - 1)?.timestamp)
        invalidate()
    }

    fun stepOneMinute(): Boolean {
        if (visibleMinutes >= points.size) return false
        visibleMinutes += 1
        replayTime = timeOf(points.getOrNull(visibleMinutes - 1)?.timestamp)
        invalidate()
        return visibleMinutes < points.size
    }

    fun currentTime(): String = replayTime
    fun isComplete(): Boolean = points.isNotEmpty() && visibleMinutes >= points.size

    private fun timeOf(value: String?): String = if (value != null && value.length >= 16) value.substring(11, 16) else "--:--"

    private fun sessionBucketMinutes(totalMinutes: Int): Int {
        val sessionOpen = 9 * 60 + 15
        val sessionClose = 15 * 60 + 30
        if (totalMinutes < sessionOpen) return (totalMinutes / 15) * 15
        if (totalMinutes >= sessionClose) return sessionClose - 15
        return sessionOpen + ((totalMinutes - sessionOpen) / 15) * 15
    }

    private fun candles(): List<ReplayCandle> {
        val visible = points.take(visibleMinutes)
        if (visible.isEmpty()) return emptyList()
        val groups = linkedMapOf<Int, MutableList<IntradayReplayPoint>>()
        visible.forEach { p ->
            val time = timeOf(p.timestamp)
            val h = time.substringBefore(":").toIntOrNull() ?: 0
            val m = time.substringAfter(":").toIntOrNull() ?: 0
            val bucket = sessionBucketMinutes(h * 60 + m)
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
        canvas.drawText("15m  •  replay $replayTime", left, height - 8f, axisPaint)
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
                canvas.drawText("${c.bucket / 60 % 24}:${(c.bucket % 60).toString().padStart(2, '0')}", max(left, x - 20f), bottom + 22f, axisPaint)
            }
        }
    }
}
