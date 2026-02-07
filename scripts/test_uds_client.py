"""Test UDS client — connects to the book ticker and/or rolling ticker
publisher sockets and prints updates.

Usage:
    python scripts/test_uds_client.py              # both streams
    python scripts/test_uds_client.py book          # book ticker only
    python scripts/test_uds_client.py rolling       # rolling ticker only
"""

from __future__ import annotations

import asyncio
import json
import sys

BOOK_TICKER_UDS_PATH = "/tmp/binance_book_ticker.sock"
ROLLING_TICKER_UDS_PATH = "/tmp/binance_rolling_ticker.sock"

STREAMS = {
    "book": BOOK_TICKER_UDS_PATH,
    "rolling": ROLLING_TICKER_UDS_PATH,
}


async def listen(name: str, uds_path: str) -> None:
    """Connect to a single UDS socket and print every frame."""
    print(f"[{name}] Connecting to {uds_path} ...")
    reader, writer = await asyncio.open_unix_connection(uds_path)
    print(f"[{name}] Connected!\n")

    try:
        while True:
            length_bytes = await reader.readexactly(4)
            length = int.from_bytes(length_bytes, "big")
            data = await reader.readexactly(length)
            msg = json.loads(data)
            print(f"  [{name}] {msg}")
    except (asyncio.IncompleteReadError, ConnectionResetError):
        print(f"[{name}] Connection closed.")
    finally:
        writer.close()


async def main(selected: list[str]) -> None:
    tasks = [
        asyncio.create_task(listen(name, path))
        for name, path in STREAMS.items()
        if name in selected
    ]
    print(f"Listening on {len(tasks)} stream(s) (Ctrl+C to stop):\n")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    choice = sys.argv[1].lower() if len(sys.argv) > 1 else "both"

    valid_choices = {"book", "rolling", "both"}
    if choice not in valid_choices:
        print(f"Unknown stream choice: {choice!r}\n", file=sys.stderr)
        usage = __doc__ or (
            "Usage:\n"
            "    python scripts/test_uds_client.py              # both streams\n"
            "    python scripts/test_uds_client.py book          # book ticker only\n"
            "    python scripts/test_uds_client.py rolling       # rolling ticker only\n"
        )
        print(usage, file=sys.stderr)
        sys.exit(1)
    if choice == "book":
        selected = ["book"]
    elif choice == "rolling":
        selected = ["rolling"]
    else:
        selected = list(STREAMS.keys())

    try:
        asyncio.run(main(selected))
    except KeyboardInterrupt:
        print("\nStopped.")