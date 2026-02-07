# Binance SBE Feed Handler — Design Document

**Version:** 1.0  
**Date:** 2026-02-07  
**Status:** Draft

---

## 1. Overview

This project implements a **low-latency feed handler** that connects to **Binance's SBE (Simple Binary Encoding) Market Data Streams** for spot trading. The feed handler decodes binary SBE messages from Binance's WebSocket API and publishes normalized market data to a local **strategy engine** via **Unix Domain Sockets (UDS)**.

### 1.1 Goals

- Connect to Binance SBE WebSocket streams (`stream-sbe.binance.com`)
- Decode SBE binary frames with zero-copy efficiency
- Publish normalized, low-latency market data over UDS to downstream strategy engines
- Start with **Best Bid/Ask Streams** (`<symbol>@bestBidAsk`), with an extensible architecture to support additional streams in the future

### 1.2 Future Streams (Planned)

| Stream                | SBE Message                           | Stream Name           | Priority    |
| --------------------- | ------------------------------------- | --------------------- | ----------- |
| ✅ Best Bid/Ask       | `BestBidAskStreamEvent` (id=10001)    | `<symbol>@bestBidAsk` | **Phase 1** |
| ⬜ Trades             | `TradesStreamEvent` (id=10000)        | `<symbol>@trade`      | Phase 2     |
| ⬜ Diff. Depth        | `DepthDiffStreamEvent` (id=10003)     | `<symbol>@depth`      | Phase 3     |
| ⬜ Partial Book Depth | `DepthSnapshotStreamEvent` (id=10002) | `<symbol>@depth20`    | Phase 3     |

---

## 2. Binance SBE WebSocket API Reference

### 2.1 Connection Details

| Property                       | Value                                                                     |
| ------------------------------ | ------------------------------------------------------------------------- |
| **Endpoint**                   | `wss://stream-sbe.binance.com:9443` or `wss://stream-sbe.binance.com:443` |
| **Single stream**              | `/ws/<streamName>`                                                        |
| **Combined streams**           | `/stream?streams=<streamName1>/<streamName2>/...`                         |
| **Authentication**             | Ed25519 API Key in `X-MBX-APIKEY` header (no signature required)          |
| **Max connection lifetime**    | 24 hours (automatic disconnect)                                           |
| **Max streams per connection** | 1024                                                                      |
| **Rate limit (client→server)** | 5 messages/second (PING, PONG, JSON control)                              |
| **Connection limit**           | 300 connections per 5 minutes per IP                                      |
| **Timestamps**                 | All in **microseconds**                                                   |

### 2.2 Keepalive

- Server sends a `ping` frame every 20 seconds
- Client must reply with `pong` (copying the ping payload) within 60 seconds or be disconnected
- Unsolicited pongs are allowed but do not prevent disconnection

### 2.3 Subscription Management

Subscriptions are managed via **JSON text frames** (same as standard Binance WS):

```json
// Subscribe
{"method": "SUBSCRIBE", "params": ["btcusdt@bestBidAsk"], "id": 1}

// Unsubscribe
{"method": "UNSUBSCRIBE", "params": ["btcusdt@bestBidAsk"], "id": 2}

// List subscriptions
{"method": "LIST_SUBSCRIPTIONS", "id": 3}
```

- Subscription responses arrive as **text frames** (JSON)
- Market data events arrive as **binary frames** (SBE)

### 2.4 Auto-Culling (Best Bid/Ask)

SBE best bid/ask streams support **auto-culling**: under high load, stale queued events are dropped in favor of the most recent event per symbol. This guarantees the client always sees the latest state rather than falling behind.

### 2.5 SBE Binary Encoding

All SBE messages use **little-endian** byte order.

#### Message Header (8 bytes)

| Field         | Type   | Size                                  |
| ------------- | ------ | ------------------------------------- |
| `blockLength` | uint16 | 2 bytes                               |
| `templateId`  | uint16 | 2 bytes — identifies the message type |
| `schemaId`    | uint16 | 2 bytes — always `1`                  |
| `version`     | uint16 | 2 bytes — always `0`                  |

#### Template IDs

| Template ID | Message Name               | Stream                |
| ----------- | -------------------------- | --------------------- |
| 10000       | `TradesStreamEvent`        | `<symbol>@trade`      |
| 10001       | `BestBidAskStreamEvent`    | `<symbol>@bestBidAsk` |
| 10002       | `DepthSnapshotStreamEvent` | `<symbol>@depth20`    |
| 10003       | `DepthDiffStreamEvent`     | `<symbol>@depth`      |

#### BestBidAskStreamEvent (Template ID 10001) — Phase 1 Target

Binary layout after the 8-byte message header:

| Field           | Type       | Offset | Size | Description                            |
| --------------- | ---------- | ------ | ---- | -------------------------------------- |
| `eventTime`     | int64 (µs) | 0      | 8    | UTC timestamp in microseconds          |
| `bookUpdateId`  | int64      | 8      | 8    | Order book update sequence ID          |
| `priceExponent` | int8       | 16     | 1    | Exponent for price mantissa (e.g., -8) |
| `qtyExponent`   | int8       | 17     | 1    | Exponent for quantity mantissa         |
| `bidPrice`      | int64      | 18     | 8    | Best bid price mantissa                |
| `bidQty`        | int64      | 26     | 8    | Best bid quantity mantissa             |
| `askPrice`      | int64      | 34     | 8    | Best ask price mantissa                |
| `askQty`        | int64      | 42     | 8    | Best ask quantity mantissa             |

Followed by a variable-length `symbol` field (varString8: 1-byte length prefix + UTF-8 string).

**Price/Quantity Calculation:**

```
actual_value = mantissa * 10^exponent
```

Example: `mantissa=2535190000`, `exponent=-8` → `25.35190000`

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Feed Handler Process                        │
│                                                                    │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────────┐ │
│  │  WebSocket   │───▶│  SBE Decoder │───▶│   UDS Publisher       │ │
│  │  Connector   │    │              │    │  (Unix Domain Socket) │ │
│  │              │    │  - Header    │    │                       │ │
│  │  - Connect   │    │  - BestBidAsk│    │  - Serialize to       │ │
│  │  - Auth      │    │  - Trades*   │    │    binary struct      │ │
│  │  - Subscribe │    │  - Depth*    │    │  - Publish to all     │ │
│  │  - Reconnect │    │              │    │    connected clients  │ │
│  │  - Keepalive │    │  (* future)  │    │                       │ │
│  └──────────────┘    └──────────────┘    └───────────┬───────────┘ │
│                                                      │             │
└──────────────────────────────────────────────────────┼─────────────┘
                                                       │
                                            UDS (e.g., /tmp/binance_feed.sock)
                                                       │
                                          ┌────────────▼────────────┐
                                          │   Strategy Engine(s)    │
                                          │   (separate process)    │
                                          └─────────────────────────┘
```

### 3.1 Component Breakdown

#### 3.1.1 WebSocket Connector (`connector.py`)

**Responsibility:** Manage the lifecycle of the WebSocket connection to Binance SBE.

- Establish WSS connection with Ed25519 API key in `X-MBX-APIKEY` header
- Handle ping/pong keepalive (auto-reply to server pings)
- Send JSON subscription requests for desired streams
- Differentiate text frames (subscription responses) from binary frames (SBE data)
- Automatic reconnection with exponential backoff on disconnect
- Graceful 24-hour reconnection before Binance forces disconnect
- Connection health monitoring and metrics

#### 3.1.2 SBE Decoder (`sbe_decoder.py`)

**Responsibility:** Zero-copy decode binary SBE frames into Python data structures.

- Parse 8-byte SBE message header to identify template ID
- Decode `BestBidAskStreamEvent` (template 10001):
  - Extract `eventTime`, `bookUpdateId`, exponents, bid/ask price/qty mantissas, symbol
  - Apply `mantissa * 10^exponent` to compute actual prices/quantities
- Use Python's `struct` module for efficient binary unpacking
- Extensible: register new decoders by template ID for future stream types
- Validate schema ID and version

#### 3.1.3 UDS Publisher (`publisher.py`)

**Responsibility:** Publish decoded market data to strategy engines over Unix Domain Sockets.

- Create and listen on a configurable UDS path (e.g., `/tmp/binance_feed.sock`)
- Accept multiple concurrent client connections (strategy engines)
- Serialize normalized market data into a compact binary protocol for IPC:
  - Fixed-size header: message type (1 byte) + payload length (4 bytes)
  - Payload: `struct`-packed fields for minimal overhead
- Non-blocking writes; drop slow consumers to avoid backpressure
- Handle client connect/disconnect gracefully

#### 3.1.4 Configuration (`config.py`)

**Responsibility:** Centralized configuration management.

- YAML-based configuration file
- Settings include:
  - Binance API key
  - Symbols to subscribe to
  - UDS socket path
  - Reconnection parameters (backoff, max retries)
  - Logging level

#### 3.1.5 Main Orchestrator (`main.py`)

**Responsibility:** Wire all components together and manage the application lifecycle.

- Parse CLI arguments and load configuration
- Initialize connector, decoder, publisher
- Run the asyncio event loop
- Handle graceful shutdown on SIGINT/SIGTERM

---

## 4. IPC Protocol (UDS Wire Format)

The feed handler publishes normalized messages to strategy engines over UDS using a simple binary protocol optimized for low latency.

### 4.1 Envelope

Every message on the UDS wire is framed as:

```
┌──────────┬────────────┬─────────────────────┐
│ msg_type │ payload_len│      payload         │
│ (1 byte) │ (2 bytes)  │  (variable)          │
└──────────┴────────────┴─────────────────────┘
```

| Field         | Type   | Description                     |
| ------------- | ------ | ------------------------------- |
| `msg_type`    | uint8  | Message type identifier         |
| `payload_len` | uint16 | Length of payload in bytes      |
| `payload`     | bytes  | Message-specific binary payload |

### 4.2 Message Types

| msg_type | Name             | Description                |
| -------- | ---------------- | -------------------------- |
| `0x01`   | `BEST_BID_ASK`   | Best bid/ask update        |
| `0x02`   | `TRADE`          | Trade event (future)       |
| `0x03`   | `DEPTH_DIFF`     | Depth diff update (future) |
| `0x04`   | `DEPTH_SNAPSHOT` | Depth snapshot (future)    |
| `0xFF`   | `HEARTBEAT`      | Publisher heartbeat        |

### 4.3 BEST_BID_ASK Payload (msg_type = 0x01)

```
┌──────────────┬──────────────┬───────────┬──────────┬───────────┬──────────┬──────────────┐
│  event_time  │ update_id    │ bid_price │ bid_qty  │ ask_price │ ask_qty  │ symbol       │
│  (8 bytes)   │ (8 bytes)    │ (8 bytes) │ (8 bytes)│ (8 bytes) │ (8 bytes)│ (N bytes)    │
│  int64 µs    │ int64        │ float64   │ float64  │ float64   │ float64  │ utf-8 string │
└──────────────┴──────────────┴───────────┴──────────┴───────────┴──────────┴──────────────┘
```

Total fixed part: 48 bytes + variable-length symbol.

Prices and quantities are transmitted as **IEEE 754 float64** (already converted from mantissa+exponent), so strategy engines can use them directly without additional math.

---

## 5. Project Structure

```
feed_handlers/
├── README.md
├── docs/
│   └── DESIGN.md                  # This document
├── pyproject.toml                 # Project metadata, dependencies
├── config/
│   └── config.yaml                # Runtime configuration
├── src/
│   └── binance_sbe/
│       ├── __init__.py
│       ├── main.py                # Entry point & orchestrator
│       ├── config.py              # Configuration loader
│       ├── connector.py           # WebSocket connector (asyncio + websockets)
│       ├── sbe_decoder.py         # SBE binary decoder
│       ├── publisher.py           # UDS publisher (asyncio Unix socket server)
│       └── models.py              # Data classes for normalized market data
├── tests/
│   ├── __init__.py
│   ├── test_sbe_decoder.py        # Unit tests for SBE decoding
│   ├── test_publisher.py          # Unit tests for UDS publisher
│   └── test_connector.py          # Unit tests for WS connector
└── scripts/
    └── test_uds_client.py         # Simple UDS client for testing/debugging
```

---

## 6. Technology Stack

| Component        | Technology                        | Rationale                                           |
| ---------------- | --------------------------------- | --------------------------------------------------- |
| Language         | **Python 3.12+**                  | Requested; rich async ecosystem                     |
| Async runtime    | **asyncio**                       | Native Python async; no extra deps                  |
| WebSocket client | **websockets**                    | Mature, asyncio-native, supports binary frames      |
| Binary decoding  | **struct** (stdlib)               | Zero-dependency, fast for fixed layouts             |
| IPC              | **Unix Domain Sockets** (asyncio) | Lowest-latency local IPC; no serialization overhead |
| Configuration    | **PyYAML**                        | Human-readable config files                         |
| Logging          | **structlog**                     | Structured, JSON-capable logging                    |
| Testing          | **pytest + pytest-asyncio**       | Standard Python testing                             |

---

## 7. Configuration

```yaml
# config/config.yaml

binance:
  api_key: 'YOUR_ED25519_API_KEY'
  ws_base_url: 'wss://stream-sbe.binance.com:9443'
  symbols:
    - btcusdt
    - ethusdt
  streams:
    - bestBidAsk # Phase 1
    # - trade             # Phase 2
    # - depth             # Phase 3
    # - depth20           # Phase 3

connection:
  reconnect_delay_initial: 1.0 # seconds
  reconnect_delay_max: 60.0 # seconds
  reconnect_delay_multiplier: 2.0
  max_reconnect_attempts: 0 # 0 = infinite
  preemptive_reconnect_hours: 23.5 # reconnect before 24h limit

publisher:
  uds_path: '/tmp/binance_feed.sock'
  max_clients: 10
  write_timeout: 0.1 # seconds; drop client if write blocks

logging:
  level: 'INFO' # DEBUG, INFO, WARNING, ERROR
  format: 'json' # json or console
```

---

## 8. Key Behaviors

### 8.1 Connection Lifecycle

```
START
  │
  ▼
Connect to WSS ──────────────────┐
  │                               │
  ▼                               │ (on error / disconnect)
Send SUBSCRIBE (JSON text frame) │
  │                               │
  ▼                               │
Receive frames in loop            │
  │                               │
  ├─ Text frame → parse JSON      │
  │   (subscription response)     │
  │                               │
  ├─ Binary frame → SBE decode    │
  │   → publish to UDS            │
  │                               │
  ├─ Ping frame → send Pong       │
  │                               │
  ├─ 23.5h elapsed → reconnect ──┘
  │
  ├─ Error/Close → reconnect ────┘
  │   (exponential backoff)
  │
  ▼
SHUTDOWN (SIGINT/SIGTERM)
  │
  ▼
Close WS + UDS cleanly
```

### 8.2 SBE Decoding Pipeline

```
Binary frame (bytes)
  │
  ▼
Parse message header (8 bytes)
  ├─ templateId == 10001 → decode BestBidAskStreamEvent
  ├─ templateId == 10000 → decode TradesStreamEvent (future)
  ├─ templateId == 10002 → decode DepthSnapshotStreamEvent (future)
  ├─ templateId == 10003 → decode DepthDiffStreamEvent (future)
  └─ unknown → log warning, skip
  │
  ▼
Normalized Python dataclass (BestBidAsk)
  │
  ▼
Pack into UDS wire format → publish
```

### 8.3 Error Handling

| Scenario               | Behavior                                         |
| ---------------------- | ------------------------------------------------ |
| WebSocket disconnect   | Exponential backoff reconnect                    |
| SBE decode error       | Log error, skip message, continue                |
| UDS client disconnects | Remove from client list, continue serving others |
| UDS write blocks       | Drop message for slow client, log warning        |
| Invalid template ID    | Log warning, skip frame                          |
| SIGINT / SIGTERM       | Graceful shutdown: close WS, close UDS, exit     |

---

## 9. Performance Considerations

1. **struct.unpack** is used for SBE decoding — avoids overhead of general-purpose deserialization
2. **Auto-culling** on Binance side ensures we always get the latest BBO even under load
3. **UDS** avoids TCP overhead (no Nagle, no network stack) for local IPC
4. **Binary IPC protocol** avoids JSON serialization/deserialization costs
5. **asyncio** single-threaded event loop avoids lock contention
6. **Slow consumer protection**: clients that can't keep up are dropped, preventing backpressure from affecting other consumers

---

## 10. Testing Strategy

| Test Type       | Scope                                                                            | Tools                       |
| --------------- | -------------------------------------------------------------------------------- | --------------------------- |
| **Unit**        | SBE decoder with crafted binary payloads                                         | pytest                      |
| **Unit**        | UDS publisher with mock clients                                                  | pytest-asyncio              |
| **Integration** | End-to-end with mock WS server                                                   | pytest-asyncio + websockets |
| **Manual**      | `scripts/test_uds_client.py` connects to live UDS socket and prints decoded data | —                           |

---

## 11. Extensibility

Adding a new stream type (e.g., Trades in Phase 2) requires:

1. **`sbe_decoder.py`**: Add a new decoder function for the template ID (e.g., 10000)
2. **`models.py`**: Add a new dataclass for the normalized event (e.g., `TradeEvent`)
3. **`publisher.py`**: Add a new message type constant and serialization method
4. **`config.yaml`**: Add the stream name to `binance.streams`
5. **`connector.py`**: No changes needed — it already subscribes to all configured streams

This design ensures **O(1) changes per layer** when adding new stream types.

---

## 12. Glossary

| Term             | Definition                                                                                         |
| ---------------- | -------------------------------------------------------------------------------------------------- |
| **SBE**          | Simple Binary Encoding — a FIX protocol for low-latency binary messages                            |
| **UDS**          | Unix Domain Socket — IPC mechanism for same-host communication                                     |
| **BBO**          | Best Bid and Offer (best bid/ask)                                                                  |
| **Mantissa**     | The integer part of a decimal number in SBE; combined with an exponent to produce the actual value |
| **Auto-culling** | Binance feature that drops stale queued events under load, delivering only the latest              |
| **Ed25519**      | An elliptic-curve signature scheme; required for Binance SBE API keys                              |

---

## 13. References

- [Binance SBE Market Data Streams](https://developers.binance.com/docs/binance-spot-api-docs/sbe-market-data-streams)
- [Binance WebSocket Streams (JSON)](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)
- [SBE Schema (stream_1_0.xml)](https://github.com/binance/binance-spot-api-docs/blob/master/sbe/schemas/stream_1_0.xml)
- [FIX SBE Specification](https://github.com/FIXTradingCommunity/fix-simple-binary-encoding)
