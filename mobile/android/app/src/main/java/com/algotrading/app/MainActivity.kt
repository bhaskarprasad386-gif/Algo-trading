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
    private lateinit var etEntryPrice: EditText
    private lateinit var etQuantity: EditText
    private lateinit var etExitPrice: EditText
    private lateinit var tvPaperResult: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        tvStatus = findViewById(R.id.tvStatus)
        etEntryPrice = findViewById(R.id.etEntryPrice)
        etQuantity = findViewById(R.id.etQuantity)
        etExitPrice = findViewById(R.id.etExitPrice)
        tvPaperResult = findViewById(R.id.tvPaperResult)

        checkServerStatus()

        findViewById<Button>(R.id.btnPaperEntry).setOnClickListener { paperEntry() }
        findViewById<Button>(R.id.btnPaperExit).setOnClickListener { paperExit() }
        findViewById<Button>(R.id.btnPaperPosition).setOnClickListener { paperPosition() }
    }

    private fun checkServerStatus() = CoroutineScope(Dispatchers.IO).launch {
        try {
            val response = ApiService.retrofitService.getRootStatus()
            withContext(Dispatchers.Main) {
                tvStatus.text = "Server Status: ${response.status}"
            }
        } catch (_: Exception) {
            withContext(Dispatchers.Main) {
                tvStatus.text = "Server Error: Offline"
            }
        }
    }

    private fun paperEntry() {
        val price = etEntryPrice.text.toString().toDoubleOrNull()
        val quantity = etQuantity.text.toString().toDoubleOrNull()
        if (price == null || price <= 0) {
            etEntryPrice.error = "Enter a positive entry price"
            return
        }
        if (quantity == null || quantity <= 0) {
            etQuantity.error = "Enter a positive quantity"
            return
        }

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.paperEntry(
                    PaperEntryRequest(price = price, quantity = quantity)
                )
                withContext(Dispatchers.Main) {
                    tvPaperResult.text = "PAPER POSITION ACTIVE\n\nEntry: ₹${response.entry_price}\nStop Loss: ₹${response.stop_loss}\nTarget: ₹${response.target}\nQuantity: ${response.position.quantity}"
                }
            } catch (error: Exception) {
                withContext(Dispatchers.Main) {
                    tvPaperResult.text = "Paper Entry Failed: ${error.message ?: "API error"}"
                }
            }
        }
    }

    private fun paperPosition() = CoroutineScope(Dispatchers.IO).launch {
        try {
            val response = ApiService.retrofitService.paperPosition()
            withContext(Dispatchers.Main) {
                val position = response.position
                tvPaperResult.text = if (position == null) {
                    "PAPER POSITION: FLAT"
                } else {
                    "PAPER POSITION ACTIVE\n\nEntry: ₹${position.entry_price}\nStop Loss: ₹${position.stop_loss}\nTarget: ₹${position.target}\nQuantity: ${position.quantity}"
                }
            }
        } catch (error: Exception) {
            withContext(Dispatchers.Main) {
                tvPaperResult.text = "Position Check Failed: ${error.message ?: "API error"}"
            }
        }
    }

    private fun paperExit() {
        val price = etExitPrice.text.toString().toDoubleOrNull()
        if (price == null || price <= 0) {
            etExitPrice.error = "Enter a positive exit price"
            return
        }

        CoroutineScope(Dispatchers.IO).launch {
            try {
                val response = ApiService.retrofitService.paperExit(PaperExitRequest(price))
                withContext(Dispatchers.Main) {
                    tvPaperResult.text = if (response.status == "closed") {
                        "PAPER POSITION CLOSED\n\nEntry: ₹${response.entry_price}\nExit: ₹${response.exit_price}\nQuantity: ${response.quantity}\nP&L: ₹${response.pnl}"
                    } else {
                        "PAPER POSITION: FLAT"
                    }
                }
            } catch (error: Exception) {
                withContext(Dispatchers.Main) {
                    tvPaperResult.text = "Paper Exit Failed: ${error.message ?: "API error"}"
                }
            }
        }
    }
}
