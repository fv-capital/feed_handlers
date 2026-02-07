"""Decode Binance JSON WebSocket stream messages into normalised models."""

from __future__ import annotations

import json
from typing import Any

import structlog

from binance_sbe.models import BestBidAsk

log = structlog.get_logger(__name__)

# Registry of stream decoders: stream_name -> decoder function
_DECODERS: dict[str, Any] = {}


def _register(stream_name: str):
    """Decorator to register a decoder for a stream type."""
    def wrapper(fn):
        _DECODERS[stream_name] = fn
        return fn
    return wrapper


# ------------------------------------------------------------------
# bookTicker decoder
# ------------------------------------------------------------------

@_register("bookTicker")
def _decode_book_ticker(data: dict[str, Any]) -> BestBidAsk:
    return BestBidAsk(
        stream_type="bookTicker",
        symbol=data.get("s", ""),
        order_book_update_id=data.get("u", 0),
        bid_price=float(data.get("b", 0)),
        bid_qty=float(data.get("B", 0)),
        ask_price=float(data.get("a", 0)),
        ask_qty=float(data.get("A", 0)),
    )


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def decode_json_message(raw: str) -> BestBidAsk | None:
    """Parse a combined-stream JSON message and return a normalised model.

    Combined stream format:
        {"stream": "btcusdt@bookTicker", "data": { ... }}

    Returns ``None`` if the stream type is unknown or the message is
    malformed.
    """
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("decoder.json_error", error=str(exc))
        return None

    # Combined stream wrapper
    stream_name: str = msg.get("stream", "")
    data: dict[str, Any] = msg.get("data", msg)  # fallback to root if no wrapper

    # Extract stream type from "btcusdt@bookTicker" -> "bookTicker"
    if "@" in stream_name:
        stream_type = stream_name.split("@", 1)[1]
    else:
        stream_type = stream_name

    decoder = _DECODERS.get(stream_type)
    if decoder is None:
        log.debug("decoder.unknown_stream", stream=stream_name)
        return None

    try:
        return decoder(data)
    except Exception as exc:
        log.warning("decoder.decode_error", stream=stream_name, error=str(exc))
        return None