"""Normalised models for Binance SDK WebSocket stream data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BookTickerEvent:
    """Individual Symbol Book Ticker update.

    Pushes the best bid/ask price and quantity in real-time.
    Stream: ``<symbol>@bookTicker``
    """

    stream_type: Literal["bookTicker"] = "bookTicker"
    update_id: int = 0
    symbol: str = ""
    bid_price: str = ""
    bid_qty: str = ""
    ask_price: str = ""
    ask_qty: str = ""


@dataclass(frozen=True, slots=True)
class RollingWindowTickerEvent:
    """Individual Symbol Rolling Window Statistics update.

    Rolling window ticker statistics for a single symbol.
    Stream: ``<symbol>@ticker_<windowSize>``
    """

    stream_type: str = "rollingWindowTicker"
    event_type: str = ""
    event_time: int = 0
    symbol: str = ""
    price_change: str = ""
    price_change_percent: str = ""
    open_price: str = ""
    high_price: str = ""
    low_price: str = ""
    close_price: str = ""
    weighted_avg_price: str = ""
    total_traded_base_volume: str = ""
    total_traded_quote_volume: str = ""
    statistics_open_time: int = 0
    statistics_close_time: int = 0
    first_trade_id: int = 0
    last_trade_id: int = 0
    total_num_trades: int = 0
