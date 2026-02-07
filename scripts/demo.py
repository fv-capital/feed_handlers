#!/usr/bin/env python3
"""Local end-to-end demo — runs a mock Binance SBE WebSocket server,
the feed handler, and a test client all in one script.

No API key or internet connection required.

Usage:
    python scripts/demo.py
"""

from __future__ import annotations

import asyncio
import json
import random
import struct
import sys
import time
from pathlib import Path

# -- path fixup so we can import from src/ without installing ------------
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from binance_sbe.config import (
    AppConfig,
    BinanceConfig,
    ConnectionConfig,
    LoggingConfig,
    PublisherConfig,
)
from binance_sbe.main import FeedHandlerApp, _configure_logging
from binance_sbe.models import BestBidAsk, MsgType
from binance_sbe.sbe_decoder import (
    SCHEMA_ID,
    SCHEMA_VERSION,
    TEMPLATE_BEST_BID_ASK,
)

# -----------------------------------------------------------------------
# Fake SBE frame builder
# -----------------------------------------------------------------------

_HEADER_FMT = "<HHHH"
_BBA_BODY_FMT = "<qqbbqqqq"


def _build_best_bid_ask_frame(
    symbol: str,
    bid: float,
    ask: float,
    bid_qty: float,
    ask_qty: float,
    update_id: int,
) -> bytes:
    """Build a binary SBE BestBidAskStreamEvent frame."""
    price_exp = -8
    qty_exp = -8

    def to_mantissa(val: float, exp: int) -> int:
        return int(round(val / (10.0 ** exp)))

    event_time = int(time.time() * 1_000_000)  # µs

    body = struct.pack(
        _BBA_BODY_FMT,
        event_time,
        update_id,
        price_exp,
        qty_exp,
        to_mantissa(bid, price_exp),
        to_mantissa(bid_qty, qty_exp),
        to_mantissa(ask, price_exp),
        to_mantissa(ask_qty, qty_exp),
    )
    header = struct.pack(
        _HEADER_FMT,
        len(body),           # blockLength
        TEMPLATE_BEST_BID_ASK,
        SCHEMA_ID,
        SCHEMA_VERSION,
    )
    sym_bytes = symbol.encode("utf-8")
    var_string = bytes([len(sym_bytes)]) + sym_bytes
    return header + body + var_string


# -----------------------------------------------------------------------
# Mock Binance SBE WebSocket server
# -----------------------------------------------------------------------

async def _mock_ws_handler(ws):
    """Handle one WebSocket client (the feed handler)."""
    import websockets

    # Wait for the SUBSCRIBE text message
    try:
        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
        if isinstance(msg, str):
            data = json.loads(msg)
            print(f"  [mock-server] Received SUBSCRIBE: {data.get('params', [])}")
            await ws.send(json.dumps({"result": None, "id": data.get("id", 1)}))
    except Exception as e:
        print(f"  [mock-server] Subscribe handshake error: {e}")

    # Send fake BBO updates
    symbols = ["BTCUSDT", "ETHUSDT"]
    base_prices = {"BTCUSDT": 97_500.0, "ETHUSDT": 2_750.0}
    update_id = 1

    print("  [mock-server] Streaming fake BBO data ...\n")

    try:
        while True:
            for sym in symbols:
                base = base_prices[sym]
                spread = base * 0.0001  # 1 bps spread
                jitter = base * random.uniform(-0.002, 0.002)
                bid = base + jitter
                ask = bid + spread
                bid_qty = round(random.uniform(0.1, 5.0), 8)
                ask_qty = round(random.uniform(0.1, 5.0), 8)

                frame = _build_best_bid_ask_frame(
                    symbol=sym,
                    bid=bid,
                    ask=ask,
                    bid_qty=bid_qty,
                    ask_qty=ask_qty,
                    update_id=update_id,
                )
                await ws.send(frame)
                update_id += 1

            await asyncio.sleep(0.5)  # 2 updates/sec per symbol
    except Exception:
        pass


async def start_mock_server(port: int):
    import websockets

    server = await websockets.serve(
        _mock_ws_handler,
        "127.0.0.1",
        port,
    )
    print(f"  [mock-server] Listening on ws://127.0.0.1:{port}")
    return server


# -----------------------------------------------------------------------
# Test IPC client (reads from publisher)
# -----------------------------------------------------------------------

_ENVELOPE_FMT = "<BH"
_ENVELOPE_SIZE = 3


async def run_test_client(port: int) -> None:
    """Connect to the publisher's TCP port and print received data."""
    await asyncio.sleep(1.5)  # wait for publisher to be ready

    print(f"  [test-client] Connecting to publisher on 127.0.0.1:{port} ...")
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    print("  [test-client] Connected!  Displaying feed:\n")
    print(f"  {'#':<5s}  {'Symbol':<10s}  {'Bid':>14s}  {'BidQty':>12s}  {'Ask':>14s}  {'AskQty':>12s}  {'UpdateID':>10s}")
    print(f"  {'─'*5}  {'─'*10}  {'─'*14}  {'─'*12}  {'─'*14}  {'─'*12}  {'─'*10}")

    count = 0
    try:
        while True:
            envelope = await reader.readexactly(_ENVELOPE_SIZE)
            msg_type, payload_len = struct.unpack(_ENVELOPE_FMT, envelope)

            if payload_len > 0:
                payload = await reader.readexactly(payload_len)
            else:
                payload = b""

            count += 1

            if msg_type == MsgType.HEARTBEAT:
                continue  # silent

            if msg_type == MsgType.BEST_BID_ASK:
                bba = BestBidAsk.from_bytes(payload)
                print(
                    f"  {count:<5d}  {bba.symbol:<10s}  "
                    f"{bba.bid_price:>14.4f}  {bba.bid_qty:>12.8f}  "
                    f"{bba.ask_price:>14.4f}  {bba.ask_qty:>12.8f}  "
                    f"{bba.update_id:>10d}"
                )
    except asyncio.IncompleteReadError:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        writer.close()


# -----------------------------------------------------------------------
# Main — wire everything together
# -----------------------------------------------------------------------

async def main() -> None:
    MOCK_WS_PORT = 19443
    PUBLISHER_TCP_PORT = 19878

    print("=" * 65)
    print("  Binance SBE Feed Handler — Local Demo")
    print("=" * 65)
    print()

    # 1. Start mock Binance WS server
    mock_server = await start_mock_server(MOCK_WS_PORT)

    # 2. Configure the feed handler to point at the mock server
    config = AppConfig(
        binance=BinanceConfig(
            api_key="",  # mock server doesn't check
            ws_base_url=f"ws://127.0.0.1:{MOCK_WS_PORT}",
            symbols=["btcusdt"],
            streams=["bestBidAsk"],
        ),
        connection=ConnectionConfig(
            reconnect_delay_initial=1.0,
            max_reconnect_attempts=3,
        ),
        publisher=PublisherConfig(
            uds_path="/tmp/binance_demo.sock",
            tcp_port=PUBLISHER_TCP_PORT,
            max_clients=5,
            write_timeout=1.0,
        ),
        logging=LoggingConfig(level="WARNING", format="console"),
    )
    _configure_logging(config)

    app = FeedHandlerApp(config)

    # 3. Run feed handler + test client concurrently
    app_task = asyncio.create_task(app.run())
    client_task = asyncio.create_task(run_test_client(PUBLISHER_TCP_PORT))

    print(f"  [demo] Feed handler started.  Press Ctrl+C to stop.\n")

    try:
        await asyncio.gather(app_task, client_task)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        print("\n\n  [demo] Shutting down ...")
        client_task.cancel()
        await app.shutdown()
        mock_server.close()
        await mock_server.wait_closed()
        print("  [demo] Done.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Bye!")
