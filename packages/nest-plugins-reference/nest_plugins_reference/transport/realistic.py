# SPDX-License-Identifier: Apache-2.0
"""Realistic transport plugin — per-hop latency, bandwidth, queueing, loss.

The default ``in_memory`` transport delivers every message at ``time = now``.
That is correctness-faithful but useless for studying any protocol property
that depends on timing: tail latency, retry behavior, backoff strategies,
deadline budgets, congestion. ``RealisticNetwork`` makes those properties
observable inside the deterministic Tier 1 simulator.

What it models
--------------

1.  **Per-hop base latency + lognormal jitter.** ``base_latency_ms`` is the
    mean propagation delay; ``jitter_sigma`` shapes the spread. Lognormal
    keeps the tail heavy (a real network never has a Gaussian latency
    distribution).
2.  **Bandwidth / serialization delay.** ``bandwidth_bps`` adds
    ``payload_size * 8 / bandwidth_bps`` seconds to the hop. A 1 KB message
    over a 1 Mbps link is ~8 ms slower than a 64 B message.
3.  **Egress queueing.** Each sender has a virtual egress link with a
    finite service rate (``bandwidth_bps``). Messages queued back-to-back
    are serialized — the second message can't leave until the first is
    fully transmitted. This is where ``mean_latency`` stops being a
    constant and starts to show the load-curve shape every backend
    engineer knows.
4.  **Egress queue capacity / load shedding.** ``max_queue_bytes`` caps how
    much can be queued per sender. When exceeded, the message is dropped
    with a ``"network"`` reason in the trace — drop-tail backpressure, the
    crude but honest baseline.
5.  **Random packet loss.** ``loss_rate`` applies an additional per-hop
    Bernoulli drop, distinct from the simulator's scenario-level
    ``failures.message_drop``. This lives at the link layer; failure
    injection lives above it.
6.  **Per-link overrides.** Specific ``(sender, target)`` pairs can carry
    their own latency, jitter, bandwidth, and loss — useful for modeling
    a slow datacenter cross-link, a flaky satellite hop, or a hot pair.

What it does *not* do
---------------------

This is still a single-process discrete-event simulator. There are no
TCP windows, no congestion control loops, no FEC. The point is: give
NEST the smallest, sharpest knobs that change protocol-level behavior
in a controlled way. Real TCP is what your production transport does;
NEST is for stressing the protocol that runs *on top* of it.

Example
-------

::

    from nest_plugins_reference.transport.realistic import (
        RealisticNetwork, RealisticTransport, LinkConfig,
    )

    net = RealisticNetwork(
        base_latency_ms=5.0,
        jitter_sigma=0.4,
        bandwidth_bps=10_000_000,    # 10 Mbps egress per agent
        max_queue_bytes=1_000_000,   # 1 MB per-agent egress queue
        loss_rate=0.001,
        links={
            ("buyer-0", "seller-0"): LinkConfig(base_latency_ms=80.0),
        },
    )

    sim = Simulator(seed=42, network_model=net)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from nest_core.sim.network import NetworkModel
from nest_core.types import AgentId, TransportCapabilities


@dataclass(frozen=True)
class LinkConfig:
    """Per-link overrides for the realistic network.

    Any field left as ``None`` falls back to the network-wide default.

    Example::

        slow = LinkConfig(base_latency_ms=200.0, loss_rate=0.05)
    """

    base_latency_ms: float | None = None
    jitter_sigma: float | None = None
    bandwidth_bps: float | None = None
    loss_rate: float | None = None


@dataclass
class _EgressState:
    """Per-sender egress link bookkeeping for queueing delay."""

    busy_until: float = 0.0
    queued_bytes: int = 0


class RealisticNetwork:
    """A network model with per-hop latency, bandwidth, jitter, and queueing.

    Instances are stateless across runs in the sense that determinism is
    governed entirely by the RNG the simulator supplies, but within a
    single run the model maintains per-agent egress state so queueing
    composes correctly across consecutive sends.

    Example::

        net = RealisticNetwork(base_latency_ms=10.0, bandwidth_bps=1_000_000)
    """

    def __init__(
        self,
        base_latency_ms: float = 5.0,
        jitter_sigma: float = 0.3,
        bandwidth_bps: float = 100_000_000.0,
        max_queue_bytes: int = 10_000_000,
        loss_rate: float = 0.0,
        links: dict[tuple[str, str], LinkConfig] | None = None,
    ) -> None:
        if base_latency_ms < 0:
            msg = "base_latency_ms must be >= 0"
            raise ValueError(msg)
        if jitter_sigma < 0:
            msg = "jitter_sigma must be >= 0"
            raise ValueError(msg)
        if bandwidth_bps <= 0:
            msg = "bandwidth_bps must be > 0"
            raise ValueError(msg)
        if max_queue_bytes < 0:
            msg = "max_queue_bytes must be >= 0"
            raise ValueError(msg)
        if not 0.0 <= loss_rate <= 1.0:
            msg = "loss_rate must be in [0, 1]"
            raise ValueError(msg)

        self._base_latency_ms = base_latency_ms
        self._jitter_sigma = jitter_sigma
        self._bandwidth_bps = bandwidth_bps
        self._max_queue_bytes = max_queue_bytes
        self._loss_rate = loss_rate
        self._links: dict[tuple[str, str], LinkConfig] = {
            (str(s), str(t)): cfg for (s, t), cfg in (links or {}).items()
        }
        self._egress: dict[AgentId, _EgressState] = {}

        # Diagnostic counters — useful in tests, not part of the public API.
        self.stats: dict[str, int] = {
            "scheduled": 0,
            "dropped_loss": 0,
            "dropped_queue_full": 0,
        }

    @classmethod
    def from_config(cls, config: dict[str, object]) -> RealisticNetwork:
        """Build a network from a plain-dict config (typically scenario YAML).

        Recognized keys: ``base_latency_ms``, ``jitter_sigma``,
        ``bandwidth_bps``, ``max_queue_bytes``, ``loss_rate``, and
        ``links`` (a list of ``{from, to, ...}`` dicts).

        Example::

            net = RealisticNetwork.from_config({
                "base_latency_ms": 20,
                "jitter_sigma": 0.5,
                "bandwidth_bps": 1_000_000,
                "links": [{"from": "a", "to": "b", "base_latency_ms": 150}],
            })
        """
        links: dict[tuple[str, str], LinkConfig] = {}
        raw_links = config.get("links")
        if isinstance(raw_links, list):
            for item in raw_links:
                if not isinstance(item, dict):
                    continue
                src = item.get("from")
                dst = item.get("to")
                if not isinstance(src, str) or not isinstance(dst, str):
                    continue
                links[(src, dst)] = LinkConfig(
                    base_latency_ms=_opt_float(item.get("base_latency_ms")),
                    jitter_sigma=_opt_float(item.get("jitter_sigma")),
                    bandwidth_bps=_opt_float(item.get("bandwidth_bps")),
                    loss_rate=_opt_float(item.get("loss_rate")),
                )
        return cls(
            base_latency_ms=float(config.get("base_latency_ms", 5.0)),  # type: ignore[arg-type]
            jitter_sigma=float(config.get("jitter_sigma", 0.3)),  # type: ignore[arg-type]
            bandwidth_bps=float(config.get("bandwidth_bps", 100_000_000.0)),  # type: ignore[arg-type]
            max_queue_bytes=int(config.get("max_queue_bytes", 10_000_000)),  # type: ignore[arg-type]
            loss_rate=float(config.get("loss_rate", 0.0)),  # type: ignore[arg-type]
            links=links,
        )

    def _link(self, sender: AgentId, target: AgentId) -> LinkConfig | None:
        return self._links.get((str(sender), str(target)))

    def _resolve(
        self,
        sender: AgentId,
        target: AgentId,
    ) -> tuple[float, float, float, float]:
        """Resolve per-link params, falling back to network defaults."""
        link = self._link(sender, target)
        base_ms = (
            self._base_latency_ms
            if link is None or link.base_latency_ms is None
            else link.base_latency_ms
        )
        sigma = (
            self._jitter_sigma if link is None or link.jitter_sigma is None else link.jitter_sigma
        )
        bw = (
            self._bandwidth_bps
            if link is None or link.bandwidth_bps is None
            else link.bandwidth_bps
        )
        loss = self._loss_rate if link is None or link.loss_rate is None else link.loss_rate
        return base_ms, sigma, bw, loss

    def schedule(
        self,
        sender: AgentId,
        target: AgentId,
        payload_size: int,
        t_now: float,
        rng: random.Random,
    ) -> float | None:
        """Compute the delivery time for a single send.

        Returns ``None`` to signal a transport-level drop (random loss or
        queue overflow).

        Example::

            t = net.schedule(AgentId("a1"), AgentId("a2"), 256, 0.0, rng)
        """
        base_ms, sigma, bw, loss = self._resolve(sender, target)

        # 1. Random link-layer loss. Sampled first so the drop decision
        #    does not depend on the queueing state.
        if loss > 0.0 and rng.random() < loss:
            self.stats["dropped_loss"] += 1
            return None

        # 2. Egress queueing. The sender has a virtual link with a finite
        #    service rate. A new message has to wait for whatever is
        #    currently in flight.
        egress = self._egress.setdefault(sender, _EgressState())
        service_time = (payload_size * 8.0) / bw  # seconds

        # Queue-occupancy estimate: bytes that haven't finished serializing.
        # We update lazily — if the link has gone idle, drain it first.
        if egress.busy_until <= t_now:
            egress.busy_until = t_now
            egress.queued_bytes = 0

        # Drop-tail: refuse the message if it would push the queue past the cap.
        if (egress.queued_bytes + payload_size) > self._max_queue_bytes:
            self.stats["dropped_queue_full"] += 1
            return None

        # 3. Jitter — lognormal so the tail behaves like a real network.
        #    A sigma of ~0.3 gives a P99/median ratio around 2x;
        #    sigma ~0.6 gives 4x, which is more like cross-region.
        jitter_factor = math.exp(rng.gauss(0.0, sigma)) if sigma > 0 else 1.0
        base_s = (base_ms / 1000.0) * jitter_factor

        # 4. Departure = when this message finishes serializing on the link.
        departure = egress.busy_until + service_time
        delivery = departure + base_s

        egress.busy_until = departure
        egress.queued_bytes += payload_size

        self.stats["scheduled"] += 1
        return delivery

    @property
    def network_model(self) -> NetworkModel:
        """Self-reference so callers can treat a plugin instance uniformly.

        Example::

            sim.set_network_model(net.network_model)
        """
        return self


# Protocol check at import time.
_proto_check: type[NetworkModel] = RealisticNetwork  # noqa: F841


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Standalone transport — usable outside the simulator (e.g. Tier 2 / tests)
# ---------------------------------------------------------------------------


@dataclass
class _Pending:
    """A scheduled delivery awaiting its turn."""

    deliver_at: float
    sender: AgentId
    payload: bytes
    seq: int


class RealisticTransport:
    """A standalone transport that uses :class:`RealisticNetwork` directly.

    For Tier 1, prefer wiring :class:`RealisticNetwork` into the
    :class:`~nest_core.sim.simulator.Simulator` via the ``network_model``
    parameter — that path is push-based and reuses the simulator's event
    queue. This class exists so the plugin satisfies the
    :class:`nest.plugins.transport` entry-point contract (a callable
    ``Transport`` implementation) and can be exercised in unit tests
    without spinning up a simulator.

    Example::

        net = RealisticNetwork(base_latency_ms=10.0)
        bus = {}
        t1 = RealisticTransport(AgentId("a1"), net, bus)
        t2 = RealisticTransport(AgentId("a2"), net, bus)
        await t1.send(AgentId("a2"), b"hello")
    """

    capabilities = TransportCapabilities(
        supports_streaming=False,
        ordered=True,
        reliable=False,  # because realistic networks drop packets
    )

    def __init__(
        self,
        agent_id: AgentId,
        network: RealisticNetwork,
        bus: dict[AgentId, list[_Pending]] | None = None,
        clock: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._network = network
        self._bus: dict[AgentId, list[_Pending]] = bus if bus is not None else {}
        self._bus.setdefault(agent_id, [])
        self._clock = clock
        self._seq = 0
        self._rng = rng if rng is not None else random.Random(0)

    @property
    def now(self) -> float:
        """Current logical time used for scheduling decisions.

        Example::

            t = transport.now
        """
        return self._clock

    def advance(self, t: float) -> None:
        """Advance the standalone clock (caller-driven; no autopilot here).

        Example::

            transport.advance(1.5)
        """
        if t < self._clock:
            msg = "Cannot move clock backwards"
            raise ValueError(msg)
        self._clock = t

    async def send(self, to: AgentId, payload: bytes) -> bool:
        """Schedule a send. Returns ``True`` if accepted, ``False`` if dropped.

        Example::

            ok = await transport.send(AgentId("a2"), b"hi")
        """
        t = self._network.schedule(
            self._agent_id,
            to,
            len(payload),
            self._clock,
            self._rng,
        )
        if t is None:
            return False
        self._seq += 1
        self._bus.setdefault(to, []).append(
            _Pending(deliver_at=t, sender=self._agent_id, payload=payload, seq=self._seq),
        )
        return True

    async def receive(self) -> tuple[AgentId, bytes]:
        """Return the earliest deliverable message (blocks logically, not async).

        Will raise ``LookupError`` if nothing has been delivered yet at
        the current logical clock — call :meth:`advance` first.

        Example::

            sender, payload = await transport.receive()
        """
        queue = self._bus.get(self._agent_id, [])
        ready = [p for p in queue if p.deliver_at <= self._clock]
        if not ready:
            raise LookupError("no message ready at current clock")
        # Earliest-time, then send-order, for deterministic ordering.
        ready.sort(key=lambda p: (p.deliver_at, p.seq))
        chosen = ready[0]
        queue.remove(chosen)
        return (chosen.sender, chosen.payload)

    async def broadcast(self, payload: bytes) -> int:
        """Broadcast to every agent registered on the shared bus.

        Returns the number of recipients that accepted the message
        (loss / queue-overflow drops are counted separately on the
        network's ``stats`` dict).

        Example::

            n = await transport.broadcast(b"announcement")
        """
        accepted = 0
        for aid in list(self._bus.keys()):
            if aid != self._agent_id and await self.send(aid, payload):
                accepted += 1
        return accepted
