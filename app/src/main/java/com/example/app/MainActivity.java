package com.example.app;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        TextView status = new TextView(this);
        status.setText("Algo Trading\nBackend connection layer ready");
        status.setTextSize(20);
        status.setPadding(32, 32, 32, 32);
        setContentView(status);
    }
}
