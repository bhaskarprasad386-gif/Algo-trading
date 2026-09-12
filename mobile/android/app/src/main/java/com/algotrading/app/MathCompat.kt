package com.algotrading.app

/** Resolves the legacy payoff graph's mixed Float/Double max expression. */
fun max(left: Float, right: Double): Double = kotlin.math.max(left.toDouble(), right)
