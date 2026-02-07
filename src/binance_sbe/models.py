"""Normalised market-data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BestBidAsk:
    """Best bid/ask (BBO) update — used for both SBE and JSON streams."""

    stream_type: Literal["bestBidAsk", "bookTicker"] = "bookTicker"
    symbol: str = ""
    order_book_update_id: int = 0
    bid_price: float = 0.0
    bid_qty: float = 0.0
    ask_price: float = 0.0
    ask_qty: float = 0.0
    event_time: int = 0  # not present in bookTicker, kept for SBE compat