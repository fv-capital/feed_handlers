"""Test UDS client — connects to the publisher socket and prints updates."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

UDS_PATH = "/tmp/binance_feed.sock"


async def main() -> None:
    print(f"Connecting to {UDS_PATH} ...")
    reader, writer = await asyncio.open_unix_connection(UDS_PATH)
    print("Connected! Waiting for updates (Ctrl+C to stop):\n")

    try:
        while True:
            length_bytes = await reader.readexactly(4)
            length = int.from_bytes(length_bytes, "big")
            data = await reader.readexactly(length)
            msg = json.loads(data)
            print(f"  {msg}")
    except (asyncio.IncompleteReadError, ConnectionResetError):
        print("Connection closed.")
    finally:
        writer.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")