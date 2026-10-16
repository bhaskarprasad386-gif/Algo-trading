"""Market-data package lifecycle hardening."""


def _install_transport_abort_fallback() -> None:
    from threading import current_thread

    from app.core.logger import app_logger
    from app.market_data.websocket import MarketDataWebSocket, _log_transport_thread_stack

    original_close = MarketDataWebSocket.close
    if getattr(MarketDataWebSocket, "_transport_abort_fallback_installed", False):
        return

    def close_with_transport_abort(self):
        with self._lock:
            sdk_socket = self.websocket
            transport_thread = self._transport_thread
            wsapp = getattr(sdk_socket, "wsapp", None) if sdk_socket is not None else None
            low_level_socket = getattr(wsapp, "sock", None) if wsapp is not None else None
            abort = getattr(low_level_socket, "abort", None)

        original_close(self)

        if (
            transport_thread is None
            or transport_thread is current_thread()
            or not transport_thread.is_alive()
            or not callable(abort)
        ):
            return

        try:
            abort()
            app_logger.warning("Angel WebSocket transport required low-level abort after graceful close timeout")
        except Exception as exc:
            app_logger.error("Angel WebSocket low-level transport abort failed: %s", exc)
            return

        transport_thread.join(timeout=1.0)
        still_alive = transport_thread.is_alive()
        with self._lock:
            self._transport_close_timed_out = still_alive
            if not still_alive:
                self._transport_thread = None
        if still_alive:
            app_logger.error("Angel WebSocket transport remained alive after low-level abort")
            _log_transport_thread_stack(transport_thread, phase="after-low-level-abort")
        else:
            app_logger.info("Angel WebSocket transport terminated after low-level abort")

    MarketDataWebSocket.close = close_with_transport_abort
    MarketDataWebSocket._transport_abort_fallback_installed = True


_install_transport_abort_fallback()
