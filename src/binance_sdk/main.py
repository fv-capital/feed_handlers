"""Main entry point for the Binance SDK WebSocket feed handler.

Uses ``binance-sdk-spot`` to subscribe to:
  - Individual Symbol Book Ticker Stream
  - Individual Symbol Rolling Window Statistics Stream
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml

from binance_sbe.config import PublisherConfig
from binance_sbe.publisher import Publisher

from .models import BookTickerEvent, RollingWindowTickerEvent
from .websocket_streams import BinanceWebSocketClient

# ──────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────
WEBSOCKET_BASE_URL = "wss://stream.binance.com:9443"
DEFAULT_SYMBOLS = ["btcusdt"]
VALID_WINDOW_SIZES = ["1h", "4h", "1d"]
DEFAULT_WINDOW_SIZES = ["1h"]
DEFAULT_BOOK_TICKER_UDS_PATH = "/tmp/binance_book_ticker.sock"
DEFAULT_ROLLING_TICKER_UDS_PATH = "/tmp/binance_rolling_ticker.sock"

LOG_FILE = Path(__file__).resolve().parents[2] / "binance_sdk.log"

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────────────────────────────

def _configure_logging(level: str = "INFO") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%dT%H:%M:%S"

    logging.basicConfig(
        level=log_level,
        format=fmt,
        datefmt=datefmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_FILE),
        ],
    )


# ──────────────────────────────────────────────────────────────────────
# Config loader
# ──────────────────────────────────────────────────────────────────────

def _load_config(path: str | None = None) -> dict:
    """Load YAML config; return empty dict on failure."""
    if path is None:
        path = str(Path(__file__).resolve().parents[2] / "config" / "config.yaml")
    p = Path(path)
    if not p.exists():
        return {}
    with open(p) as fh:
        return yaml.safe_load(fh) or {}


# ──────────────────────────────────────────────────────────────────────
# Application
# ──────────────────────────────────────────────────────────────────────

async def run(
    symbols: list[str] | None = None,
    window_sizes: list[str] | None = None,
    stream_url: str = WEBSOCKET_BASE_URL,
    book_ticker_uds_path: str = DEFAULT_BOOK_TICKER_UDS_PATH,
    rolling_ticker_uds_path: str = DEFAULT_ROLLING_TICKER_UDS_PATH,
) -> None:
    """Start the feed handler and run until interrupted."""
    book_ticker_publisher = Publisher(PublisherConfig(uds_path=book_ticker_uds_path))
    rolling_ticker_publisher = Publisher(PublisherConfig(uds_path=rolling_ticker_uds_path))

    async def on_book_ticker(event: BookTickerEvent) -> None:
        await book_ticker_publisher.publish(event)

    async def on_rolling_window_ticker(event: RollingWindowTickerEvent) -> None:
        await rolling_ticker_publisher.publish(event)

    client = BinanceWebSocketClient(
        symbols=symbols or DEFAULT_SYMBOLS,
        window_sizes=window_sizes or DEFAULT_WINDOW_SIZES,
        stream_url=stream_url,
        on_book_ticker=on_book_ticker,
        on_rolling_window_ticker=on_rolling_window_ticker,
    )

    loop = asyncio.get_running_loop()

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("Signal received — stopping …")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            signal.signal(sig, lambda s, f: _signal_handler())

    await book_ticker_publisher.start()
    log.info("Book ticker UDS publisher started on %s", book_ticker_uds_path)
    await rolling_ticker_publisher.start()
    log.info("Rolling ticker UDS publisher started on %s", rolling_ticker_uds_path)

    await client.start()

    try:
        await stop_event.wait()
    finally:
        await client.stop()
        await rolling_ticker_publisher.stop()
        await book_ticker_publisher.stop()


def main(config_path: str | None = None) -> None:
    """CLI entry point."""
    if config_path is None and len(sys.argv) > 1:
        config_path = sys.argv[1]

    raw = _load_config(config_path)
    binance_cfg = raw.get("binance", {})
    publisher_cfg = raw.get("publisher", {})
    log_cfg = raw.get("logging", {})

    _configure_logging(log_cfg.get("level", "INFO"))

    symbols = binance_cfg.get("symbols", DEFAULT_SYMBOLS)
    window_sizes = binance_cfg.get("window_sizes", DEFAULT_WINDOW_SIZES)
    stream_url = binance_cfg.get("base_url", WEBSOCKET_BASE_URL)
    book_ticker_uds = publisher_cfg.get("book_ticker_uds_path", DEFAULT_BOOK_TICKER_UDS_PATH)
    rolling_ticker_uds = publisher_cfg.get("rolling_ticker_uds_path", DEFAULT_ROLLING_TICKER_UDS_PATH)

    try:
        asyncio.run(run(
            symbols=symbols,
            window_sizes=window_sizes,
            stream_url=stream_url,
            book_ticker_uds_path=book_ticker_uds,
            rolling_ticker_uds_path=rolling_ticker_uds,
        ))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

