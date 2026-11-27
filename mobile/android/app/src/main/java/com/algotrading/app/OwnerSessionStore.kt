package com.algotrading.app

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.charset.StandardCharsets
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.security.InvalidKeyException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * The cipher boundary is injectable so the encrypted-file lifecycle can be
 * unit-tested on the JVM without an Android Keystore implementation.
 */
internal interface OwnerSessionCipher {
    fun encrypt(plaintext: ByteArray): ByteArray
    fun decrypt(ciphertext: ByteArray): ByteArray
    fun reset() {}
}

internal data class OwnerSession(val accessToken: String, val expiresAtEpochMs: Long)

/** Stores only ciphertext in app-private, non-backed-up storage. */
internal class EncryptedOwnerSessionStore(
    private val file: File,
    private val cipher: OwnerSessionCipher,
) {
    @Synchronized
    fun save(token: String, expiresAtEpochMs: Long) {
        require(token.isNotBlank()) { "owner session token cannot be blank" }
        require(token.length <= MAX_TOKEN_LENGTH) { "owner session token is too large" }
        require(expiresAtEpochMs > System.currentTimeMillis()) { "owner session expiry must be in the future" }
        val tokenBytes = token.toByteArray(StandardCharsets.UTF_8)
        val payload = ByteBuffer.allocate(12 + tokenBytes.size)
            .putLong(expiresAtEpochMs)
            .putInt(tokenBytes.size)
            .put(tokenBytes)
            .array()
        val encrypted = cipher.encrypt(payload)
        val parent = file.parentFile ?: throw IllegalStateException("owner session directory is unavailable")
        if (!parent.exists() && !parent.mkdirs()) {
            throw IllegalStateException("owner session directory could not be created")
        }
        val temporary = File(parent, "${file.name}.tmp")
        try {
            FileOutputStream(temporary).use { output ->
                output.write(encrypted)
                output.fd.sync()
            }
            if (file.exists() && !file.delete()) {
                throw IllegalStateException("previous owner session could not be replaced")
            }
            if (!temporary.renameTo(file)) {
                throw IllegalStateException("encrypted owner session could not be committed")
            }
        } finally {
            if (temporary.exists()) temporary.delete()
        }
    }

    @Synchronized
    fun load(nowEpochMs: Long = System.currentTimeMillis()): OwnerSession? {
        if (!file.isFile) return null
        return try {
            val payload = ByteBuffer.wrap(cipher.decrypt(file.readBytes()))
            if (payload.remaining() < 12) throw GeneralSecurityException("owner session payload is truncated")
            val expiresAtEpochMs = payload.long
            val tokenLength = payload.int
            if (tokenLength <= 0 || tokenLength > MAX_TOKEN_LENGTH || payload.remaining() != tokenLength) {
                throw GeneralSecurityException("owner session payload length is invalid")
            }
            val tokenBytes = ByteArray(tokenLength).also(payload::get)
            val token = String(tokenBytes, StandardCharsets.UTF_8)
            if (token.isBlank() || expiresAtEpochMs <= nowEpochMs + EXPIRY_SKEW_MS) {
                clear()
                null
            } else OwnerSession(token, expiresAtEpochMs)
        } catch (_: Exception) {
            // Corrupt ciphertext or an invalidated Keystore key is never
            // treated as an authenticated session. Force a fresh login.
            try { cipher.reset() } catch (_: Exception) { }
            clear()
            null
        }
    }

    @Synchronized
    fun clear() {
        if (file.exists() && !file.delete()) {
            throw IllegalStateException("encrypted owner session could not be deleted")
        }
        File(file.parentFile, "${file.name}.tmp").delete()
    }

    companion object {
        private const val MAX_TOKEN_LENGTH = 8192
        private const val EXPIRY_SKEW_MS = 30_000L
    }
}

/** AES-256-GCM key protected by Android Keystore; the key never leaves Keystore. */
internal class AndroidKeystoreOwnerSessionCipher(
    private val alias: String = "algo_trading_owner_session_v1",
) : OwnerSessionCipher {
    override fun encrypt(plaintext: ByteArray): ByteArray {
        var cipher = Cipher.getInstance(TRANSFORMATION)
        try {
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        } catch (_: InvalidKeyException) {
            reset()
            cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        }
        val iv = cipher.iv
        val encrypted = cipher.doFinal(plaintext)
        return ByteBuffer.allocate(2 + iv.size + encrypted.size)
            .put(VERSION)
            .put(iv.size.toByte())
            .put(iv)
            .put(encrypted)
            .array()
    }

    override fun decrypt(ciphertext: ByteArray): ByteArray {
        if (ciphertext.size < 2 + GCM_IV_BYTES + GCM_TAG_BYTES) {
            throw GeneralSecurityException("owner session ciphertext is truncated")
        }
        val buffer = ByteBuffer.wrap(ciphertext)
        if (buffer.get() != VERSION) throw GeneralSecurityException("owner session format is unsupported")
        val ivLength = buffer.get().toInt() and 0xff
        if (ivLength != GCM_IV_BYTES || buffer.remaining() <= ivLength + GCM_TAG_BYTES) {
            throw GeneralSecurityException("owner session IV or payload is invalid")
        }
        val iv = ByteArray(ivLength)
        buffer.get(iv)
        val encrypted = ByteArray(buffer.remaining())
        buffer.get(encrypted)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, getExistingKey(), GCMParameterSpec(GCM_TAG_BITS, iv))
        return cipher.doFinal(encrypted)
    }

    override fun reset() {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        if (keyStore.containsAlias(alias)) keyStore.deleteEntry(alias)
    }

    private fun getExistingKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        return keyStore.getKey(alias, null) as? SecretKey
            ?: throw GeneralSecurityException("owner session key is unavailable")
    }

    private fun getOrCreateKey(): SecretKey {
        getExistingKeyOrNull()?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .setKeySize(256)
                .build(),
        )
        return generator.generateKey()
    }

    private fun getExistingKeyOrNull(): SecretKey? {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        return keyStore.getKey(alias, null) as? SecretKey
    }

    companion object {
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        private const val VERSION: Byte = 1
        private const val GCM_IV_BYTES = 12
        private const val GCM_TAG_BITS = 128
        private const val GCM_TAG_BYTES = GCM_TAG_BITS / 8
    }
}

internal fun createOwnerSessionStore(context: Context): EncryptedOwnerSessionStore =
    EncryptedOwnerSessionStore(
        File(context.applicationContext.noBackupFilesDir, "owner_session.enc"),
        AndroidKeystoreOwnerSessionCipher(),
    )
