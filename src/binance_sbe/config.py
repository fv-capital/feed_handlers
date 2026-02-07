"""Configuration loader — reads YAML config and exposes typed settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Typed config sections
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BinanceConfig:
    api_key: str = ""
    ws_base_url: str = "wss://stream-sbe.binance.com:9443"
    symbols: list[str] = field(default_factory=lambda: ["btcusdt"])
    streams: list[str] = field(default_factory=lambda: ["bestBidAsk"])


@dataclass(slots=True)
class ConnectionConfig:
    reconnect_delay_initial: float = 1.0
    reconnect_delay_max: float = 60.0
    reconnect_delay_multiplier: float = 2.0
    max_reconnect_attempts: int = 0  # 0 = infinite
    preemptive_reconnect_hours: float = 23.5


@dataclass(slots=True)
class PublisherConfig:
    uds_path: str = "/tmp/binance_feed.sock"
    tcp_port: int = 0  # 0 = ephemeral; used on Windows where UDS is unavailable
    max_clients: int = 10
    write_timeout: float = 0.1


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "console"  # "json" or "console"


@dataclass(slots=True)
class AppConfig:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _merge(dc_cls: type, raw: dict[str, Any] | None):
    """Create a dataclass instance from a dict, ignoring unknown keys."""
    if raw is None:
        return dc_cls()
    known = {f.name for f in dc_cls.__dataclass_fields__.values()}
    return dc_cls(**{k: v for k, v in raw.items() if k in known})


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load configuration from a YAML file.

    Resolution order for the config path:
        1. Explicit *path* argument
        2. ``FEED_HANDLER_CONFIG`` environment variable
        3. ``config/config.yaml`` relative to the repo root
    """
    if path is None:
        path = os.environ.get("FEED_HANDLER_CONFIG")
    if path is None:
        # Default: repo_root/config/config.yaml
        path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    return AppConfig(
        binance=_merge(BinanceConfig, raw.get("binance")),
        connection=_merge(ConnectionConfig, raw.get("connection")),
        publisher=_merge(PublisherConfig, raw.get("publisher")),
        logging=_merge(LoggingConfig, raw.get("logging")),
    )
