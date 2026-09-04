package com.algotrading.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackendConfigTest {
    @Test
    fun production_backend_urls_are_secure_and_not_emulator_only() {
        assertTrue(BuildConfig.BACKEND_BASE_URL.startsWith("https://"))
        assertTrue(BuildConfig.BACKEND_WS_URL.startsWith("wss://"))
        assertFalse(BuildConfig.BACKEND_BASE_URL.contains("10.0.2.2"))
        assertFalse(BuildConfig.BACKEND_WS_URL.contains("10.0.2.2"))
        assertFalse(BuildConfig.BACKEND_BASE_URL.contains("localhost"))
        assertFalse(BuildConfig.BACKEND_WS_URL.contains("localhost"))
        assertFalse(BuildConfig.BACKEND_BASE_URL.contains("127.0.0.1"))
        assertFalse(BuildConfig.BACKEND_WS_URL.contains("127.0.0.1"))
    }
}
