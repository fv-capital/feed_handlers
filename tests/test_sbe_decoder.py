"""Unit tests for the SBE decoder."""

import struct

import pytest

from binance_sbe.models import BestBidAsk
from binance_sbe.sbe_decoder import (
    SCHEMA_ID,
    SCHEMA_VERSION,
    TEMPLATE_BEST_BID_ASK,
    _BBA_BODY_FMT,
    _HEADER_FMT,
    decode,
)


def _build_header(
    block_length: int,
    template_id: int,
    schema_id: int = SCHEMA_ID,
    version: int = SCHEMA_VERSION,
) -> bytes:
    return struct.pack(_HEADER_FMT, block_length, template_id, schema_id, version)


def _build_best_bid_ask_frame(
    event_time: int = 1_700_000_000_000_000,
    book_update_id: int = 42,
    price_exp: int = -8,
    qty_exp: int = -8,
    bid_price_mantissa: int = 2_535_190_000,
    bid_qty_mantissa: int = 31_210_000_00,
    ask_price_mantissa: int = 2_536_520_000,
    ask_qty_mantissa: int = 40_660_000_00,
    symbol: str = "BNBUSDT",
) -> bytes:
    """Construct a valid BestBidAskStreamEvent binary frame."""
    body = struct.pack(
        _BBA_BODY_FMT,
        event_time,
        book_update_id,
        price_exp,
        qty_exp,
        bid_price_mantissa,
        bid_qty_mantissa,
        ask_price_mantissa,
        ask_qty_mantissa,
    )
    block_length = len(body)  # 50
    header = _build_header(block_length, TEMPLATE_BEST_BID_ASK)
    sym_bytes = symbol.encode("utf-8")
    var_string = bytes([len(sym_bytes)]) + sym_bytes
    return header + body + var_string


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------

class TestDecodeBestBidAsk:
    def test_decode_returns_best_bid_ask(self):
        frame = _build_best_bid_ask_frame()
        result = decode(frame)
        assert isinstance(result, BestBidAsk)

    def test_decode_event_time(self):
        frame = _build_best_bid_ask_frame(event_time=1_700_000_000_123_456)
        result = decode(frame)
        assert result.event_time == 1_700_000_000_123_456

    def test_decode_update_id(self):
        frame = _build_best_bid_ask_frame(book_update_id=999)
        result = decode(frame)
        assert result.update_id == 999

    def test_decode_bid_price(self):
        # mantissa=2535190000, exp=-8 → 25.35190000
        frame = _build_best_bid_ask_frame(
            price_exp=-8, bid_price_mantissa=2_535_190_000,
        )
        result = decode(frame)
        assert result.bid_price == pytest.approx(25.3519, rel=1e-9)

    def test_decode_ask_price(self):
        frame = _build_best_bid_ask_frame(
            price_exp=-8, ask_price_mantissa=2_536_520_000,
        )
        result = decode(frame)
        assert result.ask_price == pytest.approx(25.3652, rel=1e-9)

    def test_decode_bid_qty(self):
        frame = _build_best_bid_ask_frame(
            qty_exp=-8, bid_qty_mantissa=3_121_000_000,
        )
        result = decode(frame)
        assert result.bid_qty == pytest.approx(31.21, rel=1e-9)

    def test_decode_ask_qty(self):
        frame = _build_best_bid_ask_frame(
            qty_exp=-8, ask_qty_mantissa=4_066_000_000,
        )
        result = decode(frame)
        assert result.ask_qty == pytest.approx(40.66, rel=1e-9)

    def test_decode_symbol(self):
        frame = _build_best_bid_ask_frame(symbol="BTCUSDT")
        result = decode(frame)
        assert result.symbol == "BTCUSDT"

    def test_decode_symbol_non_ascii(self):
        frame = _build_best_bid_ask_frame(symbol="1000SATSUSDT")
        result = decode(frame)
        assert result.symbol == "1000SATSUSDT"


# -------------------------------------------------------------------
# Edge / error cases
# -------------------------------------------------------------------

class TestDecodeEdgeCases:
    def test_empty_frame(self):
        assert decode(b"") is None

    def test_truncated_header(self):
        assert decode(b"\x00\x01\x02") is None

    def test_wrong_schema_id(self):
        header = _build_header(50, TEMPLATE_BEST_BID_ASK, schema_id=99)
        frame = header + b"\x00" * 60
        assert decode(frame) is None

    def test_unknown_template_id(self):
        header = _build_header(50, template_id=65535)
        frame = header + b"\x00" * 60
        assert decode(frame) is None

    def test_truncated_body(self):
        header = _build_header(50, TEMPLATE_BEST_BID_ASK)
        frame = header + b"\x00" * 10  # body too short
        assert decode(frame) is None

    def test_different_exponents(self):
        frame = _build_best_bid_ask_frame(
            price_exp=-2,
            qty_exp=-3,
            bid_price_mantissa=12345,
            bid_qty_mantissa=67890,
        )
        result = decode(frame)
        assert result.bid_price == pytest.approx(123.45, rel=1e-9)
        assert result.bid_qty == pytest.approx(67.890, rel=1e-9)

    def test_zero_exponent(self):
        frame = _build_best_bid_ask_frame(
            price_exp=0,
            bid_price_mantissa=42,
        )
        result = decode(frame)
        assert result.bid_price == pytest.approx(42.0)
