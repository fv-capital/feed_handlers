"""WebSocket connector — manages the connection to Binance SBE streams.

Handles:
    • WSS connection with Ed25519 API key authentication
    • JSON subscription management (text frames)
    • Binary frame dispatch to the SBE decoder
    • Automatic ping/pong keepalive (handled by the ``websockets`` library)
    • Reconnection with exponential back‑off
    • Preemptive reconnect before the 24‑hour limit
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Awaitable

import structlog
import websockets
from websockets.asyncio.client import ClientConnection

from binance_sbe.config import BinanceConfig, ConnectionConfig

log = structlog.get_logger(__name__)

# Type alias for the callback that receives raw binary frames
FrameCallback = Callable[[bytes], Awaitable[None]]


class BinanceConnector:
    """Async WebSocket connector for Binance SBE market data streams."""

    def __init__(
        self,
        binance_cfg: BinanceConfig,
        conn_cfg: ConnectionConfig,
        on_binary_frame: FrameCallback,
    ) -> None:
        self._binance_cfg = binance_cfg
        self._conn_cfg = conn_cfg
        self._on_binary_frame = on_binary_frame

        self._ws: ClientConnection | None = None
        self._running = False
        self._connect_time: float = 0.0

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _build_url(self) -> str:
        """Build the combined‑stream URL for all configured symbols × streams."""
        stream_names: list[str] = []
        for symbol in self._binance_cfg.symbols:
            for stream in self._binance_cfg.streams:
                stream_names.append(f"{symbol}@{stream}")

        base = self._binance_cfg.ws_base_url.rstrip("/")
        if len(stream_names) == 1:
            return f"{base}/ws/{stream_names[0]}"
        return f"{base}/stream?streams={'/'.join(stream_names)}"

    def _extra_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._binance_cfg.api_key:
            headers["X-MBX-APIKEY"] = self._binance_cfg.api_key
        return headers

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop — connect, receive, reconnect on failure."""
        self._running = True
        delay = self._conn_cfg.reconnect_delay_initial
        attempts = 0

        while self._running:
            try:
                await self._connect_and_listen()
                # If we get here, the connection was closed cleanly
                # (e.g. preemptive reconnect).  Reset backoff.
                delay = self._conn_cfg.reconnect_delay_initial
                attempts = 0
            except (
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                OSError,
            ) as exc:
                attempts += 1
                max_att = self._conn_cfg.max_reconnect_attempts
                if max_att > 0 and attempts > max_att:
                    log.error(
                        "connector.max_reconnects_exceeded",
                        attempts=attempts,
                    )
                    raise
                log.warning(
                    "connector.disconnected",
                    error=str(exc),
                    reconnect_in=delay,
                    attempt=attempts,
                )
                await asyncio.sleep(delay)
                delay = min(
                    delay * self._conn_cfg.reconnect_delay_multiplier,
                    self._conn_cfg.reconnect_delay_max,
                )
            except asyncio.CancelledError:
                break

        await self._close()

    async def stop(self) -> None:
        """Signal the connector to shut down."""
        self._running = False
        await self._close()

    async def _close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ------------------------------------------------------------------
    # Core receive loop
    # ------------------------------------------------------------------

    async def _connect_and_listen(self) -> None:
        url = self._build_url()
        headers = self._extra_headers()
        log.info("connector.connecting", url=url)

        async with websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=None,   # Binance sends pings; library auto‑pongs
            max_size=2**20,       # 1 MiB max frame
        ) as ws:
            self._ws = ws
            self._connect_time = time.monotonic()
            log.info("connector.connected")

            # Subscribe via JSON (for combined streams opened at /stream?...)
            await self._subscribe(ws)

            # Receive loop
            preempt_seconds = self._conn_cfg.preemptive_reconnect_hours * 3600

            async for message in ws:
                # Check preemptive reconnect timer
                elapsed = time.monotonic() - self._connect_time
                if elapsed >= preempt_seconds:
                    log.info(
                        "connector.preemptive_reconnect",
                        elapsed_hours=elapsed / 3600,
                    )
                    break  # exit cleanly → outer loop reconnects

                if isinstance(message, bytes):
                    await self._on_binary_frame(message)
                elif isinstance(message, str):
                    self._handle_text_frame(message)

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    async def _subscribe(self, ws: ClientConnection) -> None:
        """Send a SUBSCRIBE request for all configured symbol+stream pairs."""
        params: list[str] = []
        for symbol in self._binance_cfg.symbols:
            for stream in self._binance_cfg.streams:
                params.append(f"{symbol}@{stream}")

        if not params:
            return

        request = {"method": "SUBSCRIBE", "params": params, "id": 1}
        await ws.send(json.dumps(request))
        log.info("connector.subscribed", streams=params)

    @staticmethod
    def _handle_text_frame(text: str) -> None:
        """Process a JSON text frame (subscription response)."""
        try:
            data = json.loads(text)
            log.info("connector.text_frame", data=data)
        except json.JSONDecodeError:
            log.warning("connector.bad_json", text=text[:200])
