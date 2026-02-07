"""WebSocket stream client using the official Binance Spot SDK (binance-sdk-spot).

Subscribes to:
  - Individual Symbol Book Ticker Stream   (<symbol>@bookTicker)
  - Individual Symbol Rolling Window Statistics Stream (<symbol>@ticker_<window>)

Endpoint: wss://stream.binance.com:9443
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Callable, Awaitable

from binance_sdk_spot.spot import (
    Spot,
    SPOT_WS_STREAMS_PROD_URL,
    ConfigurationWebSocketStreams,
)
from binance_sdk_spot.websocket_streams.models import RollingWindowTickerWindowSizeEnum

from .models import BookTickerEvent, RollingWindowTickerEvent

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Stream URL
# ──────────────────────────────────────────────────────────────────────
STREAM_URL = "wss://stream.binance.com:9443"


# ──────────────────────────────────────────────────────────────────────
# Helpers to convert SDK Pydantic models → dataclasses
# ──────────────────────────────────────────────────────────────────────

def _parse_book_ticker(data) -> BookTickerEvent:
    """Convert an SDK ``BookTickerResponse`` model into a ``BookTickerEvent``.

    The SDK delivers parsed Pydantic models with single-letter attributes
    (u, s, b, B, a, A) — accessed via ``getattr``.
    """
    return BookTickerEvent(
        update_id=getattr(data, "u", 0) or 0,
        symbol=getattr(data, "s", "") or "",
        bid_price=getattr(data, "b", "") or "",
        bid_qty=getattr(data, "B", "") or "",
        ask_price=getattr(data, "a", "") or "",
        ask_qty=getattr(data, "A", "") or "",
    )


def _parse_rolling_window_ticker(data) -> RollingWindowTickerEvent:
    """Convert an SDK ``RollingWindowTickerResponse`` model into a ``RollingWindowTickerEvent``.

    The SDK delivers parsed Pydantic models with single-letter attributes.
    """
    return RollingWindowTickerEvent(
        event_type=getattr(data, "e", "") or "",
        event_time=getattr(data, "E", 0) or 0,
        symbol=getattr(data, "s", "") or "",
        price_change=getattr(data, "p", "") or "",
        price_change_percent=getattr(data, "P", "") or "",
        open_price=getattr(data, "o", "") or "",
        high_price=getattr(data, "h", "") or "",
        low_price=getattr(data, "l", "") or "",
        close_price=getattr(data, "c", "") or "",
        weighted_avg_price=getattr(data, "w", "") or "",
        total_traded_base_volume=getattr(data, "v", "") or "",
        total_traded_quote_volume=getattr(data, "q", "") or "",
        statistics_open_time=getattr(data, "O", 0) or 0,
        statistics_close_time=getattr(data, "C", 0) or 0,
        first_trade_id=getattr(data, "F", 0) or 0,
        last_trade_id=getattr(data, "L", 0) or 0,
        total_num_trades=getattr(data, "n", 0) or 0,
    )


# ──────────────────────────────────────────────────────────────────────
# Main client
# ──────────────────────────────────────────────────────────────────────

class BinanceWebSocketClient:
    """Manages WebSocket stream subscriptions using ``binance-sdk-spot``.

    Parameters
    ----------
    symbols : list[str]
        Symbols to subscribe to (e.g. ``["btcusdt", "ethusdt"]``).
    window_sizes : list[str]
        Rolling window sizes for the ticker stream.
        Valid: ``"1h"``, ``"4h"``, ``"1d"``.  Defaults to ``["1h"]``.
    stream_url : str
        WebSocket stream base URL.  Defaults to ``wss://stream.binance.com:9443``.
    on_book_ticker : callable, optional
        ``async def callback(event: BookTickerEvent) -> None``
    on_rolling_window_ticker : callable, optional
        ``async def callback(event: RollingWindowTickerEvent) -> None``
    """

    # Map user-friendly window size strings → SDK enum values
    _WINDOW_SIZE_MAP: dict[str, str] = {
        "1h": RollingWindowTickerWindowSizeEnum.WINDOW_SIZE_1h.value,
        "4h": RollingWindowTickerWindowSizeEnum.WINDOW_SIZE_4h.value,
        "1d": RollingWindowTickerWindowSizeEnum.WINDOW_SIZE_1d.value,
    }

    def __init__(
        self,
        symbols: list[str],
        window_sizes: list[str] | None = None,
        stream_url: str = STREAM_URL,
        on_book_ticker: Callable[[BookTickerEvent], Awaitable[None]] | None = None,
        on_rolling_window_ticker: Callable[[RollingWindowTickerEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._symbols = [s.lower() for s in symbols]
        self._window_sizes = window_sizes or ["1h"]
        self._stream_url = stream_url
        self._on_book_ticker = on_book_ticker
        self._on_rolling_window_ticker = on_rolling_window_ticker

        # SDK client
        config = ConfigurationWebSocketStreams(stream_url=self._stream_url)
        self._client = Spot(config_ws_streams=config)

        # Track active stream handles for cleanup
        self._streams: list = []
        self._connection = None

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    async def start(self) -> None:
        """Connect and subscribe to all configured streams."""
        log.info("Connecting to Binance WebSocket Streams at %s", self._stream_url)

        self._connection = await self._client.websocket_streams.create_connection()
        log.info("WebSocket connection established")

        # Subscribe to Individual Symbol Book Ticker for each symbol
        for symbol in self._symbols:
            stream = await self._connection.book_ticker(symbol=symbol)
            stream.on("message", self._make_book_ticker_handler(symbol))
            self._streams.append(stream)
            log.info("Subscribed to %s@bookTicker", symbol)

        # Subscribe to Individual Symbol Rolling Window Statistics for each symbol + window
        for symbol in self._symbols:
            for window in self._window_sizes:
                enum_val = self._WINDOW_SIZE_MAP.get(window)
                if enum_val is None:
                    log.warning(
                        "Invalid window_size '%s' — skipping (valid: %s)",
                        window,
                        list(self._WINDOW_SIZE_MAP.keys()),
                    )
                    continue

                stream = await self._connection.rolling_window_ticker(
                    symbol=symbol,
                    window_size=enum_val,
                )
                stream.on("message", self._make_rolling_window_handler(symbol, window))
                self._streams.append(stream)
                log.info("Subscribed to %s@ticker_%s", symbol, window)

        log.info(
            "All subscriptions active — %d stream(s) for %d symbol(s)",
            len(self._streams),
            len(self._symbols),
        )

    async def stop(self) -> None:
        """Unsubscribe from all streams and close the connection."""
        log.info("Shutting down WebSocket streams …")
        for stream in self._streams:
            try:
                await stream.unsubscribe()
            except Exception as exc:
                log.warning("Error unsubscribing stream: %s", exc)
        self._streams.clear()

        if self._connection is not None:
            try:
                await self._connection.close_connection(close_session=True)
            except Exception as exc:
                log.warning("Error closing connection: %s", exc)
            self._connection = None

        log.info("WebSocket streams stopped")

    # ----------------------------------------------------------------
    # Internal handlers
    # ----------------------------------------------------------------

    def _make_book_ticker_handler(self, symbol: str):
        """Return a closure that processes book ticker messages."""

        def handler(data):
            event = _parse_book_ticker(data)
            log.debug("bookTicker %s: bid=%s ask=%s", symbol, event.bid_price, event.ask_price)
            if self._on_book_ticker is not None:
                # Schedule the async callback
                asyncio.ensure_future(self._on_book_ticker(event))

        return handler

    def _make_rolling_window_handler(self, symbol: str, window: str):
        """Return a closure that processes rolling window ticker messages."""

        def handler(data):
            event = _parse_rolling_window_ticker(data)
            log.debug(
                "ticker_%s %s: close=%s volume=%s",
                window,
                symbol,
                event.close_price,
                event.total_traded_base_volume,
            )
            if self._on_rolling_window_ticker is not None:
                asyncio.ensure_future(self._on_rolling_window_ticker(event))

        return handler
