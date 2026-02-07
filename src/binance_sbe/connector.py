"""WebSocket connector for Binance public JSON streams."""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import structlog
import websockets

from binance_sbe.config import BinanceConfig, ConnectionConfig

log = structlog.get_logger(__name__)


class BinanceConnector:
    """Connects to Binance WebSocket Streams and dispatches text frames."""

    def __init__(
        self,
        binance_cfg: BinanceConfig,
        conn_cfg: ConnectionConfig,
        on_text_frame: Callable[[str], Awaitable[None]],
    ) -> None:
        self._binance_cfg = binance_cfg
        self._conn_cfg = conn_cfg
        self._on_text_frame = on_text_frame
        self._running = True
        self._ws: websockets.WebSocketClientProtocol | None = None

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    def _build_url(self) -> str:
        """Build the combined stream URL.

        Format: wss://stream.binance.com:9443/stream?streams=<s1>/<s2>/...
        Each stream is ``<symbol>@<streamName>``.
        """
        parts: list[str] = []
        for symbol in self._binance_cfg.symbols:
            for stream in self._binance_cfg.streams:
                parts.append(f"{symbol.lower()}@{stream}")
        combined = "/".join(parts)
        return f"{self._binance_cfg.base_url}/stream?streams={combined}"

    # ------------------------------------------------------------------
    # Connection loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect with exponential back-off and dispatch frames."""
        attempt = 0

        while self._running:
            url = self._build_url()
            log.info("connector.connecting", url=url)
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    attempt = 0
                    log.info("connector.connected")

                    connect_time = time.monotonic()
                    reconnect_secs = self._conn_cfg.preemptive_reconnect_hours * 3600

                    async for message in ws:
                        # Binance JSON streams send text frames
                        if isinstance(message, str):
                            await self._on_text_frame(message)
                        else:
                            # Binary frame — log and skip for now
                            log.debug("connector.binary_frame_skipped", size=len(message))

                        # Preemptive reconnect before 24 h limit
                        if time.monotonic() - connect_time > reconnect_secs:
                            log.info("connector.preemptive_reconnect")
                            break

            except asyncio.CancelledError:
                log.info("connector.cancelled")
                return
            except Exception as exc:
                attempt += 1
                if attempt > self._conn_cfg.max_retries:
                    log.error("connector.max_retries_exceeded", attempts=attempt)
                    return

                backoff = min(
                    self._conn_cfg.base_backoff_seconds * (2 ** (attempt - 1)),
                    self._conn_cfg.max_backoff_seconds,
                )
                log.warning(
                    "connector.disconnected",
                    error=str(exc),
                    attempt=attempt,
                    reconnect_in=backoff,
                )
                await asyncio.sleep(backoff)
            finally:
                self._ws = None

    async def stop(self) -> None:
        """Gracefully close the WebSocket."""
        self._running = False
        if self._ws is not None:
            await self._ws.close()