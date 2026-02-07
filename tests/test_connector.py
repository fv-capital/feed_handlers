"""Unit tests for the WebSocket connector."""

import asyncio
import json

import pytest

from binance_sbe.config import BinanceConfig, ConnectionConfig
from binance_sbe.connector import BinanceConnector


class TestBuildUrl:
    """Test URL construction logic (no network required)."""

    def _make_connector(
        self,
        symbols: list[str] | None = None,
        streams: list[str] | None = None,
        base_url: str = "wss://stream-sbe.binance.com:9443",
    ) -> BinanceConnector:
        return BinanceConnector(
            binance_cfg=BinanceConfig(
                api_key="test-key",
                ws_base_url=base_url,
                symbols=symbols or ["btcusdt"],
                streams=streams or ["bestBidAsk"],
            ),
            conn_cfg=ConnectionConfig(),
            on_binary_frame=_noop_callback,
        )

    def test_single_symbol_single_stream(self):
        c = self._make_connector(symbols=["btcusdt"], streams=["bestBidAsk"])
        url = c._build_url()
        assert url == "wss://stream-sbe.binance.com:9443/ws/btcusdt@bestBidAsk"

    def test_multiple_symbols_single_stream(self):
        c = self._make_connector(
            symbols=["btcusdt", "ethusdt"], streams=["bestBidAsk"],
        )
        url = c._build_url()
        assert "/stream?streams=" in url
        assert "btcusdt@bestBidAsk" in url
        assert "ethusdt@bestBidAsk" in url

    def test_single_symbol_multiple_streams(self):
        c = self._make_connector(
            symbols=["btcusdt"], streams=["bestBidAsk", "trade"],
        )
        url = c._build_url()
        assert "/stream?streams=" in url
        assert "btcusdt@bestBidAsk" in url
        assert "btcusdt@trade" in url

    def test_multiple_symbols_multiple_streams(self):
        c = self._make_connector(
            symbols=["btcusdt", "ethusdt"],
            streams=["bestBidAsk", "trade"],
        )
        url = c._build_url()
        parts = url.split("/stream?streams=")[1].split("/")
        assert len(parts) == 4

    def test_trailing_slash_stripped(self):
        c = self._make_connector(
            base_url="wss://stream-sbe.binance.com:9443/",
            symbols=["btcusdt"],
            streams=["bestBidAsk"],
        )
        url = c._build_url()
        assert "9443//ws" not in url


class TestExtraHeaders:
    def test_api_key_header_present(self):
        c = BinanceConnector(
            binance_cfg=BinanceConfig(api_key="my-key"),
            conn_cfg=ConnectionConfig(),
            on_binary_frame=_noop_callback,
        )
        headers = c._extra_headers()
        assert headers["X-MBX-APIKEY"] == "my-key"

    def test_no_api_key_no_header(self):
        c = BinanceConnector(
            binance_cfg=BinanceConfig(api_key=""),
            conn_cfg=ConnectionConfig(),
            on_binary_frame=_noop_callback,
        )
        headers = c._extra_headers()
        assert "X-MBX-APIKEY" not in headers


async def _noop_callback(frame: bytes) -> None:
    pass
