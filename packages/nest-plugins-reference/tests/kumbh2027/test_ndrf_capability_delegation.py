# SPDX-License-Identifier: Apache-2.0
"""Tests for NDRF capability delegation auth plugin.

Adversarial invariants verified:
* Tokens cannot grant scopes their issuer doesn't hold.
* Delegation depth is capped at MAX_DEPTH.
* Revoked parent tokens invalidate all child tokens.
* zone:close tokens expire at window_end even if a longer expiry is requested.
* Expired tokens are rejected.
* Tampered token signatures are rejected.
"""

from __future__ import annotations

import pytest
from nest_core.types import AgentId, Token
from nest_plugins_reference.kumbh2027.ndrf_capability_delegation import (
    MAX_DEPTH,
    NDRFCapabilityDelegation,
)

_WINDOW_END = 57_600.0  # 16-hour bathing window


# ---------------------------------------------------------------------------
# Basic issue / verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_and_verify() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    token = await auth.issue(AgentId("ndrf-1"), ["zone:close", "zone:hold"])
    ctx = await auth.verify(token)
    assert ctx.subject == AgentId("ndrf-1")
    assert "zone:close" in ctx.scopes
    assert "zone:hold" in ctx.scopes


@pytest.mark.asyncio
async def test_zone_close_capped_at_window_end() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    # Request exp far beyond window
    token = await auth.issue(AgentId("ndrf-1"), ["zone:close"], exp=999_999.0)
    ctx = await auth.verify(token)
    assert ctx.expires_at is not None
    assert ctx.expires_at <= _WINDOW_END


# ---------------------------------------------------------------------------
# Delegation chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_subset_of_scopes() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    root = await auth.issue(AgentId("ndrf-commander"), ["zone:close", "zone:hold"])
    child = await auth.delegate(root, AgentId("zone-commander-1"), ["zone:close"])
    ctx = await auth.verify(child)
    assert ctx.subject == AgentId("zone-commander-1")
    assert "zone:close" in ctx.scopes
    assert "zone:hold" not in ctx.scopes


@pytest.mark.asyncio
async def test_cannot_delegate_scope_not_held() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    root = await auth.issue(AgentId("ndrf-1"), ["zone:hold"])  # no zone:close
    with pytest.raises(ValueError, match="scopes not held"):
        await auth.delegate(root, AgentId("zone-cmd"), ["zone:close"])


@pytest.mark.asyncio
async def test_delegation_depth_capped() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    token = await auth.issue(AgentId("root"), ["zone:close"])
    # Chain: root → depth1 → depth2 → depth3 (MAX_DEPTH) → fail
    for i in range(MAX_DEPTH):
        token = await auth.delegate(token, AgentId(f"agent-{i}"), ["zone:close"])
    with pytest.raises(ValueError, match="maximum"):
        await auth.delegate(token, AgentId("too-deep"), ["zone:close"])


# ---------------------------------------------------------------------------
# Revocation cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoking_parent_invalidates_child() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    parent = await auth.issue(AgentId("ndrf-1"), ["zone:close"])
    child = await auth.delegate(parent, AgentId("zone-cmd"), ["zone:close"])

    await auth.revoke(parent)

    with pytest.raises(ValueError):
        await auth.verify(child)


@pytest.mark.asyncio
async def test_revoking_child_does_not_affect_parent() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    parent = await auth.issue(AgentId("ndrf-1"), ["zone:close"])
    child = await auth.delegate(parent, AgentId("zone-cmd"), ["zone:close"])

    await auth.revoke(child)

    # Parent should still be valid
    ctx = await auth.verify(parent)
    assert ctx.subject == AgentId("ndrf-1")


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_token_rejected() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    token = await auth.issue(AgentId("ndrf-1"), ["zone:hold"], exp=10.0)
    # Now check at t=100 (past expiry)
    with pytest.raises(ValueError, match="expired"):
        await auth.verify(token, now=100.0)


@pytest.mark.asyncio
async def test_non_expired_token_valid_at_boundary() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    token = await auth.issue(AgentId("ndrf-1"), ["zone:hold"], exp=100.0)
    # At exactly t=100 it should still be valid (boundary inclusive)
    ctx = await auth.verify(token, now=100.0)
    assert ctx.subject == AgentId("ndrf-1")


# ---------------------------------------------------------------------------
# Tamper resistance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tampered_token_rejected() -> None:
    auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
    token = await auth.issue(AgentId("ndrf-1"), ["zone:hold"])
    # Flip one character in the base64 payload
    raw = str(token)
    b64, sig = raw.rsplit("|", 1)
    # Mutate a character in the middle of the payload
    mid = len(b64) // 2
    corrupted_b64 = b64[:mid] + ("A" if b64[mid] != "A" else "B") + b64[mid + 1 :]
    bad_token = Token(f"{corrupted_b64}|{sig}")
    with pytest.raises(ValueError):
        await auth.verify(bad_token)


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

from hypothesis import given, settings
from hypothesis import strategies as st

_ALL_SCOPES = ["zone:close", "zone:hold", "ambulance:dispatch", "flood:alert", "zone:monitor"]


@given(
    scopes=st.lists(st.sampled_from(_ALL_SCOPES), min_size=1, max_size=4, unique=True),
    subset=st.lists(st.sampled_from(_ALL_SCOPES), min_size=1, max_size=4, unique=True),
)
@settings(max_examples=50)
def test_scope_containment_enforced(scopes: list[str], subset: list[str]) -> None:
    """A delegated token must never hold more scopes than its parent."""
    import asyncio

    extra = set(subset) - set(scopes)
    if not extra:
        return  # No violation possible with this subset

    async def _run() -> None:
        auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
        parent = await auth.issue(AgentId("root"), scopes)
        try:
            await auth.delegate(parent, AgentId("child"), subset)
            assert False, f"Should have raised ValueError for extra scopes {extra}"
        except ValueError as e:
            assert "scopes not held" in str(e).lower() or "scopes" in str(e).lower()

    asyncio.run(_run())


@given(
    n_delegates=st.integers(min_value=0, max_value=MAX_DEPTH - 1),
)
@settings(max_examples=30)
def test_delegation_chain_within_depth_always_valid(n_delegates: int) -> None:
    """A chain of n_delegates <= MAX_DEPTH-1 must always verify successfully."""
    import asyncio

    async def _run() -> None:
        auth = NDRFCapabilityDelegation(window_end=_WINDOW_END, clock=0.0)
        token = await auth.issue(AgentId("root"), ["zone:hold"])
        for i in range(n_delegates):
            token = await auth.delegate(token, AgentId(f"agent-{i}"), ["zone:hold"])
        ctx = await auth.verify(token)
        assert "zone:hold" in ctx.scopes

    asyncio.run(_run())


@given(exp=st.floats(min_value=1.0, max_value=1000.0, allow_nan=False))
@settings(max_examples=40)
def test_expired_token_always_rejected(exp: float) -> None:
    """A token verified at now > exp must always raise ValueError."""
    import asyncio

    async def _run() -> None:
        auth = NDRFCapabilityDelegation(window_end=max(exp + 1, _WINDOW_END), clock=0.0)
        token = await auth.issue(AgentId("agent-1"), ["zone:hold"], exp=exp)
        try:
            await auth.verify(token, now=exp + 1.0)
            assert False, "Must raise ValueError for expired token"
        except ValueError as e:
            assert "expired" in str(e).lower()

    asyncio.run(_run())
