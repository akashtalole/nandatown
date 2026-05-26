# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for wiring the realistic transport through the ScenarioRunner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from nest_core.metrics import compute_metrics
from nest_core.runner import ScenarioRunner
from nest_core.scenario import ScenarioConfig


def _read_trace(p: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in p.read_text().splitlines()]


class TestRealisticTransportWiring:
    @pytest.mark.asyncio
    async def test_scenario_picks_up_realistic_transport(self, tmp_path: Path) -> None:
        """When ``layers.transport: realistic`` is set, the simulator's
        virtual clock must actually advance — proving that the network
        model was installed and consulted."""
        trace_file = tmp_path / "trace.jsonl"
        config = ScenarioConfig.from_dict(
            {
                "name": "rt-test",
                "seed": 42,
                "agents": {
                    "count": 6,
                    "roles": [
                        {"name": "buyer", "count": 3},
                        {"name": "seller", "count": 3},
                    ],
                },
                "layers": {
                    "transport": "realistic",
                    "transport_config": {
                        "base_latency_ms": 20.0,
                        "jitter_sigma": 0.0,
                        "bandwidth_bps": 100_000_000.0,
                        "loss_rate": 0.0,
                    },
                },
                "task": {"type": "marketplace", "config": {"rounds": 2}},
                "duration": "ticks: 5000",
                "metrics": ["mean_latency", "duration", "message_count"],
                "output": {"trace": str(trace_file)},
            }
        )

        runner = ScenarioRunner(config)
        await runner.run()

        events = _read_trace(trace_file)
        # All ``receive`` events should now have ts > 0 because every hop
        # costs 20 ms.
        receives = [e for e in events if e["kind"] == "receive"]
        assert receives, "scenario produced no receive events"
        assert all(e["ts"] > 0 for e in receives)

        metrics = runner.metrics
        assert metrics["mean_latency"] >= 0.020 - 1e-9
        assert metrics["duration"] > 0.0

    @pytest.mark.asyncio
    async def test_scenario_loss_increases_drop_count(self, tmp_path: Path) -> None:
        """Setting a non-zero loss_rate on the realistic transport should
        produce ``dropped`` trace events with reason=='network'."""
        trace_file = tmp_path / "trace.jsonl"
        config = ScenarioConfig.from_dict(
            {
                "name": "loss-test",
                "seed": 7,
                "agents": {
                    "count": 6,
                    "roles": [
                        {"name": "buyer", "count": 3},
                        {"name": "seller", "count": 3},
                    ],
                },
                "layers": {
                    "transport": "realistic",
                    "transport_config": {
                        "base_latency_ms": 1.0,
                        "jitter_sigma": 0.0,
                        "bandwidth_bps": 1e12,
                        "loss_rate": 0.5,  # half of messages drop at the link
                    },
                },
                "task": {"type": "marketplace", "config": {"rounds": 3}},
                "duration": "ticks: 5000",
                "output": {"trace": str(trace_file)},
            }
        )

        runner = ScenarioRunner(config)
        await runner.run()

        events = _read_trace(trace_file)
        network_drops = [
            e for e in events if e["kind"] == "dropped" and e.get("reason") == "network"
        ]
        assert network_drops, "expected network-level drops with loss_rate=0.5"

    @pytest.mark.asyncio
    async def test_in_memory_default_still_zero_latency(self, tmp_path: Path) -> None:
        """Backwards-compat guard: scenarios that don't pick the realistic
        transport must keep the historical zero-latency behavior."""
        trace_file = tmp_path / "trace.jsonl"
        config = ScenarioConfig.from_dict(
            {
                "name": "default-rt",
                "seed": 42,
                "agents": {
                    "count": 6,
                    "roles": [
                        {"name": "buyer", "count": 3},
                        {"name": "seller", "count": 3},
                    ],
                },
                "task": {"type": "marketplace", "config": {"rounds": 2}},
                "duration": "ticks: 5000",
                "metrics": ["mean_latency", "duration"],
                "output": {"trace": str(trace_file)},
            }
        )

        runner = ScenarioRunner(config)
        await runner.run()

        events = _read_trace(trace_file)
        receives = [e for e in events if e["kind"] == "receive"]
        # All receives happen at ts==0 with the default in-memory transport.
        assert all(e["ts"] == 0 for e in receives)
        assert runner.metrics.get("mean_latency", 0.0) == 0.0

    @pytest.mark.asyncio
    async def test_realistic_run_is_deterministic(self, tmp_path: Path) -> None:
        """Same seed + same realistic-network config => byte-identical trace."""
        traces: list[str] = []
        for i in range(2):
            trace_file = tmp_path / f"trace-{i}.jsonl"
            config = ScenarioConfig.from_dict(
                {
                    "name": "det",
                    "seed": 99,
                    "agents": {
                        "count": 6,
                        "roles": [
                            {"name": "buyer", "count": 3},
                            {"name": "seller", "count": 3},
                        ],
                    },
                    "layers": {
                        "transport": "realistic",
                        "transport_config": {
                            "base_latency_ms": 5.0,
                            "jitter_sigma": 0.4,
                            "bandwidth_bps": 1_000_000.0,
                            "loss_rate": 0.05,
                        },
                    },
                    "task": {"type": "marketplace", "config": {"rounds": 2}},
                    "duration": "ticks: 3000",
                    "output": {"trace": str(trace_file)},
                }
            )
            await ScenarioRunner(config).run()
            traces.append(trace_file.read_text())

        assert traces[0] == traces[1]

    def test_metrics_compute_handles_realistic_trace(self, tmp_path: Path) -> None:
        """A trace with positive timestamps should produce non-zero latency
        and throughput, exercising the previously-dormant code paths."""
        import asyncio

        trace_file = tmp_path / "trace.jsonl"
        config = ScenarioConfig.from_dict(
            {
                "name": "metrics-test",
                "seed": 1,
                "agents": {
                    "count": 4,
                    "roles": [
                        {"name": "buyer", "count": 2},
                        {"name": "seller", "count": 2},
                    ],
                },
                "layers": {
                    "transport": "realistic",
                    "transport_config": {
                        "base_latency_ms": 50.0,
                        "jitter_sigma": 0.0,
                        "bandwidth_bps": 1e12,
                        "loss_rate": 0.0,
                    },
                },
                "task": {"type": "marketplace", "config": {"rounds": 2}},
                "duration": "ticks: 5000",
                "output": {"trace": str(trace_file)},
            }
        )

        asyncio.run(ScenarioRunner(config).run())

        m = compute_metrics(
            trace_file,
            ["mean_latency", "throughput", "duration", "message_count"],
        )
        # Base latency 50 ms + a negligible serialization delay at 1 Tbps.
        assert m["mean_latency"] == pytest.approx(0.050, abs=1e-3)
        assert m["mean_latency"] >= 0.050
        assert m["duration"] > 0
        assert m["throughput"] > 0
        assert m["message_count"] > 0
