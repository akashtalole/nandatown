# SPDX-License-Identifier: Apache-2.0
"""Tests for the EigenTrust plugin.

We check three layers of correctness:

1.  **Plugin conformance**: ``EigenTrust`` implements the ``Trust``
    protocol the same way ``ScoreAverageTrust`` does (drop-in).
2.  **Algorithmic invariants** (hand-rolled unit tests on small graphs):
    row-stochasticity, simplex, fixed point, Sybil lower bound,
    weak monotonicity.
3.  **Property-based** (hypothesis): the above invariants survive on
    randomly generated trust graphs.

These properties are *checkable*, not commented.  That is the whole
point of the plugin: where ``score_average`` says "trust is the mean of
feedback" and leaves Sybils silently winning, EigenTrust gives you a
fixed point you can assert against.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from nest_core.types import AgentId, Claim, Evidence
from nest_plugins_reference.trust.eigentrust import (
    DEFAULT_ALPHA,
    DEFAULT_TOL,
    EigenTrust,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: Numerical slack for floating-point comparisons.  We compute in pure
#: Python doubles, so 1e-9 is comfortable.
_EPS = 1e-9


def _agents(n: int) -> list[AgentId]:
    return [AgentId(f"a{i}") for i in range(n)]


async def _feed(
    trust: EigenTrust,
    reports: list[tuple[str, str, str]],
) -> None:
    """Feed a list of ``(reporter, subject, kind)`` tuples into the plugin."""
    for reporter, subject, kind in reports:
        await trust.report(
            AgentId(subject),
            Evidence(
                reporter=AgentId(reporter),
                subject=AgentId(subject),
                kind=kind,
            ),
        )


def _assert_on_simplex(t: dict[AgentId, float]) -> None:
    """t is a probability distribution: non-negative, sums to 1."""
    for a, v in t.items():
        assert v >= -_EPS, f"{a} has negative mass {v}"
    total = sum(t.values())
    assert math.isclose(total, 1.0, abs_tol=1e-6), f"trust vector does not sum to 1: {total}"


def _assert_row_stochastic(c: dict[AgentId, dict[AgentId, float]]) -> None:
    for i, row in c.items():
        total = sum(row.values())
        # Empty rows are not valid; we always fall back to ``p``.
        assert math.isclose(total, 1.0, abs_tol=1e-6), (
            f"row for {i} does not sum to 1: {total} (row={row})"
        )
        for j, v in row.items():
            assert v >= -_EPS, f"c[{i}][{j}] is negative: {v}"


def _assert_fixed_point(trust: EigenTrust, tol_factor: float = 10.0) -> None:
    """||(1-a) C^T t + a p - t||_inf < tol_factor * configured tol."""
    agents = sorted(trust.trust_vector().keys())
    if not agents:
        return
    t = trust.trust_vector()
    c = trust.local_trust_matrix()
    p = trust._pretrusted_distribution(agents)  # noqa: SLF001 — test wants internals  # pyright: ignore[reportPrivateUsage]
    alpha = trust._alpha  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    next_t: dict[AgentId, float] = dict.fromkeys(agents, 0.0)
    for i in agents:
        for j, c_ij in c[i].items():
            next_t[j] += c_ij * t[i]
    for j in agents:
        next_t[j] = (1.0 - alpha) * next_t[j] + alpha * p[j]
    worst = max(abs(next_t[j] - t[j]) for j in agents)
    assert worst < tol_factor * DEFAULT_TOL, f"fixed-point residual {worst} exceeds tolerance"


# ---------------------------------------------------------------------------
# 1. Plugin conformance — same shape as ``ScoreAverageTrust``
# ---------------------------------------------------------------------------


class TestEigenTrustConformance:
    @pytest.mark.asyncio
    async def test_default_score_is_uniform(self) -> None:
        """With no evidence and no pre-trusted set, score should be uniform.

        Because the seed distribution falls back to uniform when no
        pretrusted set is declared, an unknown agent gets ``1 / n`` mass
        after we register it via the ``score`` call.
        """
        trust = EigenTrust()
        score = await trust.score(AgentId("a1"))
        assert math.isclose(score.score, 1.0, abs_tol=1e-9)
        # One agent in the world -> all mass on it.
        assert score.sample_count == 0

    @pytest.mark.asyncio
    async def test_positive_reports_raise_score(self) -> None:
        trust = EigenTrust(pretrusted=[AgentId("a0")])
        # a0 is pretrusted; it endorses a1 ten times; a2 gets nothing.
        for _ in range(10):
            await trust.report(
                AgentId("a1"),
                Evidence(reporter=AgentId("a0"), subject=AgentId("a1"), kind="positive"),
            )
        s1 = await trust.score(AgentId("a1"))
        s2 = await trust.score(AgentId("a2"))
        assert s1.score > s2.score

    @pytest.mark.asyncio
    async def test_negative_reports_lower_score(self) -> None:
        trust = EigenTrust(pretrusted=[AgentId("a0")])
        await trust.report(
            AgentId("a1"),
            Evidence(reporter=AgentId("a0"), subject=AgentId("a1"), kind="positive"),
        )
        before = (await trust.score(AgentId("a1"))).score
        await trust.report(
            AgentId("a1"),
            Evidence(reporter=AgentId("a0"), subject=AgentId("a1"), kind="negative"),
        )
        after = (await trust.score(AgentId("a1"))).score
        assert after <= before + _EPS

    @pytest.mark.asyncio
    async def test_attest_returns_attestation(self) -> None:
        trust = EigenTrust()
        claim = Claim(subject=AgentId("a1"), predicate="completed_task", value="t1")
        att = await trust.attest(AgentId("a1"), claim)
        assert att.claim.subject == AgentId("a1")
        assert att.signature is not None

    @pytest.mark.asyncio
    async def test_stake_does_not_change_trust(self) -> None:
        trust = EigenTrust()
        before = (await trust.score(AgentId("a1"))).score
        await trust.stake(AgentId("a1"), 1_000)
        after = (await trust.score(AgentId("a1"))).score
        assert math.isclose(before, after, abs_tol=_EPS)

    def test_rejects_bad_alpha(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            EigenTrust(alpha=0.0)
        with pytest.raises(ValueError, match="alpha"):
            EigenTrust(alpha=1.1)

    def test_rejects_bad_iter_tol(self) -> None:
        with pytest.raises(ValueError, match="max_iter"):
            EigenTrust(max_iter=0)
        with pytest.raises(ValueError, match="tol"):
            EigenTrust(tol=0.0)


# ---------------------------------------------------------------------------
# 2. Algorithmic invariants on hand-crafted graphs
# ---------------------------------------------------------------------------


class TestEigenTrustInvariants:
    @pytest.mark.asyncio
    async def test_simplex_invariant_after_arbitrary_reports(self) -> None:
        trust = EigenTrust(pretrusted=[AgentId("a0")])
        await _feed(
            trust,
            [
                ("a0", "a1", "positive"),
                ("a0", "a1", "positive"),
                ("a1", "a2", "positive"),
                ("a2", "a3", "positive"),
                ("a3", "a4", "negative"),
                ("a1", "a4", "negative"),
            ],
        )
        # Touch a few agents so they're known.
        for i in range(5):
            await trust.score(AgentId(f"a{i}"))
        t = trust.trust_vector()
        _assert_on_simplex(t)

    @pytest.mark.asyncio
    async def test_row_stochastic_after_arbitrary_reports(self) -> None:
        trust = EigenTrust(pretrusted=[AgentId("a0")])
        await _feed(
            trust,
            [
                ("a0", "a1", "positive"),
                ("a1", "a2", "positive"),
                ("a1", "a3", "positive"),
                ("a2", "a0", "negative"),
            ],
        )
        for i in range(4):
            await trust.score(AgentId(f"a{i}"))
        c = trust.local_trust_matrix()
        _assert_row_stochastic(c)

    @pytest.mark.asyncio
    async def test_fixed_point_residual_within_tolerance(self) -> None:
        trust = EigenTrust(pretrusted=[AgentId("a0")])
        await _feed(
            trust,
            [
                ("a0", "a1", "positive"),
                ("a1", "a2", "positive"),
                ("a2", "a3", "positive"),
                ("a3", "a1", "positive"),
            ],
        )
        for i in range(4):
            await trust.score(AgentId(f"a{i}"))
        _assert_fixed_point(trust)
        assert trust.last_iters >= 1

    @pytest.mark.asyncio
    async def test_sybil_lower_bound(self) -> None:
        """A Sybil with no incoming honest trust gets at most ``alpha * p_i`` mass.

        Concretely: a0 is the only pretrusted agent; honest agents a1..a3
        endorse each other but never the Sybil ``s1``.  The Sybil can
        self-loop and shill its friends, but no honest mass flows toward
        it, so its score must be bounded above by ``alpha * p[s1]``.
        Since ``s1`` is not pretrusted, ``p[s1] == 0`` and the bound is
        ``alpha * 0 == 0``.
        """
        trust = EigenTrust(pretrusted=[AgentId("a0")], alpha=0.1)
        honest_reports = [
            ("a0", "a1", "positive"),
            ("a1", "a2", "positive"),
            ("a2", "a3", "positive"),
            ("a3", "a1", "positive"),
        ]
        sybil_reports = [
            # Sybils shilling each other.
            ("s1", "s2", "positive"),
            ("s2", "s1", "positive"),
            # Sybil claiming honest agents endorsed it -- *but* the
            # plugin only records reports made by the actual reporter,
            # which here is ``s1``, not ``a1``.  This is the threat
            # model: a Sybil cannot forge an honest agent's report.
            ("s1", "s1", "positive"),
        ]
        await _feed(trust, honest_reports + sybil_reports)
        for a in ["a0", "a1", "a2", "a3", "s1", "s2"]:
            await trust.score(AgentId(a))

        t = trust.trust_vector()
        p = trust._pretrusted_distribution(sorted(trust._agents))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        # Sybils only get the teleport mass alpha * p_i, and p_i = 0 for
        # non-pretrusted agents.  Allow tiny slack for numerical noise.
        assert t[AgentId("s1")] <= 0.1 * p[AgentId("s1")] + 1e-9
        assert t[AgentId("s2")] <= 0.1 * p[AgentId("s2")] + 1e-9
        # And the honest agents collectively dominate the simplex.
        honest_mass = sum(t[AgentId(a)] for a in ["a0", "a1", "a2", "a3"])
        assert honest_mass > 0.99

    @pytest.mark.asyncio
    async def test_weak_monotonicity_of_positive_evidence(self) -> None:
        """More positive reports from honest agents can only weakly raise score.

        Holding all other evidence fixed, increasing the positive count
        from a pretrusted reporter to a target ``t`` must not decrease
        ``t``'s score.
        """
        base = EigenTrust(pretrusted=[AgentId("a0")])
        await _feed(
            base,
            [
                ("a0", "a1", "positive"),
                ("a1", "a2", "positive"),
                ("a2", "a3", "negative"),
            ],
        )
        await base.score(AgentId("a2"))
        s_before = (await base.score(AgentId("a2"))).score

        more = EigenTrust(pretrusted=[AgentId("a0")])
        await _feed(
            more,
            [
                ("a0", "a1", "positive"),
                ("a0", "a1", "positive"),  # honest agent gets more endorsement
                ("a1", "a2", "positive"),
                ("a2", "a3", "negative"),
            ],
        )
        await more.score(AgentId("a2"))
        s_after = (await more.score(AgentId("a2"))).score
        # Bumping a1's incoming honest trust should not reduce a2's
        # downstream score (a2 receives all its mass through a1).
        assert s_after + _EPS >= s_before

    @pytest.mark.asyncio
    async def test_alpha_one_recovers_pretrusted_distribution(self) -> None:
        """With ``alpha = 1`` the chain *is* the pre-trusted distribution.

        This is a sanity check on the teleport mixture: at ``alpha = 1``
        the local trust graph is ignored and ``t == p`` exactly.
        """
        trust = EigenTrust(
            pretrusted=[AgentId("a0"), AgentId("a1")],
            alpha=1.0,
        )
        # Throw in some misleading reports — none should matter.
        await _feed(
            trust,
            [
                ("a2", "a3", "positive"),
                ("a3", "a4", "positive"),
            ],
        )
        for i in range(5):
            await trust.score(AgentId(f"a{i}"))
        t = trust.trust_vector()
        assert math.isclose(t[AgentId("a0")], 0.5, abs_tol=1e-6)
        assert math.isclose(t[AgentId("a1")], 0.5, abs_tol=1e-6)
        for i in [2, 3, 4]:
            assert math.isclose(t[AgentId(f"a{i}")], 0.0, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# 3. Property-based tests (hypothesis)
# ---------------------------------------------------------------------------


@st.composite
def _trust_graph(draw: st.DrawFn) -> tuple[list[AgentId], list[tuple[str, str, str]]]:
    """Generate a small random trust graph for hypothesis."""
    n = draw(st.integers(min_value=2, max_value=6))
    agents = _agents(n)
    edge_count = draw(st.integers(min_value=0, max_value=2 * n))
    reports: list[tuple[str, str, str]] = []
    for _ in range(edge_count):
        i = draw(st.integers(min_value=0, max_value=n - 1))
        j = draw(st.integers(min_value=0, max_value=n - 1))
        kind = draw(st.sampled_from(["positive", "negative"]))
        if i == j:
            continue
        reports.append((f"a{i}", f"a{j}", kind))
    return agents, reports


class TestEigenTrustProperties:
    @settings(
        max_examples=80,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(_trust_graph())
    @pytest.mark.asyncio
    async def test_simplex_and_row_stochastic(
        self,
        graph: tuple[list[AgentId], list[tuple[str, str, str]]],
    ) -> None:
        agents, reports = graph
        trust = EigenTrust(pretrusted=[agents[0]])
        await _feed(trust, reports)
        for a in agents:
            await trust.score(a)
        _assert_on_simplex(trust.trust_vector())
        _assert_row_stochastic(trust.local_trust_matrix())

    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(_trust_graph())
    @pytest.mark.asyncio
    async def test_fixed_point(
        self,
        graph: tuple[list[AgentId], list[tuple[str, str, str]]],
    ) -> None:
        agents, reports = graph
        trust = EigenTrust(pretrusted=[agents[0]])
        await _feed(trust, reports)
        for a in agents:
            await trust.score(a)
        _assert_fixed_point(trust, tol_factor=100.0)

    @settings(
        max_examples=40,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(_trust_graph())
    @pytest.mark.asyncio
    async def test_pretrusted_mass_lower_bound(
        self,
        graph: tuple[list[AgentId], list[tuple[str, str, str]]],
    ) -> None:
        """Pre-trusted agents receive at least ``alpha * p_i`` mass.

        This is the obvious lower bound coming straight off the fixed-
        point equation: ``t = (1-a) C^T t + a p`` implies
        ``t_i >= a * p_i`` because the first term is non-negative.
        """
        agents, reports = graph
        pretrusted = [agents[0]]
        trust = EigenTrust(pretrusted=pretrusted, alpha=DEFAULT_ALPHA)
        await _feed(trust, reports)
        for a in agents:
            await trust.score(a)
        t = trust.trust_vector()
        p = trust._pretrusted_distribution(sorted(trust._agents))  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        for a in pretrusted:
            assert t[a] + 1e-9 >= DEFAULT_ALPHA * p[a]
