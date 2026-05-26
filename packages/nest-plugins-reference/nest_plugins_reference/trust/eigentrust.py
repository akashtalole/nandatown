# SPDX-License-Identifier: Apache-2.0
"""EigenTrust-style reputation plugin with exponential time decay.

A reference implementation of the EigenTrust algorithm
(Kamvar, Schlosser, Garcia-Molina, *EigenTrust: Reputation Management
in P2P Networks*, WWW 2003), adapted for NEST's single-process discrete
event simulator and extended with:

* **Exponential time decay** of evidence — recent reports outweigh stale
  ones, so the system reacts to defectors faster than a naive running
  mean ever can.
* **Pre-trusted seed peers** — an optional set ``P`` of bootstrap agents
  whose mass is mixed in via a damping coefficient ``alpha`` (PageRank-
  style "random jump"). Prevents collusive cliques from running away
  with the score in the absence of any seed.
* **Stake as a confidence floor** — calls to :py:meth:`stake` raise the
  reported ``confidence`` of an agent (skin-in-the-game is information,
  even if NEST cannot slash it).
* **Lazy power iteration** with epoch invalidation — :py:meth:`score`
  recomputes the global trust vector only when new evidence has been
  reported since the last call. Constant amortised work in steady state.

The local trust matrix ``C`` is built from reported evidence:
``c_ij`` = max(0, positives - negatives) from i to j, with each report
exponentially decayed by ``exp(-lambda * (now - t))``. Rows are
L1-normalised; an all-zero row falls back to ``p``. The global trust
vector is the fixed point of ``t = (1-alpha) * C^T t + alpha * p``,
clamped to ``[0, 1]``.

Example::

    from nest_plugins_reference.trust.eigentrust import EigenTrust
    trust = EigenTrust(
        pre_trusted=[AgentId("seed-0")],
        decay_lambda=0.01,
        alpha=0.15,
    )
    await trust.report(AgentId("a1"), evidence)
    score = await trust.score(AgentId("a1"))
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nest_core.types import (
    AgentId,
    Attestation,
    Claim,
    Evidence,
    ReputationScore,
    Signature,
)

# Public defaults. Exposed so callers can sweep them in scenarios.
DEFAULT_ALPHA: float = 0.15  # PageRank-style damping / pre-trust weight.
DEFAULT_DECAY_LAMBDA: float = 0.0  # No decay by default — opt-in via ctor.
DEFAULT_MAX_ITERS: int = 100
DEFAULT_TOLERANCE: float = 1e-6
DEFAULT_SCORE: float = 0.5


@dataclass(slots=True)
class _Report:
    """A single decayable report. Internal; not part of the public API."""

    reporter: AgentId
    subject: AgentId
    weight: float  # +1.0 for positive, -1.0 for negative/byzantine.
    tick: int  # Monotonic event counter — deterministic across runs.


def _evidence_weight(kind: str) -> float:
    """Map an Evidence kind string to a signed weight.

    Positive evidence pulls trust up; negative / byzantine evidence pulls
    it down. Unknown kinds count as a neutral observation (weight 0) so
    that future kind extensions degrade gracefully.
    """
    if kind == "positive":
        return 1.0
    if kind in ("negative", "byzantine"):
        return -1.0
    return 0.0


class EigenTrust:
    """EigenTrust-style global reputation with time decay.

    Thread-unsafe by design — NEST is single-process and the simulator
    serialises plugin calls through an async event loop. Doing locking
    here would only hide bugs.

    Parameters
    ----------
    identity:
        Optional identity plugin used to sign attestations. Mirrors the
        ``score_average`` reference plugin so the two are drop-in
        compatible.
    pre_trusted:
        Iterable of pre-trusted seed agent ids ``P``. If empty, ``p`` is
        the uniform distribution over agents that have been observed.
        Cold-start agents inherit ``DEFAULT_SCORE`` until they have been
        reported on at least once.
    alpha:
        Damping coefficient. ``alpha`` of the mass is mixed in from
        ``p`` each iteration; the rest follows the trust matrix. The
        WWW 2003 paper uses ``a = 0.15`` (same as PageRank).
    decay_lambda:
        Exponential time-decay rate per tick. A report ``k`` ticks old
        contributes ``exp(-decay_lambda * k)`` of its original weight.
        ``0.0`` disables decay; ``0.01`` is a sensible "recent half-life
        of ~70 ticks" default. The tick is a monotonic event counter so
        the decay is deterministic across runs with the same seed.
    max_iters / tolerance:
        Power-iteration stopping conditions. Power iteration on a
        stochastic matrix with damping is geometric in ``1 - alpha``, so
        100 iterations at ``alpha = 0.15`` is wildly more than enough.
    """

    def __init__(
        self,
        identity: Any = None,
        *,
        pre_trusted: list[AgentId] | tuple[AgentId, ...] | None = None,
        alpha: float = DEFAULT_ALPHA,
        decay_lambda: float = DEFAULT_DECAY_LAMBDA,
        max_iters: int = DEFAULT_MAX_ITERS,
        tolerance: float = DEFAULT_TOLERANCE,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            msg = f"alpha must be in (0, 1], got {alpha}"
            raise ValueError(msg)
        if decay_lambda < 0.0:
            msg = f"decay_lambda must be >= 0, got {decay_lambda}"
            raise ValueError(msg)
        if max_iters < 1:
            msg = f"max_iters must be >= 1, got {max_iters}"
            raise ValueError(msg)

        self._identity = identity
        self._pre_trusted: set[AgentId] = set(pre_trusted or ())
        self._alpha = alpha
        self._decay_lambda = decay_lambda
        self._max_iters = max_iters
        self._tolerance = tolerance

        # Event log of reports. Append-only, decayed at score() time.
        self._reports: list[_Report] = []
        self._stakes: dict[AgentId, int] = {}

        # Every agent we've ever seen as reporter or subject, in
        # insertion order. Using a dict for ordered-set semantics.
        self._agents: dict[AgentId, None] = {}

        # Per-agent sample count — number of reports about this agent,
        # used as the ``sample_count`` field on ReputationScore.
        self._sample_count: dict[AgentId, int] = {}

        # Caches invalidated on every report().
        self._tick: int = 0
        self._dirty_epoch: int = 0
        self._cache_epoch: int = -1
        self._cached_trust: dict[AgentId, float] = {}

    # ------------------------------------------------------------------
    # Trust protocol
    # ------------------------------------------------------------------

    async def score(self, agent: AgentId) -> ReputationScore:
        """Return the current global trust score for ``agent``.

        The score is in ``[0, 1]``. ``confidence`` rises with both the
        number of samples (capped at 100 like ``score_average``) and any
        stake placed via :py:meth:`stake`. ``sample_count`` is the number
        of reports about ``agent``.
        """
        self._recompute_if_dirty()

        n_samples = self._sample_count.get(agent, 0)
        if n_samples == 0 and agent not in self._pre_trusted:
            # Cold start — give a neutral prior. Matches score_average.
            return ReputationScore(
                agent_id=agent,
                score=DEFAULT_SCORE,
                confidence=0.0,
                sample_count=0,
            )

        raw = self._cached_trust.get(agent, DEFAULT_SCORE)
        # Sample-based confidence (matches score_average's curve) plus a
        # small additive boost from staked credits.
        sample_conf = min(1.0, n_samples / 100.0)
        stake_conf = min(1.0, self._stakes.get(agent, 0) / 1000.0)
        confidence = min(1.0, sample_conf + 0.25 * stake_conf)
        return ReputationScore(
            agent_id=agent,
            score=raw,
            confidence=confidence,
            sample_count=n_samples,
        )

    async def attest(self, agent: AgentId, claim: Claim) -> Attestation:
        """Create an attestation about ``agent``. Mirrors score_average."""
        sig = Signature(signer=AgentId("system"), value=b"attestation", algorithm="none")
        if self._identity is not None:
            sig = self._identity.sign(claim.model_dump_json().encode())
        return Attestation(issuer=AgentId("system"), claim=claim, signature=sig)

    async def report(self, agent: AgentId, evidence: Evidence) -> None:
        """Record an evidence report and invalidate the trust cache.

        The ``evidence.reporter`` field is required and load-bearing —
        EigenTrust weights each report by the reporter's own current
        global trust. A report from a previously-reported-on-as-bad
        peer effectively counts for nothing, which is the whole point.
        """
        weight = _evidence_weight(evidence.kind)
        if weight == 0.0:
            return  # Unknown / neutral kind — silently ignore.

        self._tick += 1
        self._reports.append(
            _Report(
                reporter=evidence.reporter,
                subject=agent,
                weight=weight,
                tick=self._tick,
            )
        )
        self._sample_count[agent] = self._sample_count.get(agent, 0) + 1
        self._agents.setdefault(agent, None)
        self._agents.setdefault(evidence.reporter, None)
        self._dirty_epoch += 1

    async def stake(self, agent: AgentId, amount: int) -> None:
        """Stake credits on ``agent``'s good behaviour.

        Stake raises ``confidence`` but does not directly raise ``score``
        — the literature is unanimous that stake without slashing is a
        confidence signal, not a trust signal. Slashing is out of scope
        for this plugin; a payments-coupled variant could wire it up.
        """
        if amount == 0:
            return
        self._stakes[agent] = self._stakes.get(agent, 0) + amount
        self._agents.setdefault(agent, None)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _recompute_if_dirty(self) -> None:
        """Recompute the global trust vector if any report changed it."""
        if self._cache_epoch == self._dirty_epoch:
            return
        self._cached_trust = self._compute_trust()
        self._cache_epoch = self._dirty_epoch

    def _compute_trust(self) -> dict[AgentId, float]:
        """Run power iteration on the decayed local-trust matrix.

        Returns the global trust vector as a dict ``{agent: score}`` with
        values in ``[0, 1]``. The math:

            t_{k+1} = (1 - alpha) * C^T t_k + alpha * p

        ``C`` is built fresh from the decayed report log; ``p`` is the
        seed distribution; ``alpha`` is the damping coefficient. We
        L1-normalise rows of ``C`` and the input vectors, then rescale
        the final vector to ``[0, 1]`` by dividing by its max so the
        score returned to callers is comparable to ``score_average``.
        """
        agents = list(self._agents.keys())
        if not agents:
            return {}

        n = len(agents)
        index = {a: i for i, a in enumerate(agents)}

        # Build the decayed local trust matrix as a dense 2D list. n is
        # bounded by simulator size (10k in the README); for the trust
        # layer in practice ``n`` is small enough that O(n^2) is fine.
        # If someone needs sparse for 10k+ agents, swap the storage —
        # the algorithm is unchanged.
        c: list[list[float]] = [[0.0] * n for _ in range(n)]
        now = self._tick
        decay = self._decay_lambda
        for r in self._reports:
            i = index.get(r.reporter)
            j = index.get(r.subject)
            if i is None or j is None or i == j:
                # Drop self-reports — EigenTrust forbids them; otherwise
                # an agent can endorse itself into the trusted set.
                continue
            age = now - r.tick
            factor = math.exp(-decay * age) if decay > 0.0 else 1.0
            c[i][j] += r.weight * factor

        # Clip to non-negative (negatives become zero local trust, not
        # "negative endorsement" — the math requires a stochastic matrix)
        # and L1-normalise each row.
        for i in range(n):
            row = c[i]
            for j in range(n):
                if row[j] < 0.0:
                    row[j] = 0.0
            s = sum(row)
            if s > 0.0:
                inv = 1.0 / s
                for j in range(n):
                    row[j] *= inv

        # Seed distribution p. Default: uniform over pre-trusted set;
        # if no pre-trusted set is given, uniform over all observed
        # agents (matches the WWW 2003 fallback).
        p = [0.0] * n
        seed_agents = [a for a in self._pre_trusted if a in index]
        if seed_agents:
            w = 1.0 / len(seed_agents)
            for a in seed_agents:
                p[index[a]] = w
        else:
            w = 1.0 / n
            for i in range(n):
                p[i] = w

        # Rows that are all zero (no outgoing trust) fall back to p so
        # the matrix is stochastic. Standard PageRank trick.
        for i in range(n):
            if sum(c[i]) == 0.0:
                c[i] = list(p)

        # Power iteration: t_{k+1} = (1 - alpha) * C^T t_k + alpha * p.
        t = list(p)
        alpha = self._alpha
        one_minus_alpha = 1.0 - alpha
        for _ in range(self._max_iters):
            new_t = [alpha * p[j] for j in range(n)]
            for i in range(n):
                ti = t[i]
                if ti == 0.0:
                    continue
                row = c[i]
                contribution_factor = one_minus_alpha * ti
                for j in range(n):
                    new_t[j] += contribution_factor * row[j]
            # Numerical L1-renormalise — shouldn't drift much, but a
            # stochastic matrix can lose a bit of mass to floating point.
            s = sum(new_t)
            if s > 0.0:
                inv = 1.0 / s
                for j in range(n):
                    new_t[j] *= inv
            # L1 convergence check.
            delta = sum(abs(new_t[j] - t[j]) for j in range(n))
            t = new_t
            if delta < self._tolerance:
                break

        # Rescale to [0, 1] by dividing by the max so a single dominant
        # agent gets 1.0 — keeps the score numerically comparable to
        # score_average. If everyone is equal, max == 1/n and we get a
        # uniform 1.0; that's the "no information" case, so map it to
        # DEFAULT_SCORE instead.
        m = max(t)
        if m <= 0.0:
            return {a: DEFAULT_SCORE for a in agents}
        spread = max(t) - min(t)
        if spread < 1e-12:
            return {a: DEFAULT_SCORE for a in agents}
        return {a: t[index[a]] / m for a in agents}
