"""SBE binary decoder for Binance market data stream frames.

Decodes raw binary WebSocket frames according to the SBE schema
``stream_1_0.xml`` from the Binance spot API docs.  Each decoder is
registered by *template ID* so new stream types can be added with a
single function.

Reference schema:
    https://github.com/binance/binance-spot-api-docs/blob/master/sbe/schemas/stream_1_0.xml
"""

from __future__ import annotations

import struct
from typing import Callable

import structlog

from binance_sbe.models import BestBidAsk

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants from the SBE schema
# ---------------------------------------------------------------------------

SCHEMA_ID = 1
SCHEMA_VERSION = 0

# Message header: blockLength(H) templateId(H) schemaId(H) version(H)
_HEADER_FMT = "<HHHH"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 8 bytes

# Template IDs
TEMPLATE_BEST_BID_ASK = 10001
TEMPLATE_TRADES = 10000         # future
TEMPLATE_DEPTH_SNAPSHOT = 10002 # future
TEMPLATE_DEPTH_DIFF = 10003     # future

# BestBidAskStreamEvent body layout (after header):
#   eventTime(q) bookUpdateId(q) priceExponent(b) qtyExponent(b)
#   bidPrice(q) bidQty(q) askPrice(q) askQty(q)
_BBA_BODY_FMT = "<qqbbqqqq"
_BBA_BODY_SIZE = struct.calcsize(_BBA_BODY_FMT)  # 50 bytes

# ---------------------------------------------------------------------------
# Decoder registry
# ---------------------------------------------------------------------------

# Maps template_id -> callable(buffer, offset, block_length) -> model | None
_DecoderFn = Callable[[bytes, int, int], object | None]
_decoders: dict[int, _DecoderFn] = {}


def register_decoder(template_id: int):
    """Decorator to register a decoder function for a given template ID."""
    def wrapper(fn: _DecoderFn) -> _DecoderFn:
        _decoders[template_id] = fn
        return fn
    return wrapper


# ---------------------------------------------------------------------------
# BestBidAskStreamEvent decoder (template 10001)
# ---------------------------------------------------------------------------

@register_decoder(TEMPLATE_BEST_BID_ASK)
def _decode_best_bid_ask(buf: bytes, offset: int, block_length: int) -> BestBidAsk | None:
    """Decode a BestBidAskStreamEvent from *buf* starting at *offset*.

    Layout (after the 8‑byte header):
        eventTime      int64   8B
        bookUpdateId   int64   8B
        priceExponent  int8    1B
        qtyExponent    int8    1B
        bidPrice       int64   8B   (mantissa)
        bidQty         int64   8B   (mantissa)
        askPrice       int64   8B   (mantissa)
        askQty         int64   8B   (mantissa)
                                    ──────────
                               total 50 bytes fixed body

    Followed by a varString8 *symbol*:
        length  uint8  1B
        data    UTF‑8  <length> bytes
    """
    if len(buf) < offset + _BBA_BODY_SIZE:
        log.warning("best_bid_ask.truncated", buf_len=len(buf), offset=offset)
        return None

    (
        event_time,
        book_update_id,
        price_exp,
        qty_exp,
        bid_price_mantissa,
        bid_qty_mantissa,
        ask_price_mantissa,
        ask_qty_mantissa,
    ) = struct.unpack_from(_BBA_BODY_FMT, buf, offset)

    # The symbol var‑string sits after the fixed block.
    # block_length is the root block size declared in the header; the
    # var‑data comes right after that.
    var_offset = offset + block_length
    if var_offset >= len(buf):
        log.warning("best_bid_ask.no_symbol", var_offset=var_offset, buf_len=len(buf))
        return None

    sym_len = buf[var_offset]
    symbol = buf[var_offset + 1: var_offset + 1 + sym_len].decode("utf-8")

    # Convert mantissa + exponent → float
    price_mult = 10.0 ** price_exp
    qty_mult = 10.0 ** qty_exp

    return BestBidAsk(
        event_time=event_time,
        update_id=book_update_id,
        bid_price=bid_price_mantissa * price_mult,
        bid_qty=bid_qty_mantissa * qty_mult,
        ask_price=ask_price_mantissa * price_mult,
        ask_qty=ask_qty_mantissa * qty_mult,
        symbol=symbol,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decode(frame: bytes) -> object | None:
    """Decode a binary SBE frame and return the appropriate model instance.

    Returns ``None`` if the frame cannot be decoded (unknown template,
    schema mismatch, truncated data, etc.).
    """
    if len(frame) < _HEADER_SIZE:
        log.warning("sbe.frame_too_short", size=len(frame))
        return None

    block_length, template_id, schema_id, version = struct.unpack_from(
        _HEADER_FMT, frame, 0,
    )

    if schema_id != SCHEMA_ID:
        log.warning("sbe.schema_mismatch", expected=SCHEMA_ID, got=schema_id)
        return None

    decoder = _decoders.get(template_id)
    if decoder is None:
        log.debug("sbe.unknown_template", template_id=template_id)
        return None

    body_offset = _HEADER_SIZE
    try:
        return decoder(frame, body_offset, block_length)
    except Exception:
        log.exception("sbe.decode_error", template_id=template_id)
        return None
