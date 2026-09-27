package com.algotrading.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.net.URL
import java.security.MessageDigest

class UpdateActivity : AppCompatActivity() {
    private lateinit var status: TextView
    private lateinit var notes: TextView
    private lateinit var update: Button
    private var pendingRemote: AppUpdateInfo? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
        }
        status = TextView(this).apply { textSize = 18f; text = "Checking for updates..." }
        notes = TextView(this).apply { textSize = 14f; setPadding(0, 24, 0, 24) }
        update = Button(this).apply { text = "UPDATE NOW"; isEnabled = false }
        root.addView(status); root.addView(notes); root.addView(update)
        setContentView(root)

        update.setOnClickListener {
            pendingRemote?.let { remote -> lifecycleScope.launch { downloadAndInstall(remote) } }
                ?: checkForUpdate()
        }
        checkForUpdate()
    }

    override fun onResume() {
        super.onResume()
        pendingRemote?.let { remote -> lifecycleScope.launch { installCachedIfVerified(remote) } }
    }

    private fun checkForUpdate() = lifecycleScope.launch(Dispatchers.IO) {
        try {
            val remote = ApiService.retrofitService.appUpdate()
            val currentCode = packageManager.getPackageInfo(packageName, 0).longVersionCode
            withContext(Dispatchers.Main) {
                pendingRemote = remote
                notes.text = remote.release_notes.ifBlank { "No release notes." }
                when {
                    remote.version_code <= currentCode -> {
                        status.text = "App is up to date"; update.isEnabled = false
                    }
                    remote.apk_url.isBlank() -> {
                        status.text = "Update available, but APK is not published yet."
                        update.isEnabled = false
                    }
                    else -> {
                        status.text = "Update available • v${remote.version_name}"
                        update.isEnabled = true
                    }
                }
            }
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                status.text = "Update check failed • ${e.message ?: "network error"}"
                update.isEnabled = false
            }
        }
    }

    private suspend fun downloadAndInstall(remote: AppUpdateInfo) {
        val apk = cachedApk(remote)
        if (isVerifiedApk(apk, remote)) { installApk(apk); return }

        withContext(Dispatchers.Main) {
            update.isEnabled = false
            status.text = "Downloading v${remote.version_name}..."
        }

        try {
            apk.parentFile?.mkdirs()
            URL(remote.apk_url).openStream().use { input ->
                apk.outputStream().use { output -> input.copyTo(output) }
            }
            if (!isVerifiedApk(apk, remote)) {
                apk.delete()
                throw IllegalStateException("Downloaded APK checksum verification failed")
            }
            installApk(apk)
        } catch (e: Exception) {
            withContext(Dispatchers.Main) {
                status.text = "Update failed • ${e.message ?: "download error"}"
                update.isEnabled = true
            }
        }
    }

    private suspend fun installCachedIfVerified(remote: AppUpdateInfo) {
        val apk = cachedApk(remote)
        if (!isVerifiedApk(apk, remote)) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !packageManager.canRequestPackageInstalls()) {
            withContext(Dispatchers.Main) {
                status.text = "Allow installation permission, then return here."
                update.isEnabled = true
            }
            return
        }
        installApk(apk)
    }

    private suspend fun installApk(apk: File) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !packageManager.canRequestPackageInstalls()) {
            withContext(Dispatchers.Main) {
                status.text = "Allow this app to install updates."
                update.isEnabled = true
                startActivity(Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:$packageName")
                ))
            }
            return
        }

        withContext(Dispatchers.Main) {
            val uri = FileProvider.getUriForFile(
                this@UpdateActivity, "$packageName.fileprovider", apk
            )
            startActivity(Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            })
        }
    }

    private fun cachedApk(remote: AppUpdateInfo): File {
        val safeVersion = remote.version_name.replace(Regex("[^A-Za-z0-9._-]"), "_")
        val dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: filesDir
        return File(dir, "algo-trading-$safeVersion.apk")
    }

    private fun isVerifiedApk(file: File, remote: AppUpdateInfo): Boolean {
        if (!file.isFile || file.length() == 0L) return false
        val expected = remote.sha256.trim().lowercase()
        if (expected.isBlank()) return true
        return sha256(file) == expected
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(8192)
            while (true) {
                val n = input.read(buffer)
                if (n <= 0) break
                digest.update(buffer, 0, n)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}