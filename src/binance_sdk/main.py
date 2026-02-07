"""Main entry point for the Binance SDK WebSocket feed handler.

Uses ``binance-sdk-spot`` to subscribe to:
  - Individual Symbol Book Ticker Stream
  - Individual Symbol Rolling Window Statistics Stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

from .models import BookTickerEvent, RollingWindowTickerEvent
from .websocket_streams import BinanceWebSocketClient

# ──────────────────────────────────────────────────────────────────────
# Defaults
# ──────────────────────────────────────────────────────────────────────
WEBSOCKET_BASE_URL = "wss://stream.binance.com:9443"
DEFAULT_SYMBOLS = ["btcusdt"]
VALID_WINDOW_SIZES = ["1h", "4h", "1d"]
DEFAULT_WINDOW_SIZES = ["1h"]

# ──────────────────────────────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────────────────────────────

def _configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ──────────────────────────────────────────────────────────────────────
# Callbacks (print to console by default)
# ──────────────────────────────────────────────────────────────────────

async def on_book_ticker(event: BookTickerEvent) -> None:
    """Handle an Individual Symbol Book Ticker update."""
    print(json.dumps(asdict(event)))


async def on_rolling_window_ticker(event: RollingWindowTickerEvent) -> None:
    """Handle an Individual Symbol Rolling Window Statistics update."""
    print(json.dumps(asdict(event)))


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
) -> None:
    """Start the feed handler and run until interrupted."""
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
        logging.getLogger(__name__).info("Signal received — stopping …")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            signal.signal(sig, lambda s, f: _signal_handler())

    await client.start()

    try:
        await stop_event.wait()
    finally:
        await client.stop()


def main(config_path: str | None = None) -> None:
    """CLI entry point."""
    if config_path is None and len(sys.argv) > 1:
        config_path = sys.argv[1]

    raw = _load_config(config_path)
    binance_cfg = raw.get("binance", {})
    log_cfg = raw.get("logging", {})

    _configure_logging(log_cfg.get("level", "INFO"))

    symbols = binance_cfg.get("symbols", DEFAULT_SYMBOLS)
    window_sizes = binance_cfg.get("window_sizes", DEFAULT_WINDOW_SIZES)
    stream_url = binance_cfg.get("base_url", WEBSOCKET_BASE_URL)

    try:
        asyncio.run(run(symbols=symbols, window_sizes=window_sizes, stream_url=stream_url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

