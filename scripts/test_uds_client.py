#!/usr/bin/env python3
"""Simple IPC client for testing / debugging the feed handler.

Connects to the feed handler's IPC socket (UDS on Unix, TCP on Windows)
and prints every received market data message to stdout.

Usage:
    # Unix (UDS):
    python scripts/test_uds_client.py [/tmp/binance_feed.sock]

    # Windows (TCP loopback):
    python scripts/test_uds_client.py --tcp 127.0.0.1:9878
"""

from __future__ import annotations

import asyncio
import struct
import sys
from datetime import datetime, timezone

# Add src to path so we can import models
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from binance_sbe.models import BestBidAsk, MsgType

DEFAULT_UDS_PATH = "/tmp/binance_feed.sock"

# Envelope: msg_type (1B) + payload_len (2B) = 3 bytes
_ENVELOPE_SIZE = 3
_ENVELOPE_FMT = "<BH"


async def _open_connection(target: str, use_tcp: bool):
    """Open a reader/writer pair for UDS or TCP."""
    if use_tcp:
        host, port_str = target.rsplit(":", 1)
        return await asyncio.open_connection(host, int(port_str))
    else:
        return await asyncio.open_unix_connection(target)


async def run_client(target: str, use_tcp: bool) -> None:
    print(f"Connecting to {target} ({'TCP' if use_tcp else 'UDS'}) ...")
    reader, writer = await _open_connection(target, use_tcp)
    print("Connected!  Waiting for data ...\n")

    msg_count = 0
    try:
        while True:
            # Read envelope
            envelope = await reader.readexactly(_ENVELOPE_SIZE)
            msg_type, payload_len = struct.unpack(_ENVELOPE_FMT, envelope)

            if payload_len > 0:
                payload = await reader.readexactly(payload_len)
            else:
                payload = b""

            msg_count += 1

            if msg_type == MsgType.HEARTBEAT:
                print(f"[{msg_count}] ❤  HEARTBEAT")
                continue

            if msg_type == MsgType.BEST_BID_ASK:
                bba = BestBidAsk.from_bytes(payload)
                ts = datetime.fromtimestamp(
                    bba.event_time / 1_000_000, tz=timezone.utc,
                )
                print(
                    f"[{msg_count}] BBO  {bba.symbol:<12s}  "
                    f"bid={bba.bid_price:>14.8f} × {bba.bid_qty:<14.8f}  "
                    f"ask={bba.ask_price:>14.8f} × {bba.ask_qty:<14.8f}  "
                    f"id={bba.update_id}  t={ts.isoformat()}"
                )
                continue

            print(f"[{msg_count}] UNKNOWN msg_type=0x{msg_type:02x} len={payload_len}")

    except asyncio.IncompleteReadError:
        print("\nServer closed the connection.")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        writer.close()
        await writer.wait_closed()
        print(f"Total messages received: {msg_count}")


def main() -> None:
    use_tcp = "--tcp" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--tcp"]
    target = args[0] if args else ("127.0.0.1:9878" if use_tcp else DEFAULT_UDS_PATH)
    asyncio.run(run_client(target, use_tcp))


if __name__ == "__main__":
    main()
