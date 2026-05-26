# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the ``netem`` transport + latency metrics + SLO validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nest_core.metrics import compute_metrics
from nest_core.runner import ScenarioRunner
from nest_core.scenario import ScenarioConfig
from nest_core.sim.agent import AgentContext, StateMachineAgent
from nest_core.sim.delay_model import (
    DelayModel,
    DelayModelConfig,
    LatencyDistribution,
)
from nest_core.sim.simulator import Simulator
from nest_core.types import AgentId
from nest_core.validators import validate_latency_slo, validate_trace


class _Pinger(StateMachineAgent):
    """Sends N pings to a fixed peer at t=0 then stays silent."""

    def __init__(self, peer: AgentId, n: int) -> None:
        self._peer = peer
        self._n = n

    async def on_start(self, ctx: AgentContext) -> None:
        for i in range(self._n):
            await ctx.send(self._peer, f"ping-{i}".encode())


class _Echo(StateMachineAgent):
    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        await ctx.send(sender, payload)


# ---------------------------------------------------------------------------
# Simulator-level integration
# ---------------------------------------------------------------------------


class TestSimulatorWithNetem:
    @pytest.mark.asyncio
    async def test_constant_delay_visible_in_trace(self, tmp_path: Path) -> None:
        """Every send produces a deliver event 10ms later."""
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=10),
        )
        model = DelayModel.from_config(cfg, seed=0)
        trace = tmp_path / "t.jsonl"

        sim = Simulator(seed=0, trace_path=trace, delay_model=model)
        sim.add_agent(AgentId("a"), _Pinger(AgentId("b"), n=5))
        sim.add_agent(AgentId("b"), _Echo())
        await sim.run(max_ticks=100)

        events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
        receives = [e for e in events if e.get("kind") == "receive"]
        sends = {e["corr"]: e for e in events if e.get("kind") == "send"}
        assert receives, "no receives recorded"
        for rec in receives:
            send = sends.get(rec["corr"])
            assert send is not None
            # Constant 10ms => 0.01s, plus FIFO 1us bumps for repeated sends.
            delta = rec["ts"] - send["ts"]
            assert delta >= 0.01 - 1e-9, f"{delta=} too small"
            assert delta < 0.020, f"{delta=} too large"

    @pytest.mark.asyncio
    async def test_zero_delay_when_no_model(self, tmp_path: Path) -> None:
        """Backwards compat: no model => zero delay, mean_latency = 0."""
        trace = tmp_path / "t.jsonl"
        sim = Simulator(seed=0, trace_path=trace)
        sim.add_agent(AgentId("a"), _Pinger(AgentId("b"), n=3))
        sim.add_agent(AgentId("b"), _Echo())
        await sim.run(max_ticks=100)

        results = compute_metrics(trace, ["mean_latency"])
        assert results["mean_latency"] == 0.0

    @pytest.mark.asyncio
    async def test_netem_drop_logs_dropped(self, tmp_path: Path) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=1),
            drop_prob=1.0,
        )
        model = DelayModel.from_config(cfg, seed=0)
        trace = tmp_path / "t.jsonl"
        sim = Simulator(seed=0, trace_path=trace, delay_model=model)
        sim.add_agent(AgentId("a"), _Pinger(AgentId("b"), n=4))
        sim.add_agent(AgentId("b"), _Echo())
        await sim.run(max_ticks=100)

        events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
        kinds = [e.get("kind") for e in events]
        assert kinds.count("dropped") == 4
        # Every drop should be tagged with reason=netem so downstream tooling
        # can tell "wire-drop" from "policy-drop".
        for ev in events:
            if ev.get("kind") == "dropped":
                assert ev.get("reason") == "netem"

    @pytest.mark.asyncio
    async def test_determinism_across_runs(self, tmp_path: Path) -> None:
        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="lognormal", p50_ms=10, p99_ms=100),
            jitter_ms=2,
        )
        traces: list[str] = []
        for _ in range(2):
            model = DelayModel.from_config(cfg, seed=99)
            trace = tmp_path / f"t-{len(traces)}.jsonl"
            sim = Simulator(seed=99, trace_path=trace, delay_model=model)
            sim.add_agent(AgentId("a"), _Pinger(AgentId("b"), n=20))
            sim.add_agent(AgentId("b"), _Echo())
            await sim.run(max_ticks=500)
            traces.append(trace.read_text())
        assert traces[0] == traces[1]


# ---------------------------------------------------------------------------
# Runner / scenario integration
# ---------------------------------------------------------------------------


class TestScenarioRunnerWithNetem:
    @pytest.mark.asyncio
    async def test_marketplace_with_netem_has_real_latency(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "m.jsonl"
        config = ScenarioConfig.from_dict(
            {
                "name": "netem-marketplace",
                "seed": 1,
                "agents": {
                    "count": 10,
                    "roles": [
                        {"name": "buyer", "count": 5},
                        {"name": "seller", "count": 5},
                    ],
                },
                "layers": {"transport": "netem"},
                "transport": {
                    "latency": {"kind": "constant", "p50_ms": 5},
                },
                "task": {"type": "marketplace", "config": {"rounds": 3}},
                "duration": "ticks: 2000",
                "metrics": [
                    "mean_latency",
                    "p50_latency",
                    "p95_latency",
                    "p99_latency",
                    "max_latency",
                    "message_count",
                ],
                "output": {"trace": str(trace_file)},
            }
        )
        runner = ScenarioRunner(config)
        await runner.run()
        # 5ms constant => 0.005 +/- FIFO microsecond bumps
        assert runner.metrics["p50_latency"] >= 0.005 - 1e-6
        assert runner.metrics["mean_latency"] > 0
        assert runner.metrics["p99_latency"] >= runner.metrics["p50_latency"]
        assert runner.metrics["max_latency"] >= runner.metrics["p99_latency"]
        assert runner.metrics["message_count"] > 0

    @pytest.mark.asyncio
    async def test_slo_pass_and_fail(self, tmp_path: Path) -> None:
        trace_file = tmp_path / "m.jsonl"
        base = {
            "name": "slo-test",
            "seed": 2,
            "agents": {
                "count": 4,
                "roles": [
                    {"name": "buyer", "count": 2},
                    {"name": "seller", "count": 2},
                ],
            },
            "layers": {"transport": "netem"},
            "transport": {"latency": {"kind": "constant", "p50_ms": 10}},
            "task": {"type": "marketplace", "config": {"rounds": 2}},
            "duration": "ticks: 500",
            "output": {"trace": str(trace_file)},
        }
        # SLO that should pass: budget = 100ms
        cfg_pass = ScenarioConfig.from_dict({**base, "slo": {"p99_latency": 0.1}})
        runner = ScenarioRunner(cfg_pass)
        await runner.run()
        assert runner.validations, "expected SLO validations"
        assert all(v["passed"] for v in runner.validations), runner.validations
        # SLO that should fail: budget = 1ms but latency is 10ms
        cfg_fail = ScenarioConfig.from_dict({**base, "slo": {"p99_latency": 0.001}})
        runner = ScenarioRunner(cfg_fail)
        await runner.run()
        assert any(not v["passed"] for v in runner.validations), runner.validations


class TestSloValidator:
    def test_min_delivery_rate(self) -> None:
        events = [
            {"ts": 0, "kind": "send", "corr": "c1"},
            {"ts": 0.01, "kind": "receive", "corr": "c1"},
            {"ts": 0, "kind": "send", "corr": "c2"},
            # c2 never received -> 50% delivery
            {"ts": 0, "kind": "send", "corr": "c3"},
            {"ts": 0.01, "kind": "receive", "corr": "c3"},
        ]
        res = validate_latency_slo(events, {"min_delivery_rate": 0.9})
        assert any(not r.passed and "delivery_rate" in r.name for r in res)
        res2 = validate_latency_slo(events, {"min_delivery_rate": 0.5})
        assert all(r.passed for r in res2)

    def test_empty_trace_fails_when_budget_set(self) -> None:
        res = validate_latency_slo([], {"p99_latency": 0.1})
        assert all(not r.passed for r in res)

    def test_validate_trace_with_slo(self, tmp_path: Path) -> None:
        trace = tmp_path / "t.jsonl"
        events = [
            {"ts": 0, "kind": "send", "corr": "c1"},
            {"ts": 0.005, "kind": "receive", "corr": "c1"},
            {"ts": 0, "kind": "send", "corr": "c2"},
            {"ts": 0.05, "kind": "receive", "corr": "c2"},
        ]
        trace.write_text("\n".join(json.dumps(e) for e in events))
        results = validate_trace(trace, "marketplace", slo={"p99_latency": 0.1})
        names = [r.name for r in results]
        assert "slo_p99_latency" in names
