# SPDX-License-Identifier: Apache-2.0
"""EigenTrust trust plugin — transitive, Sybil-resistant reputation.

EigenTrust (Kamvar, Schlosser, Garcia-Molina, WWW 2003) computes a global
trust vector ``t`` as the stationary distribution of a Markov chain whose
transition matrix is built from each agent's *local* trust opinions.

Where ``score_average`` answers "what is the average feedback for this
agent?", EigenTrust answers "what mass of probability does a random walker,
restarted with probability ``a`` to a pre-trusted set ``p``, spend on this
agent in the limit?". The fixed point

    t = (1 - a) * C^T * t + a * p

is provably the unique stationary distribution on the probability simplex
when ``a > 0`` (Perron-Frobenius applied to an aperiodic, irreducible chain
formed by the teleport mixture).

What this plugin actually implements
------------------------------------

* Local trust ``s_ij`` is the running sum of positive minus negative
  evidence, clipped at zero (``max(0, pos - neg)``).
* Row-normalized to ``c_ij = s_ij / sum_k s_ik``.  Rows that sum to zero
  fall back to the pre-trusted distribution ``p`` (a "naive" walker
  teleports to known-honest peers).
* Power iteration of ``t <- (1 - a) C^T t + a p`` until ``||delta||_1 <
  tol`` or ``max_iter`` is reached.
* ``score(agent)`` returns the converged mass for ``agent``, in ``[0, 1]``.

Properties checked by the test suite
------------------------------------

* **simplex**: ``sum_i t_i == 1`` and ``t_i >= 0`` for all agents.
* **row-stochasticity**: every row of ``C`` sums to 1.
* **fixed point**: ``||(1-a) C^T t + a p - t||_inf < tol``.
* **Sybil lower bound**: a Sybil peer with no incoming local trust from
  honest agents earns at most ``a * p_i`` mass.  This is the central
  Sybil-resistance result.
* **monotonicity (weak)**: holding everyone else's feedback fixed,
  increasing positive evidence for agent ``j`` from honest reporters does
  not decrease ``t_j``.

The plugin is *deterministic* and dependency-free (pure Python, no numpy),
matching the rest of the reference plugin pack.

Example::

    trust = EigenTrust(
        identity=None,
        pretrusted=[AgentId("a1"), AgentId("a2")],
        alpha=0.1,
    )
    await trust.report(AgentId("a3"),
        Evidence(reporter=AgentId("a1"), subject=AgentId("a3"), kind="positive"))
    score = await trust.score(AgentId("a3"))
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from nest_core.types import (
    AgentId,
    Attestation,
    Claim,
    Evidence,
    ReputationScore,
    Signature,
)

# ---------------------------------------------------------------------------
# Module-level invariants exposed for tests and external assertion.  Keeping
# them as constants instead of magic numbers makes the proof obligations
# explicit.
# ---------------------------------------------------------------------------

#: Default teleport / pre-trusted mixing weight.  ``a > 0`` is required for
#: convergence; ``a`` close to 1 ignores the local-trust graph entirely.
DEFAULT_ALPHA: float = 0.1

#: Maximum power-iteration steps before we accept the current estimate.
#: For ``alpha = 0.1`` the convergence factor per step is ``(1 - alpha) =
#: 0.9``, so reaching residual ``1e-6`` from a worst-case initial gap of
#: ``2`` requires roughly ``log(2 / tol) / log(1 / (1 - alpha)) ≈ 138``
#: iterations.  ``300`` leaves comfortable headroom and is still cheap on
#: the agent counts NEST scenarios actually exercise.
DEFAULT_MAX_ITER: int = 300

#: L1 convergence tolerance for power iteration.
DEFAULT_TOL: float = 1.0e-6

#: How heavily a single piece of negative evidence outweighs positive
#: evidence when forming the raw local-trust score.  EigenTrust's original
#: paper uses ``sat - unsat`` clipped at 0; we match that.
_NEG_WEIGHT: int = 1
_POS_WEIGHT: int = 1


def _normalize_local_trust(
    raw: dict[AgentId, dict[AgentId, float]],
    agents: list[AgentId],
    pretrusted_dist: dict[AgentId, float],
) -> dict[AgentId, dict[AgentId, float]]:
    """Row-normalize the local trust matrix.

    For each agent ``i``:

    * if ``sum_j raw[i][j] > 0``, set ``c_ij = raw[i][j] / row_sum``;
    * otherwise (the "naive walker"), set ``c_ij = p_j``.

    The result satisfies ``sum_j c_ij == 1`` for every agent ``i`` —
    the row-stochasticity invariant.
    """
    c: dict[AgentId, dict[AgentId, float]] = {}
    for i in agents:
        row = raw.get(i, {})
        total = sum(max(0.0, v) for v in row.values())
        if total <= 0.0:
            c[i] = dict(pretrusted_dist)
        else:
            c[i] = {j: max(0.0, row.get(j, 0.0)) / total for j in agents}
    return c


def _power_iterate(
    c: dict[AgentId, dict[AgentId, float]],
    p: dict[AgentId, float],
    alpha: float,
    agents: list[AgentId],
    max_iter: int,
    tol: float,
) -> tuple[dict[AgentId, float], int, float]:
    """Compute the stationary distribution of ``(1 - alpha) C^T + alpha P``.

    Returns ``(t, iters, residual)``: the trust vector, the number of
    iterations used, and the final L1 residual ``||t_k - t_{k-1}||_1``.

    Loop invariants:

    * ``sum_i t_i == 1`` after every iteration (probability mass conserved
      because each ``C`` row sums to 1 and so does ``p``);
    * ``t_i >= 0`` for all ``i`` (non-negative combination of non-negative
      vectors).
    """
    n = len(agents)
    if n == 0:
        return {}, 0, 0.0
    t: dict[AgentId, float] = dict(p)
    residual = 0.0
    iters = 0
    for k in range(max_iter):
        iters = k + 1
        next_t: dict[AgentId, float] = dict.fromkeys(agents, 0.0)
        # Compute (C^T t)_j = sum_i c_ij * t_i.
        for i in agents:
            ti = t[i]
            if ti == 0.0:
                continue
            row = c[i]
            for j, c_ij in row.items():
                if c_ij == 0.0:
                    continue
                next_t[j] += c_ij * ti
        # Mix with the pre-trusted distribution.
        for j in agents:
            next_t[j] = (1.0 - alpha) * next_t[j] + alpha * p[j]
        residual = sum(abs(next_t[j] - t[j]) for j in agents)
        t = next_t
        if residual < tol:
            break
    return t, iters, residual


class EigenTrust:
    """Transitive, Sybil-resistant reputation via stationary distribution.

    Parameters
    ----------
    identity:
        Optional ``Identity`` used to sign attestations.  Kept compatible
        with ``ScoreAverageTrust``.
    pretrusted:
        Iterable of pre-trusted agent ids.  These agents seed the teleport
        distribution ``p``.  If empty, ``p`` is uniform over all agents
        seen so far (a permissive default — good for tests, less Sybil
        resistant in practice).
    alpha:
        Teleport / pre-trusted mixing weight in ``(0, 1]``.  Defaults to
        ``DEFAULT_ALPHA = 0.1`` per the EigenTrust paper.
    max_iter, tol:
        Power-iteration knobs.  See ``DEFAULT_MAX_ITER`` / ``DEFAULT_TOL``.

    Example::

        trust = EigenTrust(pretrusted=[AgentId("a1")], alpha=0.1)
        await trust.report(AgentId("a2"),
            Evidence(reporter=AgentId("a1"), subject=AgentId("a2"), kind="positive"))
        score = await trust.score(AgentId("a2"))
    """

    def __init__(
        self,
        identity: Any = None,
        pretrusted: list[AgentId] | None = None,
        alpha: float = DEFAULT_ALPHA,
        max_iter: int = DEFAULT_MAX_ITER,
        tol: float = DEFAULT_TOL,
    ) -> None:
        if not 0.0 < alpha <= 1.0:
            msg = f"alpha must be in (0, 1]; got {alpha}"
            raise ValueError(msg)
        if max_iter < 1:
            msg = f"max_iter must be >= 1; got {max_iter}"
            raise ValueError(msg)
        if tol <= 0.0:
            msg = f"tol must be > 0; got {tol}"
            raise ValueError(msg)

        self._identity = identity
        self._alpha = alpha
        self._max_iter = max_iter
        self._tol = tol

        # Pre-trusted set is stored as a *list* so the seed distribution is
        # reproducible regardless of dict ordering.
        self._pretrusted: list[AgentId] = list(pretrusted or [])

        # Local-trust counts.  ``self._pos[i][j]`` is the count of positive
        # evidence agent ``i`` reported about agent ``j``; same for neg.
        # We never mutate these once they go into the convergence step --
        # power iteration reads from a snapshot.
        self._pos: dict[AgentId, dict[AgentId, int]] = defaultdict(
            lambda: defaultdict(int),
        )
        self._neg: dict[AgentId, dict[AgentId, int]] = defaultdict(
            lambda: defaultdict(int),
        )

        # Agents we've ever seen (as reporter, subject, or pre-trusted).
        self._agents: set[AgentId] = set(self._pretrusted)

        # Optional stakes (kept for protocol compatibility; not used in the
        # eigenvector computation).
        self._stakes: dict[AgentId, int] = {}

        # Cached convergence diagnostics from the last ``score()`` call.
        # Tests and validators can read these to assert convergence.
        self.last_iters: int = 0
        self.last_residual: float = 0.0

    # ------------------------------------------------------------------ API

    async def score(self, agent: AgentId) -> ReputationScore:
        """Return the converged EigenTrust mass for ``agent``.

        The score is on ``[0, 1]`` and the full trust vector sums to 1.
        ``confidence`` is reported as ``1 - last_residual / tol`` clipped
        to ``[0, 1]`` — a soft signal of how well power iteration
        converged on this call.

        Example::

            score = await trust.score(AgentId("a1"))
        """
        # Ensure the agent is part of the known set so an isolated query
        # doesn't lie by omission.  This keeps ``score()`` total.
        self._agents.add(agent)
        t = self._compute_trust_vector()
        value = t.get(agent, 0.0)
        sample_count = sum(len(self._pos[i]) + len(self._neg[i]) for i in self._agents)
        confidence = (
            0.0
            if self._tol <= 0
            else max(
                0.0,
                min(1.0, 1.0 - self.last_residual / max(self._tol, 1e-12)),
            )
        )
        return ReputationScore(
            agent_id=agent,
            score=value,
            confidence=confidence,
            sample_count=sample_count,
        )

    async def attest(self, agent: AgentId, claim: Claim) -> Attestation:
        """Create a signed attestation about ``agent``.

        Example::

            att = await trust.attest(AgentId("a1"), claim)
        """
        sig = Signature(signer=AgentId("system"), value=b"attestation", algorithm="none")
        if self._identity is not None:
            sig = self._identity.sign(claim.model_dump_json().encode())
        return Attestation(issuer=AgentId("system"), claim=claim, signature=sig)

    async def report(self, agent: AgentId, evidence: Evidence) -> None:
        """Record a piece of evidence about ``agent``.

        Evidence kinds:

        * ``"positive"`` — increments ``pos[reporter][subject]``.
        * ``"negative"`` or ``"byzantine"`` — increments ``neg[reporter][subject]``.
        * anything else is treated as neutral and ignored (does not bias
          the local-trust matrix in either direction).

        Example::

            await trust.report(
                AgentId("a2"),
                Evidence(reporter=AgentId("a1"), subject=AgentId("a2"), kind="positive"),
            )
        """
        reporter = evidence.reporter
        subject = evidence.subject
        # Defensive: if the caller passed a stale ``agent`` distinct from
        # ``evidence.subject``, trust the explicit argument so this
        # matches the protocol's signature.
        if subject != agent:
            subject = agent
        self._agents.add(reporter)
        self._agents.add(subject)
        kind = evidence.kind
        if kind == "positive":
            self._pos[reporter][subject] += _POS_WEIGHT
        elif kind in ("negative", "byzantine"):
            self._neg[reporter][subject] += _NEG_WEIGHT
        # Other kinds: deliberately no-op.

    async def stake(self, agent: AgentId, amount: int) -> None:
        """Record a stake on ``agent`` (informational only).

        Example::

            await trust.stake(AgentId("a1"), 100)
        """
        self._agents.add(agent)
        self._stakes[agent] = self._stakes.get(agent, 0) + amount

    # ------------------------------------------------------------ Internals

    def _pretrusted_distribution(self, agents: list[AgentId]) -> dict[AgentId, float]:
        """Return the seed distribution ``p`` over ``agents``.

        Invariant: ``sum_i p_i == 1`` and ``p_i >= 0``.
        """
        n = len(agents)
        if n == 0:
            return {}
        pretrusted_in_known = [a for a in self._pretrusted if a in set(agents)]
        if pretrusted_in_known:
            mass = 1.0 / len(pretrusted_in_known)
            return {a: (mass if a in pretrusted_in_known else 0.0) for a in agents}
        # No pretrusted set declared — fall back to uniform.  This makes
        # the plugin usable in the existing ``reputation`` scenario
        # without YAML changes, at the cost of weaker Sybil resistance.
        mass = 1.0 / n
        return {a: mass for a in agents}

    def _local_trust_raw(
        self,
        agents: list[AgentId],
    ) -> dict[AgentId, dict[AgentId, float]]:
        """Build ``s_ij = max(0, pos_ij - neg_ij)`` over the known agents."""
        raw: dict[AgentId, dict[AgentId, float]] = {}
        for i in agents:
            row: dict[AgentId, float] = {}
            pos_row = self._pos.get(i, {})
            neg_row = self._neg.get(i, {})
            subjects = set(pos_row.keys()) | set(neg_row.keys())
            for j in subjects:
                if j == i:
                    # No self-trust.  An agent rating itself would let it
                    # short-circuit the random walk.
                    continue
                val = float(pos_row.get(j, 0) * _POS_WEIGHT - neg_row.get(j, 0) * _NEG_WEIGHT)
                if val > 0.0:
                    row[j] = val
            raw[i] = row
        return raw

    def _compute_trust_vector(self) -> dict[AgentId, float]:
        """Run power iteration and cache convergence diagnostics."""
        agents = sorted(self._agents)
        if not agents:
            self.last_iters = 0
            self.last_residual = 0.0
            return {}
        p = self._pretrusted_distribution(agents)
        raw = self._local_trust_raw(agents)
        c = _normalize_local_trust(raw, agents, p)
        t, iters, residual = _power_iterate(
            c,
            p,
            self._alpha,
            agents,
            self._max_iter,
            self._tol,
        )
        self.last_iters = iters
        self.last_residual = residual
        return t

    # ----------------------------------------------------------- Introspection

    def trust_vector(self) -> dict[AgentId, float]:
        """Return the current converged trust vector ``t`` as a dict.

        Useful for validators / tests that want to inspect the whole
        simplex rather than a single agent's mass.

        Example::

            t = trust.trust_vector()
            assert abs(sum(t.values()) - 1.0) < 1e-9
        """
        return self._compute_trust_vector()

    def local_trust_matrix(self) -> dict[AgentId, dict[AgentId, float]]:
        """Return the row-normalized local trust matrix ``C``.

        Every row sums to 1, by construction.  Exposed so tests can
        assert the row-stochasticity invariant directly.
        """
        agents = sorted(self._agents)
        p = self._pretrusted_distribution(agents)
        raw = self._local_trust_raw(agents)
        return _normalize_local_trust(raw, agents, p)
