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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvStatus = findViewById(R.id.tvStatus)
        etSymbol = findViewById(R.id.etSymbol)
        tvQuoteDetails = findViewById(R.id.tvQuoteDetails)
        btnFetchQuote = findViewById(R.id.btnFetchQuote)

        // Check Backend Connection on Startup
        checkServerStatus()

        // Fetch Bid, Ask and Spread based on user input
        btnFetchQuote.setOnClickListener {
            val symbol = etSymbol.text.toString().trim().uppercase()
            if (symbol.isNotEmpty()) {
                fetchMarketQuote(symbol)
            } else {
                etSymbol.error = "Please enter a valid symbol"
            }
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
}
