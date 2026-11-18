package com.algotrading.app

import java.io.File
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class OwnerSessionStoreTest {
    private class TestCipher : OwnerSessionCipher {
        private val key = SecretKeySpec(ByteArray(32) { (it + 1).toByte() }, "AES")
        var resetCount = 0

        override fun encrypt(plaintext: ByteArray): ByteArray {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key)
            val iv = cipher.iv
            val body = cipher.doFinal(plaintext)
            return byteArrayOf(iv.size.toByte()) + iv + body
        }

        override fun decrypt(ciphertext: ByteArray): ByteArray {
            val ivLength = ciphertext[0].toInt() and 0xff
            val iv = ciphertext.copyOfRange(1, 1 + ivLength)
            val body = ciphertext.copyOfRange(1 + ivLength, ciphertext.size)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
            return cipher.doFinal(body)
        }

        override fun reset() {
            resetCount += 1
        }
    }

    private fun tempFile(): File {
        val directory = Files.createTempDirectory("owner-session-test").toFile()
        directory.deleteOnExit()
        return File(directory, "owner_session.enc")
    }

    @Test
    fun encryptedSessionSurvivesStoreRecreationWithoutPlaintextAtRest() {
        val file = tempFile()
        val token = "owner-access-token-secret"
        EncryptedOwnerSessionStore(file, TestCipher()).save(token)

        assertTrue(file.isFile)
        assertFalse(String(file.readBytes(), StandardCharsets.UTF_8).contains(token))
        assertArrayEquals(token.toByteArray(StandardCharsets.UTF_8),
            EncryptedOwnerSessionStore(file, TestCipher()).load()!!.toByteArray(StandardCharsets.UTF_8))
    }

    @Test
    fun clearRemovesDurableOwnerSession() {
        val file = tempFile()
        val store = EncryptedOwnerSessionStore(file, TestCipher())
        store.save("owner-token")
        store.clear()

        assertFalse(file.exists())
        assertNull(store.load())
    }

    @Test
    fun tamperedCiphertextIsDiscardedAndNeverRestored() {
        val file = tempFile()
        val cipher = TestCipher()
        val store = EncryptedOwnerSessionStore(file, cipher)
        store.save("owner-token")
        val bytes = file.readBytes()
        bytes[bytes.lastIndex] = (bytes.last().toInt() xor 0x01).toByte()
        file.writeBytes(bytes)

        assertNull(store.load())
        assertFalse(file.exists())
        assertTrue(cipher.resetCount > 0)
    }

    @Test
    fun blankTokenCannotBePersisted() {
        val file = tempFile()
        val store = EncryptedOwnerSessionStore(file, TestCipher())

        assertThrows(IllegalArgumentException::class.java) { store.save("  ") }
        assertFalse(file.exists())
    }

    @Test
    fun newOwnerLoginReplacesPriorEncryptedSession() {
        val file = tempFile()
        val store = EncryptedOwnerSessionStore(file, TestCipher())
        store.save("old-token")
        store.save("new-token")

        assertTrue(file.isFile)
        assertTrue(store.load() == "new-token")
    }
}
