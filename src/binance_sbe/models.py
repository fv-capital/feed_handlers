"""Normalized data models for decoded market data events."""

from __future__ import annotations

import struct
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# UDS wire‑format message type constants
# ---------------------------------------------------------------------------

class MsgType:
    """IPC message type identifiers (uint8)."""
    BEST_BID_ASK: int = 0x01
    TRADE: int = 0x02          # future
    DEPTH_DIFF: int = 0x03     # future
    DEPTH_SNAPSHOT: int = 0x04 # future
    HEARTBEAT: int = 0xFF


# ---------------------------------------------------------------------------
# Market data event dataclasses
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BestBidAsk:
    """Normalised best‑bid/ask event ready for IPC publishing.

    All prices/quantities are already converted from mantissa+exponent to
    native Python floats so consumers don't need to do any extra math.
    """

    event_time: int       # UTC timestamp in microseconds
    update_id: int        # order book update sequence id
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    symbol: str

    # ---- UDS serialisation helpers ----------------------------------------

    # Fixed portion: event_time(q) + update_id(q) + bid_price(d) +
    #                bid_qty(d) + ask_price(d) + ask_qty(d) = 48 bytes
    _FIXED_FMT = "<qqdddd"
    _FIXED_SIZE = struct.calcsize(_FIXED_FMT)  # 48

    def to_bytes(self) -> bytes:
        """Serialise to the UDS wire format (envelope included).

        Wire layout:
            msg_type  (1B)  | payload_len (2B) | payload (variable)

        Payload layout:
            event_time (8B) | update_id (8B) | bid_price (8B) |
            bid_qty (8B)    | ask_price (8B) | ask_qty (8B)   |
            symbol (variable UTF‑8)
        """
        symbol_bytes = self.symbol.encode("utf-8")
        payload = struct.pack(
            self._FIXED_FMT,
            self.event_time,
            self.update_id,
            self.bid_price,
            self.bid_qty,
            self.ask_price,
            self.ask_qty,
        ) + symbol_bytes
        # Envelope: msg_type (B) + payload_len (H)
        header = struct.pack("<BH", MsgType.BEST_BID_ASK, len(payload))
        return header + payload

    @classmethod
    def from_bytes(cls, payload: bytes) -> BestBidAsk:
        """Deserialise from a UDS payload (without envelope)."""
        (
            event_time,
            update_id,
            bid_price,
            bid_qty,
            ask_price,
            ask_qty,
        ) = struct.unpack_from(cls._FIXED_FMT, payload, 0)
        symbol = payload[cls._FIXED_SIZE:].decode("utf-8")
        return cls(
            event_time=event_time,
            update_id=update_id,
            bid_price=bid_price,
            bid_qty=bid_qty,
            ask_price=ask_price,
            ask_qty=ask_qty,
            symbol=symbol,
        )
