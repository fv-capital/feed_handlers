"""Unit tests for the IPC publisher (UDS on Unix, TCP loopback on Windows)."""

import asyncio
import os
import struct
import sys

import pytest

from binance_sbe.models import BestBidAsk, MsgType
from binance_sbe.config import PublisherConfig
from binance_sbe.publisher import Publisher, _HAS_UNIX_SOCKETS


# -------------------------------------------------------------------
# Helpers — abstract away UDS vs TCP connect
# -------------------------------------------------------------------

async def _connect(publisher: Publisher, uds_path: str):
    """Open a client connection to the publisher, regardless of transport."""
    if publisher._use_uds:
        return await asyncio.open_unix_connection(uds_path)
    else:
        return await asyncio.open_connection("127.0.0.1", publisher.tcp_port)


@pytest.fixture
def uds_path(tmp_path):
    return str(tmp_path / "test_feed.sock")


@pytest.fixture
def publisher_config(uds_path):
    return PublisherConfig(
        uds_path=uds_path,
        tcp_port=0,  # ephemeral port for test isolation
        max_clients=5,
        write_timeout=1.0,
    )


@pytest.fixture
async def publisher(publisher_config):
    pub = Publisher(publisher_config)
    await pub.start()
    yield pub
    await pub.stop()


def _make_event(symbol: str = "BTCUSDT") -> BestBidAsk:
    return BestBidAsk(
        event_time=1_700_000_000_000_000,
        update_id=100,
        bid_price=25.35,
        bid_qty=31.21,
        ask_price=25.36,
        ask_qty=40.66,
        symbol=symbol,
    )


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

class TestPublisher:
    @pytest.mark.asyncio
    async def test_publisher_starts(self, publisher, uds_path):
        if publisher._use_uds:
            assert os.path.exists(uds_path)
        else:
            assert publisher.tcp_port > 0

    @pytest.mark.asyncio
    async def test_client_can_connect(self, publisher, uds_path):
        reader, writer = await _connect(publisher, uds_path)
        await asyncio.sleep(0.05)  # let the accept handler run
        assert publisher.client_count == 1
        writer.close()
        await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_publish_best_bid_ask(self, publisher, uds_path):
        reader, writer = await _connect(publisher, uds_path)
        await asyncio.sleep(0.05)

        event = _make_event("ETHUSDT")
        await publisher.publish(event)

        # Read envelope: msg_type(1) + payload_len(2)
        envelope = await asyncio.wait_for(reader.readexactly(3), timeout=2.0)
        msg_type, payload_len = struct.unpack("<BH", envelope)
        assert msg_type == MsgType.BEST_BID_ASK

        payload = await asyncio.wait_for(
            reader.readexactly(payload_len), timeout=2.0,
        )

        decoded = BestBidAsk.from_bytes(payload)
        assert decoded.symbol == "ETHUSDT"
        assert decoded.bid_price == pytest.approx(25.35)
        assert decoded.ask_price == pytest.approx(25.36)

        writer.close()
        await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_multiple_clients_receive(self, publisher, uds_path):
        readers_writers = []
        for _ in range(3):
            r, w = await _connect(publisher, uds_path)
            readers_writers.append((r, w))

        await asyncio.sleep(0.05)
        assert publisher.client_count == 3

        event = _make_event()
        await publisher.publish(event)

        for r, w in readers_writers:
            envelope = await asyncio.wait_for(r.readexactly(3), timeout=2.0)
            msg_type, _ = struct.unpack("<BH", envelope)
            assert msg_type == MsgType.BEST_BID_ASK

        for _, w in readers_writers:
            w.close()
            await w.wait_closed()

    @pytest.mark.asyncio
    async def test_publish_with_no_clients(self, publisher):
        # Should not raise
        event = _make_event()
        await publisher.publish(event)

    @pytest.mark.asyncio
    async def test_client_disconnect_cleanup(self, publisher, uds_path):
        reader, writer = await _connect(publisher, uds_path)
        await asyncio.sleep(0.05)
        assert publisher.client_count == 1

        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.1)  # let cleanup run
        assert publisher.client_count == 0

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, publisher_config, uds_path):
        pub = Publisher(publisher_config)
        await pub.start()
        if pub._use_uds:
            assert os.path.exists(uds_path)
        await pub.stop()
        if pub._use_uds:
            assert not os.path.exists(uds_path)


class TestBestBidAskSerialization:
    def test_round_trip(self):
        original = _make_event("SOLUSDT")
        data = original.to_bytes()

        # strip envelope
        msg_type, payload_len = struct.unpack("<BH", data[:3])
        assert msg_type == MsgType.BEST_BID_ASK
        payload = data[3:]
        assert len(payload) == payload_len

        restored = BestBidAsk.from_bytes(payload)
        assert restored.event_time == original.event_time
        assert restored.update_id == original.update_id
        assert restored.bid_price == pytest.approx(original.bid_price)
        assert restored.bid_qty == pytest.approx(original.bid_qty)
        assert restored.ask_price == pytest.approx(original.ask_price)
        assert restored.ask_qty == pytest.approx(original.ask_qty)
        assert restored.symbol == original.symbol
