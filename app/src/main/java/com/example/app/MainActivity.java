package com.example.app;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity {
    // Android emulator -> backend running on the host machine.
    // For a physical phone, replace 10.0.2.2 with the backend machine's reachable LAN address.
    private static final String BACKEND_WS_BASE = "ws://10.0.2.2:8000/ws/market-data/";

    private final OkHttpClient httpClient = new OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build();

    private WebSocket webSocket;
    private TextView status;
    private EditText symbolInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(32, 32, 32, 32);

        TextView title = new TextView(this);
        title.setText("Algo Trading\nLive Market Connection");
        title.setTextSize(20);
        root.addView(title);

        symbolInput = new EditText(this);
        symbolInput.setHint("NSE symbol, e.g. SBIN-EQ");
        symbolInput.setText("SBIN-EQ");
        root.addView(symbolInput);

        Button connectButton = new Button(this);
        connectButton.setText("Connect Live Market");
        root.addView(connectButton);

        status = new TextView(this);
        status.setText("Backend: ready");
        root.addView(status);

        setContentView(root);

        connectButton.setOnClickListener(v -> connectMarket());
    }

    private void connectMarket() {
        closeSocket();

        String symbol = symbolInput.getText().toString().trim().toUpperCase();
        if (symbol.isEmpty()) {
            status.setText("Enter an NSE symbol");
            return;
        }

        status.setText("Connecting: " + symbol);
        Request request = new Request.Builder()
                .url(BACKEND_WS_BASE + symbol)
                .build();

        webSocket = httpClient.newWebSocket(request, new WebSocketListener() {
            @Override
            public void onOpen(WebSocket socket, Response response) {
                runOnUiThread(() -> status.setText("Market WebSocket: CONNECTED\n" + symbol));
            }

            @Override
            public void onMessage(WebSocket socket, String text) {
                runOnUiThread(() -> status.setText("Live tick:\n" + text));
            }

            @Override
            public void onClosing(WebSocket socket, int code, String reason) {
                socket.close(code, reason);
                runOnUiThread(() -> status.setText("Market WebSocket: closing"));
            }

            @Override
            public void onClosed(WebSocket socket, int code, String reason) {
                runOnUiThread(() -> status.setText("Market WebSocket: disconnected"));
            }

            @Override
            public void onFailure(WebSocket socket, Throwable t, Response response) {
                runOnUiThread(() -> status.setText(
                        "Market WebSocket: ERROR\n" + t.getClass().getSimpleName()));
            }
        });
    }

    private void closeSocket() {
        if (webSocket != null) {
            webSocket.close(1000, "client reconnect");
            webSocket = null;
        }
    }

    @Override
    protected void onDestroy() {
        closeSocket();
        httpClient.dispatcher().executorService().shutdown();
        httpClient.connectionPool().evictAll();
        super.onDestroy();
    }
}
