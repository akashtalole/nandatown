# SPDX-License-Identifier: Apache-2.0
"""Tests for the realistic transport: latency, queueing, loss, determinism."""

from __future__ import annotations

import random
import statistics

import pytest
from nest_core.sim.network import NetworkModel, ZeroLatencyNetworkModel
from nest_core.types import AgentId
from nest_plugins_reference.transport.realistic import (
    LinkConfig,
    RealisticNetwork,
    RealisticTransport,
)

A1 = AgentId("a1")
A2 = AgentId("a2")
A3 = AgentId("a3")


# ---------------------------------------------------------------------------
# NetworkModel protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_zero_latency_implements_protocol(self) -> None:
        model: NetworkModel = ZeroLatencyNetworkModel()
        t = model.schedule(A1, A2, 100, 0.5, random.Random(0))
        assert t == 0.5

    def test_realistic_implements_protocol(self) -> None:
        model: NetworkModel = RealisticNetwork(base_latency_ms=10.0, jitter_sigma=0.0)
        t = model.schedule(A1, A2, 100, 0.0, random.Random(0))
        assert isinstance(t, float)
        assert t is not None
        assert t > 0.0


# ---------------------------------------------------------------------------
# Validation: bad construction params should be rejected eagerly
# ---------------------------------------------------------------------------


class TestValidation:
    def test_negative_latency_rejected(self) -> None:
        with pytest.raises(ValueError, match="base_latency_ms"):
            RealisticNetwork(base_latency_ms=-1.0)

    def test_negative_sigma_rejected(self) -> None:
        with pytest.raises(ValueError, match="jitter_sigma"):
            RealisticNetwork(jitter_sigma=-0.1)

    def test_zero_bandwidth_rejected(self) -> None:
        with pytest.raises(ValueError, match="bandwidth_bps"):
            RealisticNetwork(bandwidth_bps=0.0)

    def test_invalid_loss_rejected(self) -> None:
        with pytest.raises(ValueError, match="loss_rate"):
            RealisticNetwork(loss_rate=1.5)

    def test_invalid_queue_cap_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_queue_bytes"):
            RealisticNetwork(max_queue_bytes=-1)


# ---------------------------------------------------------------------------
# Latency / jitter behavior
# ---------------------------------------------------------------------------


class TestLatency:
    def test_fixed_latency_no_jitter(self) -> None:
        net = RealisticNetwork(
            base_latency_ms=10.0,
            jitter_sigma=0.0,
            bandwidth_bps=1e12,  # essentially infinite — no serialization delay
            loss_rate=0.0,
        )
        t = net.schedule(A1, A2, 1, 0.0, random.Random(0))
        assert t == pytest.approx(0.010, abs=1e-9)

    def test_serialization_delay(self) -> None:
        # 1 Mbps, 1000 bytes => 8 ms transmission delay, plus 5 ms base.
        net = RealisticNetwork(
            base_latency_ms=5.0,
            jitter_sigma=0.0,
            bandwidth_bps=1_000_000.0,
            loss_rate=0.0,
        )
        t = net.schedule(A1, A2, 1000, 0.0, random.Random(0))
        assert t == pytest.approx(0.005 + 0.008, abs=1e-9)

    def test_jitter_produces_spread(self) -> None:
        rng = random.Random(123)
        # Reset egress state between samples so we only see jitter, not queueing.
        samples: list[float] = []
        for _ in range(500):
            n = RealisticNetwork(
                base_latency_ms=10.0,
                jitter_sigma=0.4,
                bandwidth_bps=1e12,
                loss_rate=0.0,
            )
            t = n.schedule(A1, A2, 1, 0.0, rng)
            assert t is not None
            samples.append(t)
        assert min(samples) < 0.010 < max(samples)  # both sides of base
        # Lognormal with sigma=0.4 has median = base, mean > base. Spread > 0.
        assert statistics.stdev(samples) > 0.001

    def test_per_link_override(self) -> None:
        net = RealisticNetwork(
            base_latency_ms=5.0,
            jitter_sigma=0.0,
            bandwidth_bps=1e12,
            loss_rate=0.0,
            links={(str(A1), str(A2)): LinkConfig(base_latency_ms=200.0)},
        )
        t_slow = net.schedule(A1, A2, 1, 0.0, random.Random(0))
        # Use a fresh network instance so queueing on A1's egress doesn't
        # bias the comparison.
        net2 = RealisticNetwork(
            base_latency_ms=5.0,
            jitter_sigma=0.0,
            bandwidth_bps=1e12,
            loss_rate=0.0,
            links={(str(A1), str(A2)): LinkConfig(base_latency_ms=200.0)},
        )
        t_fast = net2.schedule(A1, A3, 1, 0.0, random.Random(0))
        assert t_slow == pytest.approx(0.200, abs=1e-9)
        assert t_fast == pytest.approx(0.005, abs=1e-9)


# ---------------------------------------------------------------------------
# Egress queueing — the load curve
# ---------------------------------------------------------------------------


class TestQueueing:
    def test_sequential_sends_queue_up(self) -> None:
        # 1 Mbps, 1000-byte messages => 8 ms per message of serialization.
        # The second message must depart 8 ms after the first.
        net = RealisticNetwork(
            base_latency_ms=0.0,
            jitter_sigma=0.0,
            bandwidth_bps=1_000_000.0,
            loss_rate=0.0,
        )
        rng = random.Random(0)
        t1 = net.schedule(A1, A2, 1000, 0.0, rng)
        t2 = net.schedule(A1, A2, 1000, 0.0, rng)
        t3 = net.schedule(A1, A2, 1000, 0.0, rng)
        assert t1 == pytest.approx(0.008, abs=1e-9)
        assert t2 == pytest.approx(0.016, abs=1e-9)
        assert t3 == pytest.approx(0.024, abs=1e-9)

    def test_idle_link_drains(self) -> None:
        # If the simulation clock moves past busy_until, queueing resets.
        net = RealisticNetwork(
            base_latency_ms=0.0,
            jitter_sigma=0.0,
            bandwidth_bps=1_000_000.0,
            loss_rate=0.0,
        )
        rng = random.Random(0)
        t1 = net.schedule(A1, A2, 1000, 0.0, rng)
        assert t1 == pytest.approx(0.008, abs=1e-9)
        # Big gap — link goes idle. Next send should not stack on top.
        t2 = net.schedule(A1, A2, 1000, 1.0, rng)
        assert t2 == pytest.approx(1.008, abs=1e-9)

    def test_per_sender_queues_are_independent(self) -> None:
        # A1 and A3 both target A2, but each has its own egress queue.
        net = RealisticNetwork(
            base_latency_ms=0.0,
            jitter_sigma=0.0,
            bandwidth_bps=1_000_000.0,
            loss_rate=0.0,
        )
        rng = random.Random(0)
        t_a1 = net.schedule(A1, A2, 1000, 0.0, rng)
        t_a3 = net.schedule(A3, A2, 1000, 0.0, rng)
        assert t_a1 == pytest.approx(0.008, abs=1e-9)
        assert t_a3 == pytest.approx(0.008, abs=1e-9)

    def test_queue_overflow_drops(self) -> None:
        # Tiny cap: 1500-byte budget. First 1000 fits; second 1000 overflows.
        net = RealisticNetwork(
            base_latency_ms=0.0,
            jitter_sigma=0.0,
            bandwidth_bps=1_000_000.0,
            loss_rate=0.0,
            max_queue_bytes=1500,
        )
        rng = random.Random(0)
        assert net.schedule(A1, A2, 1000, 0.0, rng) is not None
        assert net.schedule(A1, A2, 1000, 0.0, rng) is None
        assert net.stats["dropped_queue_full"] == 1


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------


class TestLoss:
    def test_zero_loss_never_drops(self) -> None:
        net = RealisticNetwork(loss_rate=0.0, jitter_sigma=0.0)
        rng = random.Random(0)
        for _ in range(200):
            assert net.schedule(A1, A2, 1, 0.0, rng) is not None
        assert net.stats["dropped_loss"] == 0

    def test_full_loss_always_drops(self) -> None:
        net = RealisticNetwork(loss_rate=1.0)
        rng = random.Random(0)
        for _ in range(50):
            assert net.schedule(A1, A2, 1, 0.0, rng) is None
        assert net.stats["dropped_loss"] == 50

    def test_loss_rate_approximate(self) -> None:
        net = RealisticNetwork(loss_rate=0.10, jitter_sigma=0.0, bandwidth_bps=1e12)
        rng = random.Random(42)
        n = 5000
        dropped = sum(1 for _ in range(n) if net.schedule(A1, A2, 1, 0.0, rng) is None)
        # 99% CI for binomial with p=0.1, n=5000 is roughly ±25 around 500
        assert 400 < dropped < 600

    def test_per_link_loss_override(self) -> None:
        net = RealisticNetwork(
            loss_rate=0.0,
            links={(str(A1), str(A2)): LinkConfig(loss_rate=1.0)},
        )
        rng = random.Random(0)
        # Hot link: every message dropped.
        for _ in range(20):
            assert net.schedule(A1, A2, 1, 0.0, rng) is None
        # Other link: never dropped.
        for _ in range(20):
            assert net.schedule(A1, A3, 1, 0.0, rng) is not None


# ---------------------------------------------------------------------------
# Determinism — same RNG seed => identical trace
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_schedule(self) -> None:
        def run() -> list[float | None]:
            net = RealisticNetwork(
                base_latency_ms=10.0,
                jitter_sigma=0.4,
                bandwidth_bps=1_000_000.0,
                loss_rate=0.05,
            )
            rng = random.Random(2024)
            out: list[float | None] = []
            for i in range(500):
                size = 100 + (i % 10) * 50
                out.append(net.schedule(A1, A2, size, i * 0.001, rng))
            return out

        assert run() == run()

    def test_different_seed_diverges(self) -> None:
        net_a = RealisticNetwork(
            base_latency_ms=10.0,
            jitter_sigma=0.4,
            bandwidth_bps=1e6,
            loss_rate=0.05,
        )
        net_b = RealisticNetwork(
            base_latency_ms=10.0,
            jitter_sigma=0.4,
            bandwidth_bps=1e6,
            loss_rate=0.05,
        )
        a = [net_a.schedule(A1, A2, 100, 0.0, random.Random(1)) for _ in range(100)]
        b = [net_b.schedule(A1, A2, 100, 0.0, random.Random(2)) for _ in range(100)]
        assert a != b


# ---------------------------------------------------------------------------
# from_config — scenario YAML wiring
# ---------------------------------------------------------------------------


class TestFromConfig:
    def test_defaults_when_empty(self) -> None:
        net = RealisticNetwork.from_config({})
        t = net.schedule(A1, A2, 1, 0.0, random.Random(0))
        # Default base 5 ms, default bandwidth huge → ~ 5 ms with jitter.
        assert t is not None
        assert 0.001 < t < 0.050

    def test_parses_links_list(self) -> None:
        net = RealisticNetwork.from_config(
            {
                "base_latency_ms": 1.0,
                "jitter_sigma": 0.0,
                "bandwidth_bps": 1e12,
                "links": [
                    {"from": str(A1), "to": str(A2), "base_latency_ms": 100.0},
                ],
            }
        )
        t = net.schedule(A1, A2, 1, 0.0, random.Random(0))
        assert t == pytest.approx(0.100, abs=1e-9)

    def test_ignores_malformed_link(self) -> None:
        net = RealisticNetwork.from_config(
            {
                "base_latency_ms": 2.0,
                "jitter_sigma": 0.0,
                "bandwidth_bps": 1e12,
                "links": ["not a dict", {"from": str(A1)}],
            },  # missing 'to'
        )
        t = net.schedule(A1, A2, 1, 0.0, random.Random(0))
        assert t == pytest.approx(0.002, abs=1e-9)


# ---------------------------------------------------------------------------
# Standalone RealisticTransport — non-simulator usage
# ---------------------------------------------------------------------------


class TestStandaloneTransport:
    @pytest.mark.asyncio
    async def test_send_then_receive_after_advance(self) -> None:
        net = RealisticNetwork(
            base_latency_ms=5.0,
            jitter_sigma=0.0,
            bandwidth_bps=1e12,
            loss_rate=0.0,
        )
        bus: dict[AgentId, list] = {}
        t1 = RealisticTransport(A1, net, bus, rng=random.Random(0))
        t2 = RealisticTransport(A2, net, bus, rng=random.Random(0))

        ok = await t1.send(A2, b"hello")
        assert ok is True

        # Not ready at t=0.
        with pytest.raises(LookupError):
            await t2.receive()

        # Advance past delivery.
        t2.advance(0.010)
        sender, payload = await t2.receive()
        assert sender == A1
        assert payload == b"hello"

    @pytest.mark.asyncio
    async def test_drop_returns_false(self) -> None:
        net = RealisticNetwork(loss_rate=1.0)
        bus: dict[AgentId, list] = {}
        t1 = RealisticTransport(A1, net, bus, rng=random.Random(0))
        # registering t2 so the bus knows about it
        RealisticTransport(A2, net, bus, rng=random.Random(0))

        ok = await t1.send(A2, b"hello")
        assert ok is False

    @pytest.mark.asyncio
    async def test_broadcast_excludes_self(self) -> None:
        net = RealisticNetwork(
            base_latency_ms=0.0,
            jitter_sigma=0.0,
            bandwidth_bps=1e12,
            loss_rate=0.0,
        )
        bus: dict[AgentId, list] = {}
        t1 = RealisticTransport(A1, net, bus, rng=random.Random(0))
        RealisticTransport(A2, net, bus, rng=random.Random(0))
        RealisticTransport(A3, net, bus, rng=random.Random(0))

        accepted = await t1.broadcast(b"hello")
        assert accepted == 2  # A2 and A3, not self

    def test_clock_cannot_go_backwards(self) -> None:
        net = RealisticNetwork()
        bus: dict[AgentId, list] = {}
        t1 = RealisticTransport(A1, net, bus, clock=5.0)
        with pytest.raises(ValueError, match="backwards"):
            t1.advance(3.0)
