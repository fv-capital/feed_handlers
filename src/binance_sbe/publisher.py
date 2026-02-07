"""IPC publisher — serves decoded market data to strategy engines.

On Unix systems, creates an ``asyncio`` Unix‑domain‑socket server.
On Windows (where UDS is not supported by asyncio), falls back to a
TCP loopback server on a configurable port.

Decoded market‑data events are serialised into the compact binary IPC
protocol described in DESIGN.md §4 and broadcast to every connected
client.

Slow consumers that cannot keep up are dropped so that back‑pressure
never propagates to the feed handler's hot path.
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys
from pathlib import Path

import structlog

from binance_sbe.config import PublisherConfig
from binance_sbe.models import BestBidAsk, MsgType

log = structlog.get_logger(__name__)

# Heartbeat envelope (no payload)
_HEARTBEAT_MSG = struct.pack("<BH", MsgType.HEARTBEAT, 0)
_HEARTBEAT_INTERVAL = 5.0  # seconds

# Platform detection: asyncio UDS support
_HAS_UNIX_SOCKETS = hasattr(asyncio, "start_unix_server")


class Publisher:
    """Async IPC publisher for market data events.

    Uses Unix Domain Sockets when available, TCP loopback otherwise.
    """

    def __init__(self, config: PublisherConfig) -> None:
        self._config = config
        self._uds_path = config.uds_path
        self._tcp_port = config.tcp_port
        self._max_clients = config.max_clients
        self._write_timeout = config.write_timeout

        # Connected client transports
        self._clients: dict[asyncio.StreamWriter, str] = {}
        self._server: asyncio.AbstractServer | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._use_uds = _HAS_UNIX_SOCKETS

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Create the listener and start accepting connections."""
        if self._use_uds:
            await self._start_uds()
        else:
            await self._start_tcp()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _start_uds(self) -> None:
        sock_path = Path(self._uds_path)
        if sock_path.exists():
            os.unlink(sock_path)
        sock_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._on_client_connected,
            path=self._uds_path,
        )
        log.info("publisher.started", transport="uds", uds_path=self._uds_path)

    async def _start_tcp(self) -> None:
        self._server = await asyncio.start_server(
            self._on_client_connected,
            host="127.0.0.1",
            port=self._tcp_port,
        )
        # Resolve the actual port (useful when tcp_port=0 for ephemeral)
        addr = self._server.sockets[0].getsockname()
        self._tcp_port = addr[1]
        log.info("publisher.started", transport="tcp", port=self._tcp_port)

    async def stop(self) -> None:
        """Shut down the publisher and close all client connections."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Close all clients
        for writer in list(self._clients):
            await self._remove_client(writer)

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

        # Remove socket file (UDS only)
        if self._use_uds:
            sock_path = Path(self._uds_path)
            if sock_path.exists():
                os.unlink(sock_path)

        log.info("publisher.stopped")

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    async def _on_client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = repr(writer.get_extra_info("peername", "unknown"))
        if len(self._clients) >= self._max_clients:
            log.warning("publisher.max_clients_reached", peer=peer)
            writer.close()
            await writer.wait_closed()
            return

        self._clients[writer] = peer
        log.info("publisher.client_connected", peer=peer, total=len(self._clients))

        # Keep the handler alive until the client disconnects
        try:
            await reader.read()  # blocks until EOF
        except (ConnectionError, OSError):
            pass
        finally:
            await self._remove_client(writer)

    async def _remove_client(self, writer: asyncio.StreamWriter) -> None:
        peer = self._clients.pop(writer, "unknown")
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        log.info("publisher.client_disconnected", peer=peer, total=len(self._clients))

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, event: BestBidAsk) -> None:
        """Broadcast a market data event to all connected clients."""
        if not self._clients:
            return

        data = event.to_bytes()
        dead: list[asyncio.StreamWriter] = []

        for writer in list(self._clients):
            try:
                writer.write(data)
                await asyncio.wait_for(
                    writer.drain(),
                    timeout=self._write_timeout,
                )
            except (asyncio.TimeoutError, ConnectionError, OSError):
                log.warning(
                    "publisher.slow_consumer_dropped",
                    peer=self._clients.get(writer, "unknown"),
                )
                dead.append(writer)

        for writer in dead:
            await self._remove_client(writer)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats so clients can detect stale connections."""
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                await self._send_heartbeat()
        except asyncio.CancelledError:
            return

    async def _send_heartbeat(self) -> None:
        if not self._clients:
            return

        dead: list[asyncio.StreamWriter] = []
        for writer in list(self._clients):
            try:
                writer.write(_HEARTBEAT_MSG)
                await asyncio.wait_for(
                    writer.drain(),
                    timeout=self._write_timeout,
                )
            except (asyncio.TimeoutError, ConnectionError, OSError):
                dead.append(writer)

        for writer in dead:
            await self._remove_client(writer)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def tcp_port(self) -> int:
        """Return the TCP port (only meaningful when using TCP transport)."""
        return self._tcp_port
