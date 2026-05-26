# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``netem`` standalone transport + plugin registration."""

from __future__ import annotations

import asyncio
import time

import pytest
from nest_core.plugins import PluginRegistry
from nest_core.types import AgentId


class TestNetemPluginResolution:
    def test_registry_resolves_netem(self) -> None:
        reg = PluginRegistry()
        cls = reg.resolve("transport", "netem")
        assert cls is not None
        assert cls.__name__ == "StandaloneNetemTransport"

    def test_list_includes_netem(self) -> None:
        reg = PluginRegistry()
        names = {n for (_, n) in reg.list_plugins("transport")}
        assert "netem" in names
        assert "in_memory" in names


class TestStandaloneNetem:
    @pytest.mark.asyncio
    async def test_delay_observable_via_asyncio_sleep(self) -> None:
        from nest_plugins_reference.transport.in_memory import InMemoryNetwork
        from nest_plugins_reference.transport.netem import (
            StandaloneNetemTransport,
            make_delay_model,
        )

        network = InMemoryNetwork()
        model = make_delay_model(
            {"latency": {"kind": "constant", "p50_ms": 30}},
            seed=0,
        )
        t1 = StandaloneNetemTransport(AgentId("a1"), network, model)
        StandaloneNetemTransport(AgentId("a2"), network, model)

        started = time.perf_counter()
        await t1.send(AgentId("a2"), b"hello")
        elapsed = time.perf_counter() - started
        # The 30ms delay must materialise via asyncio.sleep.  Allow a
        # generous lower bound to keep the test stable under CI noise.
        assert elapsed >= 0.020, f"expected >=20ms wait, got {elapsed * 1000:.2f}ms"

    @pytest.mark.asyncio
    async def test_drop_returns_without_delivery(self) -> None:
        from nest_plugins_reference.transport.in_memory import InMemoryNetwork
        from nest_plugins_reference.transport.netem import (
            StandaloneNetemTransport,
            make_delay_model,
        )

        network = InMemoryNetwork()
        model = make_delay_model(
            {
                "latency": {"kind": "constant", "p50_ms": 0},
                "drop_prob": 1.0,
            },
            seed=0,
        )
        t1 = StandaloneNetemTransport(AgentId("a1"), network, model)
        t2 = StandaloneNetemTransport(AgentId("a2"), network, model)

        await t1.send(AgentId("a2"), b"hello")
        # Receive must time out — nothing was delivered.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(t2.receive(), timeout=0.05)
