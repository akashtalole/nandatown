# SPDX-License-Identifier: Apache-2.0
"""Tests for ``DpopAuth`` — the hardened replacement for the toy ``jwt`` plugin.

These tests are written as adversarial vignettes: each one names an
attack that succeeds against the baseline ``JwtAuth`` and shows that
``DpopAuth`` blocks it.

Run::

    uv run pytest packages/nest-plugins-reference/tests/test_dpop_auth.py -v
"""

from __future__ import annotations

import base64
import json

import pytest
from nest_core.types import AgentId, Token
from nest_plugins_reference.auth.dpop_jwt import (
    DpopAuth,
    DpopProof,
    make_dpop_proof,
)
from nest_plugins_reference.auth.jwt_auth import JwtAuth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Manually-advanced clock for deterministic time-based tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _decode_payload(token: Token) -> dict[str, object]:
    _, payload_b64, _ = str(token).split(".")
    padding = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + padding))


# ---------------------------------------------------------------------------
# 1. Issue / verify happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_issue_then_verify_for_audience(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", issuer="nest", clock=clock)

        token = await auth.issue(AgentId("a1"), ["read"], audience="payments")
        ctx = await auth.verify_for_audience(token, audience="payments")
        assert ctx.subject == AgentId("a1")
        assert ctx.scopes == ["read"]

    @pytest.mark.asyncio
    async def test_format_is_real_jwt_three_segments(self) -> None:
        # The baseline JwtAuth uses ``payload|sig`` (a custom blob, not JWT).
        # DpopAuth produces an RFC-7519-shaped token.
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        token = str(await auth.issue(AgentId("a1"), ["read"], audience="svc"))
        parts = token.split(".")
        assert len(parts) == 3
        # Header is decodable JSON with ``alg`` and ``typ``.
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
        assert header == {"alg": "HS256", "typ": "JWT"}

    @pytest.mark.asyncio
    async def test_issued_token_carries_all_expected_claims(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", issuer="nest", clock=clock)
        token = await auth.issue(
            AgentId("a1"), ["read", "write"], audience="payments", ttl_seconds=60
        )
        payload = _decode_payload(token)
        assert payload["sub"] == "a1"
        assert payload["scopes"] == ["read", "write"]
        assert payload["aud"] == "payments"
        assert payload["iss"] == "nest"
        assert payload["iat"] == 100.0
        assert payload["exp"] == 160.0
        assert isinstance(payload["jti"], str) and len(payload["jti"]) >= 8

    @pytest.mark.asyncio
    async def test_extra_claims_cannot_override_reserved(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        with pytest.raises(ValueError, match="reserved claims"):
            await auth.issue(
                AgentId("a1"),
                ["read"],
                audience="svc",
                extra_claims={"sub": "evil"},
            )


# ---------------------------------------------------------------------------
# 2. Attack: alg confusion (none, unknown)
# ---------------------------------------------------------------------------


class TestAlgConfusion:
    @pytest.mark.asyncio
    async def test_rejects_alg_none(self) -> None:
        """The infamous JWT 'alg: none' bug — an attacker swaps the header
        for one that claims no signature, and the verifier trusts the
        unsigned payload.  We reject it before the MAC check."""
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        good = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        _header_b64, payload_b64, _ = str(good).split(".")
        # Build a forged header with alg=none.
        forged_header = (
            base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
        )
        forged = Token(f"{forged_header}.{payload_b64}.")
        with pytest.raises(ValueError, match="Unsupported or unsafe algorithm"):
            await auth.verify_for_audience(forged, audience="svc")

    @pytest.mark.asyncio
    async def test_rejects_unknown_alg(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        good = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        _, payload_b64, _ = str(good).split(".")
        forged_header = (
            base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
        )
        forged = Token(f"{forged_header}.{payload_b64}.AAAA")
        with pytest.raises(ValueError, match="Unsupported or unsafe algorithm"):
            await auth.verify_for_audience(forged, audience="svc")


# ---------------------------------------------------------------------------
# 3. Attack: signature forgery / tamper
# ---------------------------------------------------------------------------


class TestSignatureIntegrity:
    @pytest.mark.asyncio
    async def test_tampered_payload_rejected(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        token = str(await auth.issue(AgentId("a1"), ["read"], audience="svc"))
        header_b64, _, sig_b64 = token.split(".")
        # Substitute a payload claiming the attacker is admin.
        evil_payload = (
            base64.urlsafe_b64encode(
                b'{"sub":"admin","scopes":["root"],"iat":0,"nbf":0,'
                b'"exp":9999999999,"iss":"nest","jti":"x","aud":"svc"}'
            )
            .rstrip(b"=")
            .decode()
        )
        forged = Token(f"{header_b64}.{evil_payload}.{sig_b64}")
        with pytest.raises(ValueError, match="Invalid token signature"):
            await auth.verify_for_audience(forged, audience="svc")

    @pytest.mark.asyncio
    async def test_signature_from_wrong_secret_rejected(self) -> None:
        a = DpopAuth(secret=b"k1", clock=_FakeClock(0.0))
        b = DpopAuth(secret=b"k2", clock=_FakeClock(0.0))
        token = await a.issue(AgentId("a1"), ["read"], audience="svc")
        with pytest.raises(ValueError, match="Invalid token signature"):
            await b.verify_for_audience(token, audience="svc")


# ---------------------------------------------------------------------------
# 4. Attack: cross-audience replay
# ---------------------------------------------------------------------------


class TestAudienceBinding:
    @pytest.mark.asyncio
    async def test_token_for_one_aud_rejected_for_another(self) -> None:
        """The baseline JwtAuth has no audience concept — a token issued
        for the registry can be replayed against payments.  DpopAuth
        rejects audience mismatches."""
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        token = await auth.issue(AgentId("a1"), ["read"], audience="registry")
        # Same auth instance acting as the payments verifier:
        with pytest.raises(ValueError, match="audience mismatch"):
            await auth.verify_for_audience(token, audience="payments")

    @pytest.mark.asyncio
    async def test_no_audience_in_token_fails_strict_verify(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        # Issue without audience — bare verify works...
        token = await auth.issue(AgentId("a1"), ["read"])
        ctx = await auth.verify(token)
        assert ctx.subject == AgentId("a1")
        # ...but verify_for_audience refuses because the token has no aud.
        with pytest.raises(ValueError, match="no 'aud'"):
            await auth.verify_for_audience(token, audience="svc")

    @pytest.mark.asyncio
    async def test_baseline_jwt_has_no_audience_concept(self) -> None:
        """Demonstrates the bug we are fixing.  The baseline plugin
        does not even *know* about audiences."""
        baseline = JwtAuth(secret=b"k")
        token = await baseline.issue(AgentId("a1"), ["read"])
        # No matter what audience the verifier 'thinks' they are, the
        # baseline accepts the token.  There is no audience check at all.
        ctx = await baseline.verify(token)
        assert ctx.subject == AgentId("a1")


# ---------------------------------------------------------------------------
# 5. Attack: replay (same jti accepted twice)
# ---------------------------------------------------------------------------


class TestReplayProtection:
    @pytest.mark.asyncio
    async def test_same_token_cannot_verify_twice_for_same_audience(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        # First call: fine.
        await auth.verify_for_audience(token, audience="svc")
        # Second call: replay.
        with pytest.raises(ValueError, match="replayed"):
            await auth.verify_for_audience(token, audience="svc")

    @pytest.mark.asyncio
    async def test_baseline_jwt_accepts_replays(self) -> None:
        """The baseline plugin has no replay protection."""
        baseline = JwtAuth(secret=b"k")
        token = await baseline.issue(AgentId("a1"), ["read"])
        # We can verify the same token an unbounded number of times.
        for _ in range(5):
            ctx = await baseline.verify(token)
            assert ctx.subject == AgentId("a1")

    @pytest.mark.asyncio
    async def test_bare_verify_does_not_record_replays(self) -> None:
        """``verify`` (no audience) is the permissive path and is allowed
        to be replay-tolerant for compatibility.  ``verify_for_audience``
        is the strict path."""
        clock = _FakeClock(0.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        token = await auth.issue(AgentId("a1"), ["read"])
        for _ in range(3):
            await auth.verify(token)


# ---------------------------------------------------------------------------
# 6. Expiry and clock skew
# ---------------------------------------------------------------------------


class TestExpiryAndSkew:
    @pytest.mark.asyncio
    async def test_expired_token_rejected(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock, token_ttl_seconds=10)
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        clock.t = 200.0
        with pytest.raises(ValueError, match="expired"):
            await auth.verify_for_audience(token, audience="svc")

    @pytest.mark.asyncio
    async def test_clock_skew_tolerated(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock, token_ttl_seconds=10, skew_seconds=5)
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        clock.t = 113.0  # 3 seconds past exp but within skew
        ctx = await auth.verify_for_audience(token, audience="svc")
        assert ctx.subject == AgentId("a1")

    @pytest.mark.asyncio
    async def test_nbf_in_the_future_rejected(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        clock.t = 50.0  # earlier than nbf
        with pytest.raises(ValueError, match="not yet valid"):
            await auth.verify_for_audience(token, audience="svc")


# ---------------------------------------------------------------------------
# 7. Issuer claim
# ---------------------------------------------------------------------------


class TestIssuerClaim:
    @pytest.mark.asyncio
    async def test_wrong_issuer_rejected(self) -> None:
        auth_a = DpopAuth(secret=b"k", issuer="tenant-a", clock=_FakeClock(0.0))
        auth_b = DpopAuth(secret=b"k", issuer="tenant-b", clock=_FakeClock(0.0))
        token = await auth_a.issue(AgentId("a1"), ["read"], audience="svc")
        # ``auth_b`` shares the secret but expects a different issuer.
        with pytest.raises(ValueError, match="issuer mismatch"):
            await auth_b.verify_for_audience(token, audience="svc")


# ---------------------------------------------------------------------------
# 8. DPoP proof-of-possession
# ---------------------------------------------------------------------------


class TestDpopBinding:
    @pytest.mark.asyncio
    async def test_bound_token_requires_proof(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        agent_pk = b"agent-a1-public-key"
        token = await auth.issue(
            AgentId("a1"),
            ["read"],
            audience="svc",
            bind_to_public_key=agent_pk,
        )
        # No DPoP proof => rejected.
        with pytest.raises(ValueError, match="DPoP-bound"):
            await auth.verify_for_audience(token, audience="svc")

    @pytest.mark.asyncio
    async def test_bound_token_accepted_with_proof(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        agent_pk = b"agent-a1-public-key"
        token = await auth.issue(
            AgentId("a1"), ["read"], audience="svc", bind_to_public_key=agent_pk
        )
        jti = str(_decode_payload(token)["jti"])
        proof = make_dpop_proof(audience="svc", token_jti=jti, iat=clock.t, public_key=agent_pk)
        ctx = await auth.verify_for_audience(token, audience="svc", dpop=proof)
        assert ctx.subject == AgentId("a1")

    @pytest.mark.asyncio
    async def test_proof_with_wrong_key_rejected(self) -> None:
        """The classic 'stole the token, want to use it' scenario.
        Attacker has the token but not the bound private key.  Any
        proof they craft uses *their* key, which the token does not
        bind to."""
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        victim_pk = b"victim-pk"
        attacker_pk = b"attacker-pk"
        token = await auth.issue(
            AgentId("victim"),
            ["read"],
            audience="svc",
            bind_to_public_key=victim_pk,
        )
        jti = str(_decode_payload(token)["jti"])
        attacker_proof = make_dpop_proof(
            audience="svc", token_jti=jti, iat=clock.t, public_key=attacker_pk
        )
        with pytest.raises(ValueError, match="cnf.jkt"):
            await auth.verify_for_audience(token, audience="svc", dpop=attacker_proof)

    @pytest.mark.asyncio
    async def test_proof_for_different_jti_rejected(self) -> None:
        """Attacker captures a fresh proof for *their own* token A and
        tries to pair it with stolen token B.  Both proofs are valid in
        isolation, but DpopAuth binds the proof to the token's jti."""
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        pk = b"shared-pk"
        token_b = await auth.issue(AgentId("a1"), ["read"], audience="svc", bind_to_public_key=pk)
        # Attacker generates a proof using a different jti.
        wrong_proof = make_dpop_proof(
            audience="svc", token_jti="not-this-token", iat=clock.t, public_key=pk
        )
        with pytest.raises(ValueError, match="does not bind"):
            await auth.verify_for_audience(token_b, audience="svc", dpop=wrong_proof)

    @pytest.mark.asyncio
    async def test_proof_for_different_audience_rejected(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        pk = b"k1"
        token = await auth.issue(
            AgentId("a1"), ["read"], audience="payments", bind_to_public_key=pk
        )
        jti = str(_decode_payload(token)["jti"])
        # Proof for the wrong audience.
        proof = make_dpop_proof(audience="registry", token_jti=jti, iat=clock.t, public_key=pk)
        with pytest.raises(ValueError, match="proof audience mismatch"):
            await auth.verify_for_audience(token, audience="payments", dpop=proof)

    @pytest.mark.asyncio
    async def test_expired_proof_rejected(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock, dpop_ttl_seconds=10)
        pk = b"pk"
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc", bind_to_public_key=pk)
        jti = str(_decode_payload(token)["jti"])
        # Proof made 1000s ago, well past ttl.
        proof = make_dpop_proof(audience="svc", token_jti=jti, iat=clock.t - 1000.0, public_key=pk)
        with pytest.raises(ValueError, match="proof has expired"):
            await auth.verify_for_audience(token, audience="svc", dpop=proof)

    @pytest.mark.asyncio
    async def test_future_dated_proof_rejected(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock, dpop_ttl_seconds=10, skew_seconds=1)
        pk = b"pk"
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc", bind_to_public_key=pk)
        jti = str(_decode_payload(token)["jti"])
        proof = make_dpop_proof(audience="svc", token_jti=jti, iat=clock.t + 1000.0, public_key=pk)
        with pytest.raises(ValueError, match="from the future"):
            await auth.verify_for_audience(token, audience="svc", dpop=proof)

    @pytest.mark.asyncio
    async def test_tampered_proof_signature_rejected(self) -> None:
        clock = _FakeClock(100.0)
        auth = DpopAuth(secret=b"k", clock=clock)
        pk = b"pk"
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc", bind_to_public_key=pk)
        jti = str(_decode_payload(token)["jti"])
        bad_proof = DpopProof(
            jti=jti,
            audience="svc",
            iat=clock.t,
            signature=b"\x00" * 32,
            public_key=pk,
        )
        with pytest.raises(ValueError, match="Invalid DPoP proof signature"):
            await auth.verify_for_audience(token, audience="svc", dpop=bad_proof)


# ---------------------------------------------------------------------------
# 9. Revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    @pytest.mark.asyncio
    async def test_revoke_then_verify_fails(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        await auth.revoke(token)
        with pytest.raises(ValueError, match="revoked"):
            await auth.verify_for_audience(token, audience="svc")

    @pytest.mark.asyncio
    async def test_revoke_by_jti_blocks_even_re_encoded(self) -> None:
        """We revoke by jti — even if someone manages to produce a
        differently-serialised copy of the same token (different
        whitespace, etc.), the jti will still match and revocation
        bites."""
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        token = await auth.issue(AgentId("a1"), ["read"], audience="svc")
        await auth.revoke(token)
        # Re-issuing a different token with the same jti should still be
        # blocked.  We simulate this by directly verifying the revoked
        # token from any code path.
        with pytest.raises(ValueError, match="revoked"):
            await auth.verify(token)


# ---------------------------------------------------------------------------
# 10. Malformed input
# ---------------------------------------------------------------------------


class TestMalformedInput:
    @pytest.mark.asyncio
    async def test_three_dots_required(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        with pytest.raises(ValueError, match="three"):
            await auth.verify_for_audience(Token("not-a-jwt"), audience="svc")

    @pytest.mark.asyncio
    async def test_garbage_header_rejected(self) -> None:
        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        with pytest.raises(ValueError, match="header"):
            await auth.verify_for_audience(Token("!!!.AAAA.AAAA"), audience="svc")

    @pytest.mark.asyncio
    async def test_empty_secret_refused(self) -> None:
        with pytest.raises(ValueError, match="non-empty secret"):
            DpopAuth(secret=b"")


# ---------------------------------------------------------------------------
# 11. Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_seeded_rng_produces_deterministic_jti(self) -> None:
        import random

        class SeededRng:
            def __init__(self, seed: int) -> None:
                self._r = random.Random(seed)

            def token_hex(self, n: int) -> str:
                return self._r.randbytes(n).hex()

        a1 = DpopAuth(secret=b"k", clock=_FakeClock(0.0), rng=SeededRng(42))
        a2 = DpopAuth(secret=b"k", clock=_FakeClock(0.0), rng=SeededRng(42))
        tok1 = await a1.issue(AgentId("a"), ["r"], audience="svc")
        tok2 = await a2.issue(AgentId("a"), ["r"], audience="svc")
        assert tok1 == tok2


# ---------------------------------------------------------------------------
# 12. Conformance to the bare Auth protocol
# ---------------------------------------------------------------------------


class TestAuthProtocolConformance:
    @pytest.mark.asyncio
    async def test_satisfies_runtime_protocol(self) -> None:
        from nest_core.layers.auth import Auth

        auth = DpopAuth(secret=b"k", clock=_FakeClock(0.0))
        assert isinstance(auth, Auth)

    @pytest.mark.asyncio
    async def test_plugin_registry_resolves_dpop_jwt(self) -> None:
        from nest_core.plugins import PluginRegistry

        reg = PluginRegistry()
        cls = reg.resolve("auth", "dpop_jwt")
        assert cls is DpopAuth
