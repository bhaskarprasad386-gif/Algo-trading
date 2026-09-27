package com.algotrading.app

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.google.android.gms.auth.api.signin.GoogleSignInClient
import com.google.android.gms.auth.api.signin.GoogleSignInOptions
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class AuthActivity : AppCompatActivity() {
    private lateinit var googleSignInClient: GoogleSignInClient
    private lateinit var tvMode: TextView
    private lateinit var identifier: EditText
    private lateinit var password: EditText
    private lateinit var name: EditText
    private lateinit var submit: Button
    private lateinit var toggle: Button
    private lateinit var forgot: Button
    private lateinit var result: TextView
    private var signup = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        AppContextHolder.context = applicationContext
        setContentView(R.layout.activity_auth)
        tvMode = findViewById(R.id.tvAuthMode)
        identifier = findViewById(R.id.etAuthIdentifier)
        password = findViewById(R.id.etAuthPassword)
        name = findViewById(R.id.etAuthName)
        submit = findViewById(R.id.btnAuthSubmit)
        toggle = findViewById(R.id.btnAuthToggle)
        forgot = findViewById(R.id.btnAuthForgot)
        result = findViewById(R.id.tvAuthResult)
        val googleButton: Button = findViewById(R.id.btnGoogleSignIn)
        val googleClientId = BuildConfig.GOOGLE_WEB_CLIENT_ID.trim()
        if (googleClientId.isBlank()) {
            googleButton.isEnabled = false
            googleButton.text = "GOOGLE LOGIN (NOT CONFIGURED)"
        } else {
            val options = GoogleSignInOptions.Builder(GoogleSignInOptions.DEFAULT_SIGN_IN)
                .requestIdToken(googleClientId)
                .requestEmail()
                .build()
            googleSignInClient = GoogleSignIn.getClient(this, options)
            googleButton.setOnClickListener { startActivityForResult(googleSignInClient.signInIntent, GOOGLE_SIGN_IN_REQUEST) }
        }
        submit.setOnClickListener { submitAuth() }
        toggle.setOnClickListener { toggleMode() }
        forgot.setOnClickListener { requestPasswordReset() }
        if (!ApiService.getToken(this).isNullOrBlank()) validateExistingSession()
    }

    @Deprecated("Android Google Sign-In callback")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: android.content.Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != GOOGLE_SIGN_IN_REQUEST) return
        try {
            val account = GoogleSignIn.getSignedInAccountFromIntent(data).getResult(com.google.android.gms.common.api.ApiException::class.java)
            val token = account.idToken
            if (token.isNullOrBlank()) throw IllegalStateException("Google did not return an ID token")
            lifecycleScope.launch(Dispatchers.IO) {
                try {
                    val response = ApiService.retrofitService.googleLogin(GoogleLoginRequest(token))
                    ApiService.saveToken(this@AuthActivity, response.access_token)
                    ApiService.retrofitService.me()
                    withContext(Dispatchers.Main) { openDashboard() }
                } catch (error: Exception) {
                    ApiService.clearToken(this@AuthActivity)
                    withContext(Dispatchers.Main) { result.text = "Google login failed: ${error.message ?: "API error"}" }
                }
            }
        } catch (error: Exception) {
            val statusCode = (error as? com.google.android.gms.common.api.ApiException)?.statusCode
            val statusText = when (statusCode) {
                10 -> "DEVELOPER_ERROR"
                12501 -> "SIGN_IN_CANCELLED"
                else -> null
            }
            result.text = if (statusCode != null) {
                "Google Sign-In failed: " + (statusText ?: "STATUS_" + statusCode) + " (" + statusCode + ")"
            } else {
                "Google Sign-In failed: " + (error.message ?: error.javaClass.simpleName)
            }
        }
    }

    private fun toggleMode() {
        signup = !signup
        tvMode.text = if (signup) "Create your trading account" else "Login to continue"
        submit.text = if (signup) "CREATE ACCOUNT" else "LOGIN"
        toggle.text = if (signup) "BACK TO LOGIN" else "CREATE ACCOUNT"
        name.visibility = if (signup) View.VISIBLE else View.GONE
        forgot.visibility = if (signup) View.GONE else View.VISIBLE
        result.text = ""
    }

    private fun validateExistingSession() {
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                ApiService.retrofitService.me()
                withContext(Dispatchers.Main) { openDashboard() }
            } catch (_: Exception) {
                ApiService.clearToken(this@AuthActivity)
                withContext(Dispatchers.Main) { result.text = "Session expired. Please login again." }
            }
        }
    }

    private fun requestPasswordReset() {
        val id = identifier.text.toString().trim()
        if (id.isBlank()) {
            identifier.error = "Email or mobile number required"
            return
        }
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val response = ApiService.retrofitService.requestPasswordReset(PasswordResetRequest(id))
                withContext(Dispatchers.Main) { result.text = response.message }
            } catch (error: Exception) {
                withContext(Dispatchers.Main) {
                    result.text = "Password reset request failed: ${error.message ?: "API error"}"
                }
            }
        }
    }

    private fun submitAuth() {
        val id = identifier.text.toString().trim()
        val pass = password.text.toString()
        if (id.isBlank()) { identifier.error = "Email or mobile number required"; return }
        if (pass.length < 8) { password.error = "Password must be at least 8 characters"; return }
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val token = if (signup) {
                    val request = if (id.contains("@")) RegisterRequest(email = id, password = pass, full_name = name.text.toString().trim().ifBlank { null })
                    else RegisterRequest(mobile_number = id, password = pass, full_name = name.text.toString().trim().ifBlank { null })
                    ApiService.retrofitService.register(request)
                } else {
                    ApiService.retrofitService.login(LoginRequest(id, pass))
                }
                ApiService.saveToken(this@AuthActivity, token.access_token)
                ApiService.retrofitService.me()
                withContext(Dispatchers.Main) { openDashboard() }
            } catch (error: Exception) {
                ApiService.clearToken(this@AuthActivity)
                withContext(Dispatchers.Main) { result.text = if (signup) "Sign Up failed: ${error.message ?: "API error"}" else "Login failed: ${error.message ?: "API error"}" }
            }
        }
    }

    private fun openDashboard() {
        startActivity(Intent(this, MainActivity::class.java))
        finish()
    }
    companion object { private const val GOOGLE_SIGN_IN_REQUEST = 9001 }
}
