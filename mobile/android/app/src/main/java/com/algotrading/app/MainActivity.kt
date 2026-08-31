package com.algotrading.app

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity() {

    private lateinit var tvStatus: TextView
    private lateinit var etSymbol: EditText
    private lateinit var tvQuoteDetails: TextView
    private lateinit var btnFetchQuote: Button
    private lateinit var etQuantity: EditText
    private lateinit var btnBuy: Button
    private lateinit var btnSell: Button
    private lateinit var tvOrderResult: TextView

    // Order History list to keep track of multiple orders
    private val orderHistoryList = mutableListOf<String>()

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
                fetchMarketQuote(symbol)
            } else {
                etSymbol.error = "Please enter a symbol"
            }
        }

        btnBuy.setOnClickListener {
            placeOrder("BUY")
        }

        btnSell.setOnClickListener {
            placeOrder("SELL")
        }
    }

    private fun checkServerStatus() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.getRootStatus()
                withContext(Dispatchers.Main) {
                    tvStatus.text = "Server Status: ${response.status} - ${response.message}"
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    tvStatus.text = "Server Error: Unable to connect"
                }
            }
        }
    }

    private fun fetchMarketQuote(symbol: String) {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val quote = ApiService.retrofitService.getBidAskQuote(symbol)
                withContext(Dispatchers.Main) {
                    tvQuoteDetails.text = """
                        Symbol: ${quote.symbol} (${quote.exchange})
                        -----------------------------------
                        Bid Price : ₹${quote.bidPrice}
                        Ask Price : ₹${quote.askPrice}
                        LTP       : ₹${quote.ltp}
                        Spread    : ₹${quote.spread}
                    """.trimIndent()
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    tvQuoteDetails.text = "Failed to fetch quote for $symbol: ${e.localizedMessage}"
                }
            }
        }
    }

    private fun placeOrder(transactionType: String) {
        val symbol = etSymbol.text.toString().trim().uppercase()
        val qtyStr = etQuantity.text.toString().trim()

        if (symbol.isEmpty()) {
            etSymbol.error = "Enter symbol first"
            return
        }
        if (qtyStr.isEmpty()) {
            etQuantity.error = "Enter quantity"
            return
        }

        val quantity = qtyStr.toIntOrNull() ?: 1
        val request = OrderRequest(symbol = symbol, quantity = quantity, transactionType = transactionType)

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.placeOrder(request)
                withContext(Dispatchers.Main) {
                    val orderDetail = "[$transactionType] $quantity x $symbol | ID: ${response.orderId} (${response.status})"
                    
                    // Add new order to the top of the history list
                    orderHistoryList.add(0, orderDetail)
                    
                    // Display all past orders
                    tvOrderResult.text = "--- Order History ---\n\n" + orderHistoryList.joinToString("\n\n")
                }
            } catch (e: Exception) {
                withContext(Dispatchers.Main) {
                    val errorDetail = "[FAILED] $transactionType $symbol: ${e.localizedMessage}"
                    orderHistoryList.add(0, errorDetail)
                    tvOrderResult.text = "--- Order History ---\n\n" + orderHistoryList.joinToString("\n\n")
                }
            }
        }
    }
}
