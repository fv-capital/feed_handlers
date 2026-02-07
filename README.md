# Binance WebSocket Feed Handler

A lightweight, production-ready Python feed handler that connects to [Binance WebSocket Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams), decodes real-time market data, and publishes normalised events over a Unix Domain Socket (UDS) for downstream consumers.

## Features

- **Real-time `bookTicker` stream** — receives best bid/ask price and quantity updates with zero delay
- **Combined streams** — subscribe to multiple symbols over a single WebSocket connection
- **Extensible decoder registry** — add new stream types (e.g., `trade`, `kline`, `depth`) by registering a simple decoder function
- **UDS IPC publisher** — publishes length-prefixed JSON frames to connected clients via a Unix socket
- **Automatic reconnection** — exponential back-off with configurable retries and a preemptive reconnect before the Binance 24-hour connection limit
- **Structured logging** — powered by `structlog` with JSON or console output

## Architecture

```
┌──────────────┐     WebSocket      ┌──────────────┐     UDS       ┌──────────────┐
│   Binance    │ ─── JSON frames ──▶│ Feed Handler │ ── events ──▶│  Downstream  │
│  WS Server   │                    │              │               │   Consumers  │
└──────────────┘                    │  connector   │               └──────────────┘
                                    │  decoder     │
                                    │  publisher   │
                                    └──────────────┘
```

| Component | File | Responsibility |
|-----------|------|----------------|
| **Connector** | `src/binance_sbe/connector.py` | WebSocket connection lifecycle, reconnection logic |
| **Decoder** | `src/binance_sbe/decoder.py` | JSON parsing, stream-type dispatch, normalisation |
| **Publisher** | `src/binance_sbe/publisher.py` | UDS server, length-prefixed framing, fan-out to clients |
| **Models** | `src/binance_sbe/models.py` | Frozen dataclasses for normalised market data |
| **Config** | `src/binance_sbe/config.py` | YAML config loading and validation |
| **Main** | `src/binance_sbe/main.py` | Wires components together, signal handling, event loop |

## Project Structure

```
feed_handlers/
├── config/
│   ├── config.example.yaml   # Example config (safe to commit)
│   └── config.yaml           # Your local config (git-ignored)
├── scripts/
│   ├── demo.py               # Quick demo — prints bookTicker to console
│   └── test_uds_client.py    # Test client — reads from UDS socket
├── src/
│   └── binance_sbe/
│       ├── __init__.py
│       ├── __main__.py        # python -m binance_sbe
│       ├── config.py
│       ├── connector.py
│       ├── decoder.py
│       ├── main.py
│       ├── models.py
│       └── publisher.py
├── tests/
├── pyproject.toml
├── .gitignore
└── README.md
```

## Quick Start

### Prerequisites

- Python 3.11+

### Installation

```bash
git clone <repo-url> && cd feed_handlers
pip install -e ".[dev]"
```

### Configuration

Copy the example config and edit as needed:

```bash
cp config/config.example.yaml config/config.yaml
```

```yaml
binance:
  base_url: "wss://stream.binance.com:9443"
  symbols:
    - btcusdt
    - ethusdt
  streams:
    - bookTicker

connection:
  max_retries: 10
  base_backoff_seconds: 1.0
  max_backoff_seconds: 60.0
  preemptive_reconnect_hours: 23.5

publisher:
  uds_path: "/tmp/binance_feed.sock"

logging:
  level: "info"
  format: "console"   # "console" or "json"
```

### Run the Feed Handler

```bash
python -m binance_sbe
```

Or with a custom config path:

```bash
python -m binance_sbe /path/to/config.yaml
```

### Read the Output (in a second terminal)

```bash
python scripts/test_uds_client.py
```

You should see real-time BBO updates:

```
Connecting to /tmp/binance_feed.sock ...
Connected! Waiting for updates (Ctrl+C to stop):

  [1] {'stream_type': 'bookTicker', 'symbol': 'BTCUSDT', 'order_book_update_id': 400900217, 'bid_price': 97234.51, 'bid_qty': 1.234, 'ask_price': 97234.99, 'ask_qty': 0.567, 'event_time': 0}
  [2] {'stream_type': 'bookTicker', 'symbol': 'ETHUSDT', ...}
```

### Quick Demo (no UDS, just console output)

```bash
python scripts/demo.py
```

## Subscribing to Streams

### Supported Streams

| Stream | Config Value | Description |
|--------|-------------|-------------|
| [Book Ticker](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams#individual-symbol-book-ticker-streams) | `bookTicker` | Real-time best bid/ask price and quantity |

### Adding a New Stream Type

1. **Add the stream name** to `config.yaml`:

   ```yaml
   binance:
     streams:
       - bookTicker
       - trade        # <-- new
   ```

2. **Add a model** in `src/binance_sbe/models.py`:

   ```python
   @dataclass(frozen=True, slots=True)
   class Trade:
       stream_type: Literal["trade"] = "trade"
       symbol: str = ""
       price: float = 0.0
       quantity: float = 0.0
       trade_id: int = 0
       event_time: int = 0
   ```

3. **Register a decoder** in `src/binance_sbe/decoder.py`:

   ```python
   @_register("trade")
   def _decode_trade(data: dict[str, Any]) -> Trade:
       return Trade(
           symbol=data.get("s", ""),
           price=float(data.get("p", 0)),
           quantity=float(data.get("q", 0)),
           trade_id=data.get("t", 0),
           event_time=data.get("E", 0),
       )
   ```

4. **Handle the new type** in `main.py`'s `_on_text_frame` callback if needed.

## IPC Protocol

The publisher uses a simple length-prefixed framing protocol over UDS:

```
┌────────────────────┬──────────────────────────────┐
│ 4 bytes (big-endian)│         JSON payload          │
│   payload length    │                              │
└────────────────────┴──────────────────────────────┘
```

Each JSON payload is a serialised dataclass (e.g., `BestBidAsk`).

## Running Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=binance_sbe --cov-report=term-missing
```

## License

MIT
