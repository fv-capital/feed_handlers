"""Main entry point — wires all components together and runs the event loop."""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

import structlog

from binance_sbe.config import AppConfig, load_config
from binance_sbe.connector import BinanceConnector
from binance_sbe.decoder import decode_json_message
from binance_sbe.models import BestBidAsk
from binance_sbe.publisher import Publisher

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(cfg: AppConfig) -> None:
    """Initialise ``structlog`` based on config."""
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if cfg.logging.format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            structlog.stdlib.NAME_TO_LEVEL.get(
                cfg.logging.level.lower(), 20,  # default INFO
            ),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class FeedHandlerApp:
    """Top‑level orchestrator for the Binance feed handler."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._publisher = Publisher(config.publisher)
        self._connector = BinanceConnector(
            binance_cfg=config.binance,
            conn_cfg=config.connection,
            on_text_frame=self._on_text_frame,
        )
        self._connector_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Text frame callback (hot path)
    # ------------------------------------------------------------------

    async def _on_text_frame(self, raw: str) -> None:
        """Called for every text WS frame from Binance."""
        event = decode_json_message(raw)
        if event is None:
            return

        if isinstance(event, BestBidAsk):
            await self._publisher.publish(event)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all components and block until shutdown."""
        await self._publisher.start()

        self._connector_task = asyncio.create_task(
            self._connector.run(),
            name="binance-connector",
        )

        log.info("app.running")

        try:
            await self._connector_task
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully shut down all components."""
        log.info("app.shutting_down")
        await self._connector.stop()
        if self._connector_task and not self._connector_task.done():
            self._connector_task.cancel()
            try:
                await self._connector_task
            except asyncio.CancelledError:
                pass
        await self._publisher.stop()
        log.info("app.stopped")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main(config_path: str | None = None) -> None:
    """CLI entry point."""
    if config_path is None and len(sys.argv) > 1:
        config_path = sys.argv[1]

    config = load_config(config_path)
    _configure_logging(config)

    app = FeedHandlerApp(config)

    loop = asyncio.new_event_loop()

    def _signal_handler() -> None:
        log.info("app.signal_received")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: _signal_handler())

    try:
        loop.run_until_complete(app.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        loop.run_until_complete(app.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()