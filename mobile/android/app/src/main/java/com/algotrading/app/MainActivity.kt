package com.algotrading.app

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        val textView = TextView(this).apply {
            text = "Algo Trading Mobile Client - Connected to Backend"
            textSize = 18f
            setPadding(40, 40, 40, 40)
        }
        
        setContentView(textView)
    }
}
