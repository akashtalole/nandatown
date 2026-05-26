# SPDX-License-Identifier: Apache-2.0
"""Tests for the netem-style :class:`DelayModel`."""

from __future__ import annotations

import math
from typing import Any

import pytest
from nest_core.sim.delay_model import (
    DelayModel,
    DelayModelConfig,
    LatencyDistribution,
)
from nest_core.types import AgentId


def _percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    rank = max(1, math.ceil(q / 100.0 * len(s)))
    return s[rank - 1]


class TestLatencyDistribution:
    def test_constant_returns_p50(self) -> None:
        cfg = DelayModelConfig(latency=LatencyDistribution(kind="constant", p50_ms=10))
        model = DelayModel.from_config(cfg, seed=1)
        samples = model.sample_latencies_ms(50)
        assert all(abs(s - 10) < 1e-9 for s in samples)

    def test_uniform_within_bounds(self) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="uniform", p50_ms=10, min_ms=5, max_ms=20)
        )
        model = DelayModel.from_config(cfg, seed=1)
        samples = model.sample_latencies_ms(2000)
        assert min(samples) >= 5.0
        assert max(samples) <= 20.0
        # Mean should be near 12.5 within a few stddev
        mean = sum(samples) / len(samples)
        assert 11.0 < mean < 14.0

    def test_lognormal_percentiles(self) -> None:
        cfg = DelayModelConfig(latency=LatencyDistribution(kind="lognormal", p50_ms=20, p99_ms=200))
        model = DelayModel.from_config(cfg, seed=42)
        samples = model.sample_latencies_ms(20_000)
        # Allow 15% slack — log-normal tails are noisy at 20k samples.
        assert abs(_percentile(samples, 50) - 20.0) / 20.0 < 0.15
        assert abs(_percentile(samples, 99) - 200.0) / 200.0 < 0.15

    def test_lognormal_requires_p99(self) -> None:
        with pytest.raises(ValueError, match="p99_ms"):
            LatencyDistribution(kind="lognormal", p50_ms=20)

    def test_lognormal_rejects_p99_below_p50(self) -> None:
        with pytest.raises(ValueError, match="p99_ms"):
            LatencyDistribution(kind="lognormal", p50_ms=200, p99_ms=20)

    def test_uniform_rejects_inverted_bounds(self) -> None:
        with pytest.raises(ValueError, match="min_ms"):
            LatencyDistribution(kind="uniform", p50_ms=5, min_ms=20, max_ms=10)


class TestDeterminism:
    def test_same_seed_same_samples(self) -> None:
        cfg = DelayModelConfig(latency=LatencyDistribution(kind="lognormal", p50_ms=10, p99_ms=100))
        a = DelayModel.from_config(cfg, seed=7)
        b = DelayModel.from_config(cfg, seed=7)
        for _ in range(100):
            sender, receiver = AgentId("a"), AgentId("b")
            da = a.compute_delay(sender, receiver, 16)
            db = b.compute_delay(sender, receiver, 16)
            assert da == db

    def test_different_seed_diverges(self) -> None:
        cfg = DelayModelConfig(latency=LatencyDistribution(kind="lognormal", p50_ms=10, p99_ms=100))
        a = DelayModel.from_config(cfg, seed=1)
        b = DelayModel.from_config(cfg, seed=2)
        seq_a = [a.compute_delay(AgentId("x"), AgentId("y"), 16) for _ in range(50)]
        seq_b = [b.compute_delay(AgentId("x"), AgentId("y"), 16) for _ in range(50)]
        assert seq_a != seq_b


class TestDropAndOrdering:
    def test_drop_prob_zero_never_drops(self) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=1),
            drop_prob=0.0,
        )
        model = DelayModel.from_config(cfg, seed=0)
        for _ in range(500):
            assert model.compute_delay(AgentId("a"), AgentId("b"), 1) is not None

    def test_drop_prob_one_always_drops(self) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=1),
            drop_prob=1.0,
        )
        model = DelayModel.from_config(cfg, seed=0)
        for _ in range(50):
            assert model.compute_delay(AgentId("a"), AgentId("b"), 1) is None

    def test_drop_prob_in_expected_range(self) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=1),
            drop_prob=0.25,
        )
        model = DelayModel.from_config(cfg, seed=0)
        n = 10_000
        drops = sum(
            1 for _ in range(n) if model.compute_delay(AgentId("a"), AgentId("b"), 1) is None
        )
        # Allow 1.5% slack
        assert 0.235 <= drops / n <= 0.265

    def test_fifo_preservation_without_reorder(self) -> None:
        """With reorder_prob=0 and per-message jitter, delays must be monotonic per link."""
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="lognormal", p50_ms=20, p99_ms=200),
            jitter_ms=5,
        )
        model = DelayModel.from_config(cfg, seed=99)
        prev = -1.0
        for _ in range(500):
            d = model.compute_delay(AgentId("a"), AgentId("b"), 64)
            assert d is not None
            assert d > prev, f"FIFO violated: {d} <= {prev}"
            prev = d

    def test_independent_links_have_independent_fifo(self) -> None:
        cfg = DelayModelConfig(latency=LatencyDistribution(kind="constant", p50_ms=10))
        model = DelayModel.from_config(cfg, seed=0)
        # (a -> b) at 10ms repeated should still be monotonic on link
        d1 = model.compute_delay(AgentId("a"), AgentId("b"), 1)
        d2 = model.compute_delay(AgentId("a"), AgentId("b"), 1)
        # On a different link, the first message can be at 10ms again — not
        # pinned to the previous delay.
        d3 = model.compute_delay(AgentId("c"), AgentId("d"), 1)
        assert d1 is not None and d2 is not None and d3 is not None
        assert d2 > d1
        assert abs(d3 - 0.01) < 1e-6


class TestBandwidth:
    def test_serialization_delay_proportional_to_size(self) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=0),
            bandwidth_kbps=1000,  # 1 Mbps
        )
        model = DelayModel.from_config(cfg, seed=0)
        # 1000 bytes = 8000 bits / 1000 kbps = 8 ms
        d = model.compute_delay(AgentId("a"), AgentId("b"), 1000)
        assert d is not None
        assert abs(d - 0.008) < 1e-6

    def test_bandwidth_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="bandwidth_kbps"):
            DelayModelConfig(bandwidth_kbps=0)


class TestConfigFromDict:
    def test_round_trip(self) -> None:
        data: dict[str, Any] = {
            "latency": {"kind": "lognormal", "p50_ms": 5, "p99_ms": 50},
            "jitter_ms": 1,
            "bandwidth_kbps": 100,
            "reorder_prob": 0.0,
            "drop_prob": 0.0,
        }
        cfg = DelayModelConfig.from_dict(data)
        assert cfg.latency.kind == "lognormal"
        assert cfg.latency.p50_ms == 5
        assert cfg.latency.p99_ms == 50
        assert cfg.jitter_ms == 1
        assert cfg.bandwidth_kbps == 100

    def test_empty_dict_produces_noop_model(self) -> None:
        cfg = DelayModelConfig.from_dict({})
        model = DelayModel.from_config(cfg, seed=0)
        d = model.compute_delay(AgentId("a"), AgentId("b"), 16)
        assert d is not None
        assert abs(d) < 1e-9

    def test_validation_negative_jitter(self) -> None:
        with pytest.raises(ValueError, match="jitter_ms"):
            DelayModelConfig(jitter_ms=-1)

    def test_validation_prob_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="reorder_prob"):
            DelayModelConfig(reorder_prob=1.5)
        with pytest.raises(ValueError, match="drop_prob"):
            DelayModelConfig(drop_prob=-0.1)
