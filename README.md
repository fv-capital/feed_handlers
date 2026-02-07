# Binance SBE Feed Handler

A low-latency feed handler that connects to **Binance's SBE (Simple Binary Encoding) WebSocket Market Data Streams** for spot trading and publishes normalized market data to strategy engines over **Unix Domain Sockets (UDS)**.

## Features

- **SBE binary decoding** — direct `struct.unpack` of Binance SBE frames (no JSON overhead)
- **Best Bid/Ask streams** — real-time BBO with auto-culling support
- **UDS IPC** — ultra-low-latency local communication with strategy engines via compact binary protocol
- **Automatic reconnection** — exponential backoff with preemptive 24-hour reconnect
- **Multi-client** — multiple strategy engines can connect to the same feed
- **Extensible** — adding new stream types (trades, depth) requires minimal changes

## Supported Streams

| Stream                                  | Status         |
| --------------------------------------- | -------------- |
| Best Bid/Ask (`<symbol>@bestBidAsk`)    | ✅ Implemented |
| Trades (`<symbol>@trade`)               | 🔜 Planned     |
| Diff. Depth (`<symbol>@depth`)          | 🔜 Planned     |
| Partial Book Depth (`<symbol>@depth20`) | 🔜 Planned     |

## Quick Start

### Prerequisites

- Python 3.12+
- A Binance **Ed25519** API key (no special permissions needed for public market data)
- Linux/macOS (UDS requires Unix-like OS) or Windows with WSL

### Installation

```bash
pip install -e .
```

### Configuration

Edit `config/config.yaml`:

```yaml
binance:
  api_key: 'YOUR_ED25519_API_KEY'
  symbols:
    - btcusdt
    - ethusdt
  streams:
    - bestBidAsk

publisher:
  uds_path: '/tmp/binance_feed.sock'
```

### Run

```bash
python -m binance_sbe
```

### Test UDS Client

In another terminal, connect to the feed to verify data flow:

```bash
python scripts/test_uds_client.py
```

## Project Structure

```
feed_handlers/
├── docs/DESIGN.md              # Full design document
├── config/config.yaml          # Runtime configuration
├── src/binance_sbe/
│   ├── main.py                 # Entry point & orchestrator
│   ├── config.py               # Configuration loader
│   ├── connector.py            # WebSocket connector
│   ├── sbe_decoder.py          # SBE binary decoder
│   ├── publisher.py            # UDS publisher
│   └── models.py               # Normalized data models
├── tests/                      # Unit & integration tests
└── scripts/test_uds_client.py  # Debug/test UDS client
```

## Documentation

See **[docs/DESIGN.md](docs/DESIGN.md)** for the full design document, including:

- Binance SBE API reference and binary layout
- Architecture diagrams
- IPC wire format specification
- Configuration reference
- Extensibility guide

## License

Private — internal use only.
