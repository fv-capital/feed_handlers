# Binance WebSocket Feed Handler

A lightweight, production-ready Python feed handler that connects to [Binance WebSocket Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams), decodes real-time market data, and publishes normalised events over Unix Domain Sockets (UDS) for downstream consumers.

Two feed handler implementations are provided:

| Package | Approach | Streams |
|---------|----------|---------|
| `binance_sbe` | Raw WebSocket + custom JSON decoder | `bookTicker` |
| `binance_sdk` | Official `binance-sdk-spot` SDK | `bookTicker`, rolling window ticker (`ticker_1h` / `ticker_4h` / `ticker_1d`) |

## Features

- **Real-time `bookTicker` stream** — receives best bid/ask price and quantity updates with zero delay
- **Rolling window ticker stream** (`binance_sdk`) — 1h / 4h / 1d rolling statistics per symbol
- **Combined streams** — subscribe to multiple symbols over a single WebSocket connection
- **Separate UDS publishers** — book ticker and rolling ticker events are published on independent Unix sockets so consumers can subscribe to exactly the data they need
- **Extensible decoder registry** (`binance_sbe`) — add new stream types (e.g., `trade`, `kline`, `depth`) by registering a simple decoder function
- **UDS IPC publisher** — publishes length-prefixed JSON frames to connected clients via Unix sockets
- **Automatic reconnection** (`binance_sbe`) — exponential back-off with configurable retries and a preemptive reconnect before the Binance 24-hour connection limit
- **Structured logging** — `structlog` (binance_sbe) / stdlib `logging` (binance_sdk)

## Architecture

```
┌──────────────┐     WebSocket      ┌──────────────────┐     UDS       ┌──────────────┐
│   Binance    │ ─── frames ──────▶│   Feed Handler   │ ── events ──▶│  Downstream  │
│  WS Server   │                    │                  │               │   Consumers  │
└──────────────┘                    │  connector /     │               └──────────────┘
                                    │  websocket_streams│
                                    │  decoder         │
                                    │  publisher(s)    │
                                    └──────────────────┘
```

### `binance_sbe` Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **Connector** | `src/binance_sbe/connector.py` | WebSocket connection lifecycle, reconnection logic |
| **Decoder** | `src/binance_sbe/decoder.py` | JSON parsing, stream-type dispatch, normalisation |
| **Publisher** | `src/binance_sbe/publisher.py` | UDS server, length-prefixed framing, fan-out to clients |
| **Models** | `src/binance_sbe/models.py` | Frozen dataclasses for normalised market data |
| **Config** | `src/binance_sbe/config.py` | YAML config loading and validation |
| **Main** | `src/binance_sbe/main.py` | Wires components together, signal handling, event loop |

### `binance_sdk` Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **WebSocket Streams** | `src/binance_sdk/websocket_streams.py` | SDK-based WebSocket subscriptions (bookTicker, rolling window ticker) |
| **Models** | `src/binance_sdk/models.py` | Frozen dataclasses for normalised stream data |
| **Main** | `src/binance_sdk/main.py` | Creates two UDS publishers, wires callbacks, signal handling |

## Project Structure

```
feed_handlers/
├── config/
│   └── config.yaml           # Shared configuration
├── scripts/
│   ├── demo.py               # Quick demo — prints bookTicker to console
│   └── test_uds_client.py    # Test client — reads from UDS sockets
├── src/
│   ├── binance_sbe/
│   │   ├── __main__.py        # python -m binance_sbe
│   │   ├── config.py
│   │   ├── connector.py
│   │   ├── decoder.py
│   │   ├── main.py
│   │   ├── models.py
│   │   └── publisher.py
│   └── binance_sdk/
│       ├── __init__.py
│       ├── __main__.py        # python -m binance_sdk
│       ├── main.py
│       ├── models.py
│       └── websocket_streams.py
├── tests/
├── pyproject.toml
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
  book_ticker_uds_path: "/tmp/binance_book_ticker.sock"
  rolling_ticker_uds_path: "/tmp/binance_rolling_ticker.sock"

logging:
  level: "info"
  format: "console"   # "console" or "json"
```

### Run the Feed Handler

#### Using the SDK-based handler (`binance_sdk`)

```bash
python -m binance_sdk
```

#### Using the SBE handler (`binance_sbe`)

```bash
python -m binance_sbe
```

Or with a custom config path:

```bash
python -m binance_sbe /path/to/config.yaml
```

### Read the Output (in a second terminal)

Listen to **both** streams:

```bash
python scripts/test_uds_client.py
```

Listen to **book ticker** only:

```bash
python scripts/test_uds_client.py book
```

Listen to **rolling window ticker** only:

```bash
python scripts/test_uds_client.py rolling
```

Example output:

```
Listening on 2 stream(s) (Ctrl+C to stop):

  [book] {'stream_type': 'bookTicker', 'symbol': 'btcusdt', 'bid_price': '97234.51', ...}
  [rolling] {'stream_type': 'rollingWindowTicker', 'symbol': 'btcusdt', 'close_price': '97200.00', ...}
```

### Quick Demo (no UDS, just console output)

```bash
python scripts/demo.py
```

## UDS Publishers

The `binance_sdk` handler creates **two** independent UDS publishers so downstream consumers can subscribe to exactly the event types they need:

| Socket Path | Event Type | Description |
|-------------|-----------|-------------|
| `/tmp/binance_book_ticker.sock` | `BookTickerEvent` | Best bid/ask price and quantity |
| `/tmp/binance_rolling_ticker.sock` | `RollingWindowTickerEvent` | Rolling 1h/4h/1d window statistics |

Both paths are configurable via `config.yaml` under the `publisher` section.

## IPC Protocol

The publisher uses a simple length-prefixed framing protocol over UDS:

```
┌────────────────────┬──────────────────────────────┐
│ 4 bytes (big-endian)│         JSON payload          │
│   payload length    │                              │
└────────────────────┴──────────────────────────────┘
```

Each JSON payload is a serialised dataclass (e.g., `BookTickerEvent`, `RollingWindowTickerEvent`).

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
