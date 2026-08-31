package com.algotrading.app

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.*
import okhttp3.*
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class MainActivity : AppCompatActivity() {

    private lateinit var tvStatus: TextView
    private lateinit var etSymbol: EditText
    private lateinit var tvQuoteDetails: TextView
    private lateinit var btnFetchQuote: Button
    private lateinit var etQuantity: EditText
    private lateinit var btnBuy: Button
    private lateinit var btnSell: Button
    private lateinit var tvOrderResult: TextView

    private val orderHistoryList = mutableListOf<String>()
    private var webSocket: WebSocket? = null
    
    // Optimized OkHttpClient for stable streaming
    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .build()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvStatus = findViewById(R.id.tvStatus)
        etSymbol = findViewById(R.id.etSymbol)
        tvQuoteDetails = findViewById(R.id.tvQuoteDetails)
        btnFetchQuote = findViewById(R.id.btnFetchQuote)
        etQuantity = findViewById(R.id.etQuantity)
        btnBuy = findViewById(R.id.btnBuy)
        btnSell = findViewById(R.id.btnSell)
        tvOrderResult = findViewById(R.id.tvOrderResult)

        checkServerStatus()

        btnFetchQuote.setOnClickListener {
            val symbol = etSymbol.text.toString().trim().uppercase()
            if (symbol.isNotEmpty()) {
                startLiveWebSocket(symbol)
            } else {
                etSymbol.error = "Please enter a symbol"
            }
        }

        btnBuy.setOnClickListener { placeOrder("BUY") }
        btnSell.setOnClickListener { placeOrder("SELL") }
    }

    private fun checkServerStatus() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.getRootStatus()
                withContext(Dispatchers.Main) {
                    tvStatus.text = "Server Status: ${response.status}"
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    tvStatus.text = "Server Error: Offline"
                }
            }
        }
    }

    private fun startLiveWebSocket(symbol: symbolModelToStringSafe(symbol)) {
        webSocket?.close(1000, "Switching")

        // Use 10.0.2.2 for Emulator, or your live server IP for real device
        val request = Request.Builder().url("ws://10.0.2.2:8000/ws/market-data/$symbol").build()

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                runOnUiThread { tvQuoteDetails.text = "Connected to $symbol Live Stream" }
            }

            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    val json = JSONObject(text)
                    val sSymbol = json.getString("symbol")
                    val bid = json.getDouble("bidPrice")
                    val ask = json.getDouble("askPrice")
                    val ltp = json.getDouble("ltp")
                    val spread = json.getDouble("spread")

                    // Safely update UI without lagging the main thread
                    runOnUiThread {
                        tvQuoteDetails.text = """
                            🟢 LIVE MARKET ($sSymbol)
                            -------------------------
                            Bid Price : ₹$bid
                            Ask Price : ₹$ask
                            LTP       : ₹$ltp
                            Spread    : ₹$spread
                        """.trimIndent()
                    }
                } catch (e: Exception) {
                    // Skip malformed packets smoothly
                }
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvQuoteDetails.text = "Stream Connection Lost" }
            }
        })
    }

    private fun symbolModelToStringSafe(s: String): String = s

    private fun placeOrder(transactionType: String) {
        val symbol = etSymbol.text.toString().trim().uppercase()
        val qtyStr = etQuantity.text.toString().trim()

        if (symbol.isEmpty()) { etSymbol.error = "Enter symbol"; return }
        if (qtyStr.isEmpty()) { etQuantity.error = "Enter quantity"; return }

        val quantity = qtyStr.toIntOrNull() ?: 1
        val request = OrderRequest(symbol = symbol, quantity = quantity, transactionType = transactionType)

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.placeOrder(request)
                withContext(Dispatchers.Main) {
                    val orderDetail = "[$transactionType] $quantity x $symbol | ID: ${response.orderId}"
                    orderHistoryList.add(0, orderDetail)
                    tvOrderResult.text = "--- Order History ---\n\n" + orderHistoryList.joinToString("\n\n")
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    orderHistoryList.add(0, "[FAILED] $transactionType $symbol")
                    tvOrderResult.text = "--- Order History ---\n\n" + orderHistoryList.joinToString("\n\n")
                }
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        webSocket?.close(1000, "Closed")
    }
}
