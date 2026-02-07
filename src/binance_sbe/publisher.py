"""UDS publisher — sends normalised events to connected clients."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from typing import Any

import structlog

from binance_sbe.config import PublisherConfig

log = structlog.get_logger(__name__)


class Publisher:
    """Publishes events over a Unix Domain Socket (UDS).

    Each message is framed as: [4-byte big-endian length][JSON payload].
    """

    def __init__(self, config: PublisherConfig) -> None:
        self._uds_path = config.uds_path
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._clients.add(writer)
        log.info("publisher.client_connected", total=len(self._clients))
        try:
            # Keep connection open until client disconnects
            await reader.read()
        except Exception:
            pass
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            log.info("publisher.client_disconnected", total=len(self._clients))

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, event: Any) -> None:
        """Serialise and send an event to all connected clients."""
        if not self._clients:
            return

        payload = json.dumps(asdict(event)).encode()
        frame = len(payload).to_bytes(4, "big") + payload

        dead: list[asyncio.StreamWriter] = []
        for writer in self._clients:
            try:
                writer.write(frame)
                await writer.drain()
            except Exception:
                dead.append(writer)

        for w in dead:
            self._clients.discard(w)
            try:
                w.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the UDS server."""
        # Remove stale socket file
        if os.path.exists(self._uds_path):
            os.unlink(self._uds_path)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self._uds_path,
        )
        log.info(
            "publisher.started",
            transport="uds",
            uds_path=self._uds_path,
        )

    async def stop(self) -> None:
        """Stop the UDS server and disconnect all clients."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

        for writer in list(self._clients):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()

        if os.path.exists(self._uds_path):
            os.unlink(self._uds_path)

        log.info("publisher.stopped")