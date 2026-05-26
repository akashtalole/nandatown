# SPDX-License-Identifier: Apache-2.0
"""Per-link delay, bandwidth, jitter and reorder model for Tier 1 transport.

By default the Tier 1 simulator delivers messages at ``time = now`` and the
virtual clock never advances on its own.  That is fine for correctness tests
but it means *every* latency metric in a NEST trace is ``0.0`` — which is
useless for SLO work, capacity planning, tail-latency analysis, or comparing
two protocol implementations.

This module fills that gap.  A :class:`DelayModel` is consulted by the
simulator on every ``send`` (and ``broadcast``) and returns the delay (in
virtual seconds) that the simulator should add to the delivery event.  All
randomness is sourced from a caller-provided :class:`random.Random` so the
simulation stays deterministic: same seed, same trace, byte-for-byte.

The model is intentionally cheap and self-contained so it can be slapped on
to existing scenarios without changing agents:

* ``LatencyDistribution`` — constant / uniform / lognormal-tailed delays in
  virtual seconds.
* ``DelayModel`` — applies latency + jitter + bandwidth-based serialization
  delay + reordering, with reproducible seeding.

The model is *deliberately* simple — it is a netem-style fault injector for
unit-sized agent messages, not a full DPDK emulator.  It captures the things
that move SLO needles in real systems: median, tail, jitter, throughput
ceiling, and out-of-order delivery.

Example::

    from nest_core.sim.delay_model import (
        DelayModel,
        LatencyDistribution,
        DelayModelConfig,
    )

    model = DelayModel.from_config(
        DelayModelConfig(
            latency=LatencyDistribution(kind="lognormal", p50_ms=20, p99_ms=120),
            jitter_ms=2.0,
            bandwidth_kbps=1000,
        ),
        seed=42,
    )

    # Per-message delay in *virtual seconds*:
    delay = model.compute_delay(sender, receiver, payload_size_bytes=512)
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from nest_core.types import AgentId

LatencyKind = Literal["constant", "uniform", "lognormal"]


# ---------------------------------------------------------------------------
# Public configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyDistribution:
    """Latency distribution descriptor.

    All times are *milliseconds*; the model converts to virtual seconds.

    * ``constant`` — every message takes exactly ``p50_ms``.
    * ``uniform`` — uniformly random in ``[min_ms, max_ms]``. If only
      ``p50_ms`` is set, ``min_ms = 0`` and ``max_ms = 2 * p50_ms`` so that
      the mean matches.
    * ``lognormal`` — fits a log-normal whose 50th percentile is ``p50_ms``
      and 99th percentile is ``p99_ms``. Heavy-tail by construction.

    Example::

        d = LatencyDistribution(kind="lognormal", p50_ms=20, p99_ms=200)
    """

    kind: LatencyKind = "constant"
    p50_ms: float = 0.0
    p99_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None

    def __post_init__(self) -> None:
        if self.p50_ms < 0:
            msg = f"p50_ms must be >= 0, got {self.p50_ms}"
            raise ValueError(msg)
        if self.kind == "lognormal":
            if self.p99_ms is None:
                msg = "lognormal latency requires p99_ms"
                raise ValueError(msg)
            if self.p99_ms < self.p50_ms:
                msg = f"p99_ms ({self.p99_ms}) must be >= p50_ms ({self.p50_ms})"
                raise ValueError(msg)
        if self.kind == "uniform":
            lo, hi = self.uniform_bounds()
            if lo > hi:
                msg = f"uniform latency: min_ms ({lo}) > max_ms ({hi})"
                raise ValueError(msg)

    def uniform_bounds(self) -> tuple[float, float]:
        lo = self.min_ms if self.min_ms is not None else 0.0
        hi = self.max_ms if self.max_ms is not None else 2.0 * self.p50_ms
        return lo, hi


@dataclass(frozen=True)
class DelayModelConfig:
    """Top-level config for a :class:`DelayModel`.

    Example::

        cfg = DelayModelConfig(
            latency=LatencyDistribution(kind="constant", p50_ms=10),
            jitter_ms=1.0,
            bandwidth_kbps=1000,
            reorder_prob=0.0,
            drop_prob=0.0,
        )
    """

    latency: LatencyDistribution = field(default_factory=LatencyDistribution)
    jitter_ms: float = 0.0
    bandwidth_kbps: float | None = None  # None = unlimited
    reorder_prob: float = 0.0
    drop_prob: float = 0.0
    # Optional per-link multipliers, keyed by (sender, receiver).  Useful for
    # asymmetric topologies / partitions.
    link_scale: dict[tuple[str, str], float] = field(
        default_factory=lambda: dict[tuple[str, str], float]()
    )

    def __post_init__(self) -> None:
        if self.jitter_ms < 0:
            msg = f"jitter_ms must be >= 0, got {self.jitter_ms}"
            raise ValueError(msg)
        if self.bandwidth_kbps is not None and self.bandwidth_kbps <= 0:
            msg = f"bandwidth_kbps must be > 0 or None, got {self.bandwidth_kbps}"
            raise ValueError(msg)
        if not 0.0 <= self.reorder_prob <= 1.0:
            msg = f"reorder_prob must be in [0,1], got {self.reorder_prob}"
            raise ValueError(msg)
        if not 0.0 <= self.drop_prob <= 1.0:
            msg = f"drop_prob must be in [0,1], got {self.drop_prob}"
            raise ValueError(msg)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DelayModelConfig:
        """Build a config from a plain dict (as parsed from YAML).

        Example::

            cfg = DelayModelConfig.from_dict({
                "latency": {"kind": "constant", "p50_ms": 10},
                "jitter_ms": 1.0,
            })
        """
        raw_lat_obj: Any = data.get("latency") or {}
        if not isinstance(raw_lat_obj, Mapping):
            msg = "transport.latency must be a mapping"
            raise TypeError(msg)
        raw_lat = cast("Mapping[str, Any]", raw_lat_obj)

        kind = cast("LatencyKind", str(raw_lat.get("kind", "constant")))
        p50 = float(raw_lat.get("p50_ms", 0.0))
        p99_raw = raw_lat.get("p99_ms")
        min_raw = raw_lat.get("min_ms")
        max_raw = raw_lat.get("max_ms")
        lat = LatencyDistribution(
            kind=kind,
            p50_ms=p50,
            p99_ms=float(p99_raw) if p99_raw is not None else None,
            min_ms=float(min_raw) if min_raw is not None else None,
            max_ms=float(max_raw) if max_raw is not None else None,
        )

        bw_raw = data.get("bandwidth_kbps")
        return cls(
            latency=lat,
            jitter_ms=float(data.get("jitter_ms", 0.0)),
            bandwidth_kbps=float(bw_raw) if bw_raw is not None else None,
            reorder_prob=float(data.get("reorder_prob", 0.0)),
            drop_prob=float(data.get("drop_prob", 0.0)),
        )


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------


def _solve_lognormal(p50_ms: float, p99_ms: float) -> tuple[float, float]:
    """Return (mu, sigma) for a log-normal hitting (p50, p99) in ms.

    For a log-normal, the *k*-th percentile is ``exp(mu + sigma * z_k)``
    where ``z_k`` is the standard-normal quantile.  Therefore:

        mu    = ln(p50)
        sigma = (ln(p99) - ln(p50)) / z_99

    with ``z_99 ~ 2.3263478740408408``.
    """
    if p50_ms <= 0:
        return -math.inf, 0.0
    z99 = 2.3263478740408408
    mu = math.log(p50_ms)
    if p99_ms <= p50_ms:
        return mu, 0.0
    sigma = (math.log(p99_ms) - mu) / z99
    return mu, sigma


class DelayModel:
    """Sample per-message delays for the simulator's transport.

    The model is *pure* with respect to its rng: feed it the same rng state
    and it will produce the same sequence of delays.  All knobs are static
    after construction — this matches netem's model.

    Example::

        model = DelayModel.from_config(cfg, seed=42)
        delay_seconds = model.compute_delay(sender, receiver, payload_size=128)
        if delay_seconds is None:
            # message dropped
            ...
    """

    __slots__ = (
        "_cfg",
        "_rng",
        "_mu",
        "_sigma",
        "_last_delay",  # for ordering preservation when reorder_prob == 0
    )

    def __init__(self, cfg: DelayModelConfig, rng: random.Random) -> None:
        self._cfg = cfg
        self._rng = rng
        self._mu, self._sigma = _solve_lognormal(
            cfg.latency.p50_ms,
            cfg.latency.p99_ms if cfg.latency.p99_ms is not None else cfg.latency.p50_ms,
        )
        self._last_delay: dict[tuple[str, str], float] = {}

    @classmethod
    def from_config(cls, cfg: DelayModelConfig, seed: int = 0) -> DelayModel:
        """Build from a config + integer seed.

        Example::

            model = DelayModel.from_config(cfg, seed=42)
        """
        return cls(cfg, random.Random(seed))

    @property
    def config(self) -> DelayModelConfig:
        return self._cfg

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------
    def _sample_latency_ms(self) -> float:
        kind = self._cfg.latency.kind
        if kind == "constant":
            return self._cfg.latency.p50_ms
        if kind == "uniform":
            lo, hi = self._cfg.latency.uniform_bounds()
            if hi <= lo:
                return lo
            return self._rng.uniform(lo, hi)
        if kind == "lognormal":
            if self._sigma == 0.0:
                return self._cfg.latency.p50_ms
            z = self._rng.gauss(0.0, 1.0)
            return math.exp(self._mu + self._sigma * z)
        msg = f"unknown latency kind: {kind!r}"
        raise ValueError(msg)

    def _serialization_delay_ms(self, payload_size: int) -> float:
        bw = self._cfg.bandwidth_kbps
        if bw is None or bw <= 0:
            return 0.0
        # bandwidth_kbps is *kilo-bits* per second; payload_size is bytes.
        bits = payload_size * 8
        return bits / bw  # kbps means kbit/s -> ms = bits / kbps

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    def compute_delay(
        self,
        sender: AgentId,
        receiver: AgentId,
        payload_size: int,
    ) -> float | None:
        """Return delivery delay in virtual seconds, or ``None`` if dropped.

        Even when ``reorder_prob == 0`` the model enforces FIFO per
        (sender, receiver): if a freshly sampled delay would land before the
        previous message on the same link, it is bumped up to ``prev + 1us``.

        Example::

            delay = model.compute_delay(AgentId("a"), AgentId("b"), 128)
        """
        if self._cfg.drop_prob > 0 and self._rng.random() < self._cfg.drop_prob:
            return None

        lat_ms = self._sample_latency_ms()

        if self._cfg.jitter_ms > 0:
            lat_ms += self._rng.uniform(-self._cfg.jitter_ms, self._cfg.jitter_ms)

        lat_ms += self._serialization_delay_ms(payload_size)

        scale = self._cfg.link_scale.get((str(sender), str(receiver)), 1.0)
        lat_ms *= scale

        if lat_ms < 0:
            lat_ms = 0.0

        delay_s = lat_ms / 1000.0

        # FIFO preservation per (sender, receiver) when reorder is off.
        key = (str(sender), str(receiver))
        prev = self._last_delay.get(key)
        if self._cfg.reorder_prob == 0.0 and prev is not None and delay_s <= prev:
            # Add a microsecond past the previous delay to preserve ordering
            # *deterministically*.  This avoids accidental reorders just
            # because two messages were sent at the same virtual time.
            delay_s = prev + 1e-6
        elif (
            self._cfg.reorder_prob > 0.0
            and prev is not None
            and self._rng.random() >= self._cfg.reorder_prob
            and delay_s <= prev
        ):
            delay_s = prev + 1e-6

        self._last_delay[key] = delay_s
        return delay_s

    # ------------------------------------------------------------------
    # Diagnostic helpers (for tests / metrics)
    # ------------------------------------------------------------------
    def sample_latencies_ms(self, n: int) -> list[float]:
        """Draw *n* raw latency samples (no jitter, no bandwidth, no FIFO).

        Useful for tests that want to verify the distribution shape.

        Example::

            samples = model.sample_latencies_ms(10_000)
        """
        return [self._sample_latency_ms() for _ in range(n)]
