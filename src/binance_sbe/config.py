"""Configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class BinanceConfig:
    base_url: str = "wss://stream.binance.com:9443"
    symbols: list[str] = field(default_factory=lambda: ["btcusdt"])
    streams: list[str] = field(default_factory=lambda: ["bookTicker"])


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    max_retries: int = 10
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 60.0
    preemptive_reconnect_hours: float = 23.5


@dataclass(frozen=True, slots=True)
class PublisherConfig:
    uds_path: str = "/tmp/binance_feed.sock"


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    level: str = "info"
    format: str = "console"  # "console" | "json"


@dataclass(frozen=True, slots=True)
class AppConfig:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    connection: ConnectionConfig = field(default_factory=ConnectionConfig)
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _build_dataclass(cls: type, raw: dict[str, Any] | None):
    """Construct a dataclass from a dict, ignoring unknown keys."""
    if raw is None:
        return cls()
    valid = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in raw.items() if k in valid})


def load_config(path: str | None = None) -> AppConfig:
    """Load config from YAML file.  Falls back to defaults."""
    if path is None:
        path = str(Path(__file__).resolve().parents[2] / "config" / "config.yaml")

    cfg_path = Path(path)
    if not cfg_path.exists():
        return AppConfig()

    with open(cfg_path) as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    return AppConfig(
        binance=_build_dataclass(BinanceConfig, raw.get("binance")),
        connection=_build_dataclass(ConnectionConfig, raw.get("connection")),
        publisher=_build_dataclass(PublisherConfig, raw.get("publisher")),
        logging=_build_dataclass(LoggingConfig, raw.get("logging")),
    )