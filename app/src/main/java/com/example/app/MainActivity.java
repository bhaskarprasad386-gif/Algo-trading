package com.example.app;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    // Change this to the reachable backend address when running on a physical phone.
    // Android emulator can use http://10.0.2.2:8000/health for a host-machine backend.
    private static final String BACKEND_HEALTH_URL = "http://10.0.2.2:8000/health";

    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TextView status;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        status = new TextView(this);
        status.setText("Algo Trading\nChecking backend connection...");
        status.setTextSize(20);
        status.setPadding(32, 32, 32, 32);
        setContentView(status);

        checkBackend();
    }

    private void checkBackend() {
        executor.execute(() -> {
            HttpURLConnection connection = null;
            String result;
            try {
                URL url = new URL(BACKEND_HEALTH_URL);
                connection = (HttpURLConnection) url.openConnection();
                connection.setRequestMethod("GET");
                connection.setConnectTimeout(5000);
                connection.setReadTimeout(5000);
                connection.setUseCaches(false);

                int code = connection.getResponseCode();
                InputStream stream = code >= 200 && code < 400
                        ? connection.getInputStream()
                        : connection.getErrorStream();

                String body = readBody(stream);
                if (code >= 200 && code < 300) {
                    result = "Algo Trading\nBackend: CONNECTED\n" + body;
                } else {
                    result = "Algo Trading\nBackend: HTTP " + code + "\n" + body;
                }
            } catch (Exception e) {
                result = "Algo Trading\nBackend: NOT CONNECTED\n" + e.getClass().getSimpleName();
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }

            final String message = result;
            runOnUiThread(() -> status.setText(message));
        });
    }

    private String readBody(InputStream stream) throws Exception {
        if (stream == null) {
            return "";
        }
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            StringBuilder body = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                body.append(line);
            }
            return body.toString();
        }
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }
}
