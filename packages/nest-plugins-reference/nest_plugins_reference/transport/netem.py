# SPDX-License-Identifier: Apache-2.0
"""``netem`` transport — netem-style link emulation for NEST Tier 1.

The plugin itself is intentionally a thin re-export of the simulator's
in-memory transport: the *actual* per-link delay, jitter, bandwidth and
reorder behaviour lives in :class:`nest_core.sim.delay_model.DelayModel`,
which is wired into :class:`nest_core.sim.transport.InMemoryTransport` by
the runner when ``layers.transport == "netem"``.

We expose two things here:

* :class:`StandaloneNetemTransport` — a Tier-2-style standalone transport
  that injects delays using ``asyncio.sleep``.  Useful for stress-testing
  shell agents against realistic-ish latency profiles.
* :func:`make_delay_model` — convenience builder for tests / direct API
  use.

For Tier 1 scenarios, just set ``layers.transport: netem`` and add a
``transport:`` block; the runner takes care of the rest.

Example (YAML)::

    layers:
      transport: netem
    transport:
      latency: { kind: lognormal, p50_ms: 20, p99_ms: 200 }
      jitter_ms: 2
      bandwidth_kbps: 1000

Example (Python)::

    from nest_plugins_reference.transport.netem import (
        make_delay_model,
        StandaloneNetemTransport,
    )

    model = make_delay_model({"latency": {"kind": "constant", "p50_ms": 5}})
"""

from __future__ import annotations

import asyncio
from typing import Any

from nest_core.sim.delay_model import DelayModel, DelayModelConfig
from nest_core.types import AgentId, TransportCapabilities

from .in_memory import InMemoryNetwork


def make_delay_model(config: dict[str, Any] | None = None, seed: int = 0) -> DelayModel:
    """Build a :class:`DelayModel` from a plain config dict + seed.

    Example::

        model = make_delay_model({"latency": {"kind": "constant", "p50_ms": 5}})
    """
    cfg = DelayModelConfig.from_dict(config or {})
    return DelayModel.from_config(cfg, seed=seed)


class StandaloneNetemTransport:
    """In-memory transport with netem-style delays for non-simulator use.

    Backed by an :class:`InMemoryNetwork`; delays are injected through an
    ``asyncio.sleep`` so this is usable from Tier 2 / shell agents.  The
    sleep duration is sourced from the same :class:`DelayModel` that the
    Tier 1 simulator uses, so the *profile* of latencies stays comparable
    across tiers.

    Note: Because Tier 2 is non-deterministic by design, the standalone
    transport's RNG advances in real time and is not seed-pinnable.

    Example::

        network = InMemoryNetwork()
        model = make_delay_model({"latency": {"kind": "constant", "p50_ms": 5}})
        t = StandaloneNetemTransport(AgentId("a1"), network, model)
        await t.send(AgentId("a2"), b"hello")  # actually sleeps ~5ms
    """

    capabilities = TransportCapabilities(
        supports_streaming=False,
        ordered=True,
        reliable=True,
    )

    def __init__(
        self,
        agent_id: AgentId,
        network: InMemoryNetwork,
        delay_model: DelayModel,
    ) -> None:
        self._agent_id = agent_id
        self._network = network
        self._queue = network.register(agent_id)
        self._delay_model = delay_model

    async def send(self, to: AgentId, payload: bytes) -> None:
        """Send a payload with netem-modelled delay (and possible drop).

        Example::

            await transport.send(AgentId("a2"), b"hello")
        """
        delay = self._delay_model.compute_delay(self._agent_id, to, len(payload))
        if delay is None:
            return  # dropped at the wire
        if delay > 0:
            await asyncio.sleep(delay)
        await self._network.deliver(self._agent_id, to, payload)

    async def receive(self) -> tuple[AgentId, bytes]:
        """Wait for the next message and return (sender, payload).

        Example::

            sender, data = await transport.receive()
        """
        return await self._queue.get()

    async def broadcast(self, payload: bytes) -> None:
        """Broadcast to all peers, each subject to its own delay sample.

        Example::

            await transport.broadcast(b"announcement")
        """
        for aid in self._network.get_agents():
            if aid != self._agent_id:
                await self.send(aid, payload)
