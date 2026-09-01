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
    private val client = OkHttpClient.Builder().pingInterval(10, TimeUnit.SECONDS).build()

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
            if (symbol.isNotEmpty()) startLiveWebSocket(symbol) else etSymbol.error = "Please enter a symbol"
        }
        btnBuy.setOnClickListener { placeOrder("BUY") }
        btnSell.setOnClickListener { placeOrder("SELL") }
    }

    private fun checkServerStatus() = CoroutineScope(Dispatchers.IO).launch {
        try {
            val response = ApiService.retrofitService.getRootStatus()
            withContext(Dispatchers.Main) { tvStatus.text = "Server Status: ${response.status}" }
        } catch (_: Exception) {
            withContext(Dispatchers.Main) { tvStatus.text = "Server Error: Offline" }
        }
    }

    private fun startLiveWebSocket(symbol: String) {
        webSocket?.close(1000, "Switching symbol")
        val request = Request.Builder().url("${BuildConfig.BACKEND_WS_URL}/ws/market-data/$symbol").build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                runOnUiThread { tvQuoteDetails.text = "Connected to Live Stream for $symbol..." }
            }
            override fun onMessage(ws: WebSocket, text: String) {
                try {
                    val json = JSONObject(text)
                    val sSymbol = json.getString("symbol")
                    val bid = json.getDouble("bidPrice")
                    val ask = json.getDouble("askPrice")
                    val ltp = json.getDouble("ltp")
                    val spread = json.getDouble("spread")
                    runOnUiThread {
                        tvQuoteDetails.text = "LIVE MARKET ($sSymbol)\n-------------------------\nBid Price : ₹$bid\nAsk Price : ₹$ask\nLTP       : ₹$ltp\nSpread    : ₹$spread"
                    }
                } catch (_: Exception) { }
            }
            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread { tvQuoteDetails.text = "Live Stream Connection Lost" }
            }
        })
    }

    private fun placeOrder(transactionType: String) {
        val symbol = etSymbol.text.toString().trim().uppercase()
        val qtyStr = etQuantity.text.toString().trim()
        if (symbol.isEmpty()) { etSymbol.error = "Enter symbol"; return }
        if (qtyStr.isEmpty()) { etQuantity.error = "Enter quantity"; return }
        val quantity = qtyStr.toIntOrNull()
        if (quantity == null || quantity <= 0) { etQuantity.error = "Enter a positive quantity"; return }

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.placeOrder(OrderRequest(symbol, quantity, transactionType))
                withContext(Dispatchers.Main) {
                    orderHistoryList.add(0, "[$transactionType] $quantity x $symbol | ID: ${response.orderId}")
                    tvOrderResult.text = "--- Paper Order History ---\n\n" + orderHistoryList.joinToString("\n\n")
                }
            } catch (_: Exception) {
                withContext(Dispatchers.Main) {
                    orderHistoryList.add(0, "[FAILED] $transactionType $symbol")
                    tvOrderResult.text = "--- Paper Order History ---\n\n" + orderHistoryList.joinToString("\n\n")
                }
            }
        }
    }

    override fun onDestroy() {
        webSocket?.close(1000, "App closed")
        client.dispatcher.executorService.shutdown()
        super.onDestroy()
    }
}
