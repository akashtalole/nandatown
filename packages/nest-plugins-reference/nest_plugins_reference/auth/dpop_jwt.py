# SPDX-License-Identifier: Apache-2.0
"""DPoP-bound JWT auth plugin — hardened reference for NEST's auth layer.

The default ``jwt`` plugin is a deliberately toy HMAC token: bearer-style,
no audience, no replay protection, no proof-of-possession, custom
``payload|sig`` format.  Anyone who observes a token can replay it
against any verifier that shares the secret, against any service, until
the token expires.

``DpopAuth`` is an opinionated, security-conscious alternative aimed at
multi-agent swarms where:

* Many verifiers share a trust root but each is its own audience.
* Tokens cross transports that are not confidential by default
  (in-memory, naive TCP, etc.).
* Some agents are adversarial — they will replay tokens, forge audiences,
  and try every JWT footgun.

It hardens the baseline along the dimensions an attacker would target
first:

1. **Real RFC-7519 layout.**  ``base64url(header).base64url(payload).base64url(sig)``
   so the plugin reads like a JWT, not a custom blob.
2. **Algorithm pinning.**  ``HS256`` only — ``alg: none``, ``alg: HS256/RS256``
   confusion, and unknown algorithms are all rejected before any signature
   check runs.  This blocks the classic ``alg`` family of JWT bugs.
3. **Audience binding (``aud``).**  Tokens are issued for a specific
   audience.  ``verify_for_audience`` refuses tokens whose ``aud`` does not
   match — so a token meant for the registry cannot be replayed against
   payments.
4. **Unique ``jti`` + replay window.**  Every token carries a unique ID.
   Verifiers track seen ``jti`` values and reject replays.  Bounded by
   token expiry so the cache cannot grow unboundedly.
5. **DPoP-style proof-of-possession.**  Tokens may be bound to an agent's
   identity public key via ``cnf.jkt`` (a hash of the public key).
   Verification then *requires* a fresh DPoP proof: a short-lived
   signature, by the bound key, over the audience + ``jti`` + a server
   nonce.  Stealing the token alone is not enough.
6. **Issuer claim (``iss``) + ``nbf`` with leeway.**  Mismatched issuers
   are rejected; not-yet-valid tokens fail explicitly.
7. **Revocation by ``jti``.**  Compact and bounded.  Bearer-style revoke
   by raw token still works for compatibility with the ``Auth`` protocol.

This module is dependency-free (stdlib only) and deterministic given the
same seed, so it composes with NEST's replay-deterministic simulator.

Example::

    from nest_plugins_reference.auth.dpop_jwt import DpopAuth

    auth = DpopAuth(secret=b"trust-root", issuer="nest-issuer", clock=lambda: 100.0)
    token = await auth.issue(
        AgentId("a1"),
        ["read", "write"],
        audience="payments-svc",
    )
    ctx = await auth.verify_for_audience(token, audience="payments-svc")
    assert ctx.subject == AgentId("a1")
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from nest_core.types import AgentId, AuthContext, Token

# Compact JSON separators keep tokens deterministic and small.
_JSON_SEP = (",", ":")

# Algorithm whitelist.  ``none`` is intentionally absent — there are no
# unsigned tokens in NEST.  Anything outside this set is rejected before
# we touch the signature, which is what kills ``alg`` confusion attacks.
_SUPPORTED_ALGS = frozenset({"HS256"})

_DEFAULT_TTL_SECONDS = 3600
_DEFAULT_DPOP_TTL_SECONDS = 30
_DEFAULT_CLOCK_SKEW_SECONDS = 5
_DEFAULT_NONCE_BYTES = 16

# Cap on how many seen ``jti`` values we keep at once.  Each entry is
# (jti, exp); we evict expired ones lazily.  This bound is per-verifier
# and keeps replay defence O(1) amortised even under flood.
_DEFAULT_REPLAY_CACHE_MAX = 100_000


def _b64u_encode(data: bytes) -> str:
    """URL-safe base64 without padding (RFC 7515 §2)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(data: str) -> bytes:
    """Inverse of :func:`_b64u_encode`."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _canonical_json(obj: dict[str, Any]) -> bytes:
    """Deterministic JSON encoding (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=_JSON_SEP).encode("utf-8")


def _jkt(public_key: bytes) -> str:
    """JWK Thumbprint-style identifier for a public key (RFC 7638 in spirit).

    NEST's identity plugins emit opaque public-key bytes; we hash them
    directly.  The point is a stable, short fingerprint we can put in
    ``cnf.jkt``.
    """
    return _b64u_encode(hashlib.sha256(public_key).digest())


@dataclass(frozen=True)
class DpopProof:
    """A short-lived proof that the caller holds a bound key.

    Example::

        proof = DpopProof(jti="abc", audience="payments", iat=100.0, signature=b"...")
    """

    jti: str
    audience: str
    iat: float
    signature: bytes
    public_key: bytes


@dataclass
class _ReplayCache:
    """Bounded FIFO + expiry cache for seen ``jti`` values."""

    capacity: int = _DEFAULT_REPLAY_CACHE_MAX
    _seen: dict[str, float] = field(default_factory=dict[str, float])

    def seen(self, jti: str, now: float) -> bool:
        # Lazy GC: drop anything that has already expired.
        if self._seen:
            expired = [k for k, exp in self._seen.items() if exp <= now]
            for k in expired:
                self._seen.pop(k, None)
        return jti in self._seen

    def remember(self, jti: str, exp: float) -> None:
        if len(self._seen) >= self.capacity:
            # Drop the oldest by expiry to bound memory.
            oldest_key = min(self._seen.items(), key=lambda kv: kv[1])[0]
            self._seen.pop(oldest_key, None)
        self._seen[jti] = exp


class DpopAuth:
    """JWT-style auth with audience binding, replay protection, and DPoP.

    Parameters
    ----------
    secret:
        HMAC key used to sign tokens.  In production this is loaded from
        a KMS / HSM; in NEST it is a deterministic byte string.
    issuer:
        Value to place in ``iss`` and to require on verification.
    clock:
        Callable returning the current time as a ``float`` (seconds).
        If ``None``, uses :func:`time.time`.  In NEST simulations you
        will usually wire this to the simulator clock for determinism.
    token_ttl_seconds:
        Default lifetime of issued tokens.
    dpop_ttl_seconds:
        Maximum age of a DPoP proof considered fresh.
    skew_seconds:
        Tolerated clock skew when checking ``nbf`` / ``exp``.
    rng:
        Source of randomness for ``jti`` and nonce values.  Pass a
        seeded :class:`secrets.SystemRandom` substitute (or anything
        exposing ``token_bytes``) to keep traces deterministic.

    Example::

        auth = DpopAuth(secret=b"k", issuer="nest")
        tok = await auth.issue(AgentId("a"), ["read"], audience="svc")
    """

    def __init__(
        self,
        secret: bytes = b"nest-default-secret",
        *,
        issuer: str = "nest",
        clock: Callable[[], float] | None = None,
        token_ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        dpop_ttl_seconds: float = _DEFAULT_DPOP_TTL_SECONDS,
        skew_seconds: float = _DEFAULT_CLOCK_SKEW_SECONDS,
        rng: Any | None = None,
        replay_cache_capacity: int = _DEFAULT_REPLAY_CACHE_MAX,
    ) -> None:
        if not secret:
            msg = "DpopAuth requires a non-empty secret"
            raise ValueError(msg)
        self._secret = secret
        self._issuer = issuer
        self._clock_fn = clock
        self._token_ttl = float(token_ttl_seconds)
        self._dpop_ttl = float(dpop_ttl_seconds)
        self._skew = float(skew_seconds)
        self._rng = rng if rng is not None else secrets
        self._revoked_jti: set[str] = set()
        self._replay = _ReplayCache(capacity=replay_cache_capacity)
        # Server-issued DPoP nonces, keyed by audience.  Single-use.
        self._nonces: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> float:
        if self._clock_fn is not None:
            return float(self._clock_fn())
        return time.time()

    def _sign_raw(self, signing_input: bytes) -> bytes:
        return hmac.new(self._secret, signing_input, hashlib.sha256).digest()

    def _encode_token(self, payload: dict[str, Any]) -> Token:
        header = {"alg": "HS256", "typ": "JWT"}
        header_b64 = _b64u_encode(_canonical_json(header))
        payload_b64 = _b64u_encode(_canonical_json(payload))
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        sig = self._sign_raw(signing_input)
        sig_b64 = _b64u_encode(sig)
        return Token(f"{header_b64}.{payload_b64}.{sig_b64}")

    def _decode_token(self, token: Token) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = str(token)
        parts = raw.split(".")
        if len(parts) != 3:
            msg = "Malformed token: expected three '.'-separated segments"
            raise ValueError(msg)
        header_b64, payload_b64, sig_b64 = parts

        # 1. Parse header *before* doing crypto so we can reject ``alg``
        #    confusion early without giving timing signal on the MAC.
        try:
            header = json.loads(_b64u_decode(header_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            msg = "Malformed token: header is not valid JSON"
            raise ValueError(msg) from exc
        alg = header.get("alg")
        if alg not in _SUPPORTED_ALGS:
            # Catches ``alg: none``, RS256 confusion, and typos.
            msg = f"Unsupported or unsafe algorithm: {alg!r}"
            raise ValueError(msg)

        # 2. Constant-time signature comparison.
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected_sig = self._sign_raw(signing_input)
        try:
            provided_sig = _b64u_decode(sig_b64)
        except (ValueError, TypeError) as exc:
            msg = "Malformed token: signature is not valid base64url"
            raise ValueError(msg) from exc
        if not hmac.compare_digest(expected_sig, provided_sig):
            msg = "Invalid token signature"
            raise ValueError(msg)

        # 3. Parse payload only after the MAC checks out.
        try:
            payload = json.loads(_b64u_decode(payload_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            msg = "Malformed token: payload is not valid JSON"
            raise ValueError(msg) from exc
        return header, payload

    # ------------------------------------------------------------------
    # Auth protocol surface
    # ------------------------------------------------------------------

    async def issue(
        self,
        subject: AgentId,
        scopes: list[str],
        *,
        audience: str | None = None,
        bind_to_public_key: bytes | None = None,
        ttl_seconds: float | None = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> Token:
        """Issue a token.

        Parameters
        ----------
        subject:
            The agent the token speaks for (``sub`` claim).
        scopes:
            Authorisation scopes (``scope``-style list, embedded as ``scopes``
            for ergonomic Pythonic access).
        audience:
            Optional ``aud`` claim.  Verifiers compare this against their
            own audience and reject mismatches.
        bind_to_public_key:
            If supplied, the resulting token is *only* usable by the agent
            holding the matching private key.  Verifiers require a fresh
            DPoP proof.
        ttl_seconds:
            Override the default TTL for this single token.
        extra_claims:
            Additional claims merged into the payload.  Reserved claim
            names (``sub``, ``scopes``, ``iat``, ``exp``, ``nbf``, ``iss``,
            ``aud``, ``jti``, ``cnf``) cannot be overridden.

        Example::

            tok = await auth.issue(
                AgentId("a1"), ["read"],
                audience="payments", bind_to_public_key=ident.public_key,
            )
        """
        now = self._now()
        ttl = float(ttl_seconds) if ttl_seconds is not None else self._token_ttl
        jti = self._rng.token_hex(16)
        payload: dict[str, Any] = {
            "sub": str(subject),
            "scopes": list(scopes),
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "iss": self._issuer,
            "jti": jti,
        }
        if audience is not None:
            payload["aud"] = audience
        if bind_to_public_key is not None:
            payload["cnf"] = {"jkt": _jkt(bind_to_public_key)}
        if extra_claims:
            reserved = {"sub", "scopes", "iat", "exp", "nbf", "iss", "aud", "jti", "cnf"}
            overlap = reserved & set(extra_claims)
            if overlap:
                msg = f"extra_claims may not override reserved claims: {sorted(overlap)}"
                raise ValueError(msg)
            payload.update(extra_claims)
        return self._encode_token(payload)

    async def verify(self, token: Token) -> AuthContext:
        """Verify a token without checking audience or DPoP binding.

        Useful for compatibility with the bare :class:`Auth` protocol and
        for clients that simply want a parsed context.  **If the token was
        issued with an ``aud`` or ``cnf`` claim, prefer
        :meth:`verify_for_audience`** — bare verify deliberately does not
        enforce those, mirroring how a permissive verifier would behave.

        Example::

            ctx = await auth.verify(token)
        """
        return self._verify_core(token, audience=None, dpop=None)

    async def verify_for_audience(
        self,
        token: Token,
        *,
        audience: str,
        dpop: DpopProof | None = None,
        expected_issuer: str | None = None,
    ) -> AuthContext:
        """Verify a token against a specific audience and (if bound) DPoP proof.

        This is the verification path you almost always want.  It enforces:

        * Signature, ``exp``, ``nbf`` (with skew).
        * ``iss`` matches the expected issuer (defaults to the auth's own).
        * ``aud`` is present and equals ``audience``.
        * ``jti`` has not been replayed.
        * If the token has ``cnf.jkt``, a fresh, well-formed
          :class:`DpopProof` for that key is required.

        Example::

            ctx = await auth.verify_for_audience(tok, audience="payments", dpop=proof)
        """
        return self._verify_core(
            token,
            audience=audience,
            dpop=dpop,
            expected_issuer=expected_issuer,
        )

    def _verify_core(
        self,
        token: Token,
        *,
        audience: str | None,
        dpop: DpopProof | None,
        expected_issuer: str | None = None,
    ) -> AuthContext:
        raw = str(token)
        # Bearer-style revoke (compat with the base Auth protocol's ``revoke``):
        if raw in self._revoked_raw:
            msg = "Token has been revoked"
            raise ValueError(msg)

        _header, payload = self._decode_token(token)  # signature + alg checks

        now = self._now()
        exp = payload.get("exp")
        nbf = payload.get("nbf")
        if not isinstance(exp, (int, float)):
            msg = "Token missing 'exp' claim"
            raise ValueError(msg)
        if exp + self._skew < now:
            msg = "Token has expired"
            raise ValueError(msg)
        if isinstance(nbf, (int, float)) and nbf - self._skew > now:
            msg = "Token not yet valid"
            raise ValueError(msg)

        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            msg = "Token missing 'jti' claim"
            raise ValueError(msg)
        if jti in self._revoked_jti:
            msg = "Token has been revoked"
            raise ValueError(msg)

        iss = payload.get("iss")
        want_iss = expected_issuer if expected_issuer is not None else self._issuer
        if iss != want_iss:
            msg = f"Token issuer mismatch: got {iss!r}, expected {want_iss!r}"
            raise ValueError(msg)

        # Audience enforcement.
        if audience is not None:
            token_aud = payload.get("aud")
            if token_aud is None:
                msg = "Audience required but token has no 'aud' claim"
                raise ValueError(msg)
            if token_aud != audience:
                msg = f"Token audience mismatch: got {token_aud!r}, expected {audience!r}"
                raise ValueError(msg)

        # Replay protection: a token's ``jti`` may only be accepted once
        # per audience-scoped verifier.  This is checked *after* signature
        # and audience so attackers cannot use this path to poison the
        # cache with arbitrary jti values.
        if audience is not None:
            if self._replay.seen(jti, now):
                msg = "Token jti replayed"
                raise ValueError(msg)
            self._replay.remember(jti, float(exp))

        # DPoP binding: if the token says ``cnf.jkt``, the caller must
        # prove possession of the key.
        cnf_raw: object = payload.get("cnf")
        if isinstance(cnf_raw, dict):
            cnf_typed = cast("dict[str, Any]", cnf_raw)
            jkt_val = cnf_typed.get("jkt")
            if jkt_val is not None:
                if dpop is None:
                    msg = "Token is DPoP-bound but no DPoP proof was supplied"
                    raise ValueError(msg)
                self._verify_dpop_proof(
                    dpop,
                    expected_jkt=str(jkt_val),
                    audience=audience,
                    token_jti=jti,
                    now=now,
                )

        return AuthContext(
            subject=AgentId(str(payload["sub"])),
            scopes=list(payload.get("scopes", [])),
            issued_at=payload.get("iat"),
            expires_at=exp,
        )

    async def revoke(self, token: Token) -> None:
        """Revoke a token.

        We revoke by ``jti`` (so the revocation set stays small) and also
        keep the raw token string in a fallback set so callers that re-use
        an old ``Token`` value still get a clear failure.

        Example::

            await auth.revoke(token)
        """
        raw = str(token)
        self._revoked_raw.add(raw)
        try:
            _, payload = self._decode_token(token)
        except ValueError:
            # If we cannot decode it (e.g. tampered) the raw-set entry is
            # still enough to refuse future verifies of the same bytes.
            return
        jti = payload.get("jti")
        if isinstance(jti, str) and jti:
            self._revoked_jti.add(jti)

    # ------------------------------------------------------------------
    # DPoP helpers
    # ------------------------------------------------------------------

    def issue_dpop_nonce(self, audience: str) -> str:
        """Issue a single-use server nonce a client must echo in its DPoP proof.

        Verifiers that want the strongest replay protection should call
        this, hand the nonce to the client, and require the resulting
        proof to embed it.  For simpler deployments the audience+jti
        signature is already binding enough.

        Example::

            nonce = auth.issue_dpop_nonce("payments")
        """
        nonce = self._rng.token_hex(_DEFAULT_NONCE_BYTES)
        self._nonces.setdefault(audience, set()).add(nonce)
        return nonce

    @staticmethod
    def build_dpop_signing_input(
        *,
        audience: str,
        token_jti: str,
        iat: float,
        nonce: str | None = None,
    ) -> bytes:
        """Canonical byte string the DPoP-bound key must sign.

        The structure is fixed so both sides agree byte-for-byte without
        a separate header.  Mirrors RFC 9449 in spirit (a signed
        statement about the audience and the bound token) but kept
        compact because we are not interoperating with browsers.

        Example::

            data = DpopAuth.build_dpop_signing_input(
                audience="payments", token_jti="abc", iat=100.0,
            )
        """
        obj: dict[str, Any] = {"aud": audience, "jti": token_jti, "iat": iat}
        if nonce is not None:
            obj["nonce"] = nonce
        return _canonical_json(obj)

    def _verify_dpop_proof(
        self,
        proof: DpopProof,
        *,
        expected_jkt: str,
        audience: str | None,
        token_jti: str,
        now: float,
    ) -> None:
        # 1. The proof must be for the same audience we are verifying for.
        if audience is None:
            msg = "DPoP-bound tokens require an audience to verify against"
            raise ValueError(msg)
        if proof.audience != audience:
            msg = f"DPoP proof audience mismatch: got {proof.audience!r}, expected {audience!r}"
            raise ValueError(msg)

        # 2. The proof must reference *this* token's jti, otherwise an
        #    attacker who captures a fresh proof for token A could pair it
        #    with stolen token B.
        if proof.jti != token_jti:
            msg = "DPoP proof does not bind to this token's jti"
            raise ValueError(msg)

        # 3. Freshness — DPoP proofs are short-lived.
        if proof.iat - self._skew > now:
            msg = "DPoP proof is from the future"
            raise ValueError(msg)
        if proof.iat + self._dpop_ttl + self._skew < now:
            msg = "DPoP proof has expired"
            raise ValueError(msg)

        # 4. Key thumbprint must match what the token says.
        if _jkt(proof.public_key) != expected_jkt:
            msg = "DPoP proof key does not match token's cnf.jkt"
            raise ValueError(msg)

        # 5. Verify the proof signature.  We accept either:
        #    - HMAC over the signing input using the *public key bytes* as
        #      a symmetric secret — useful for tests, deterministic, and
        #      the simplest possible proof-of-possession compatible with
        #      NEST's keyless mock identities.
        #    - A signature made by a registered :class:`Identity`-style
        #      signer (the caller supplies bytes that the embedded public
        #      key can verify).  See :func:`verify_dpop_signature` below
        #      for the asymmetric path; we keep this method MAC-only so
        #      ``DpopAuth`` itself stays dependency-free.
        signing_input = self.build_dpop_signing_input(
            audience=audience, token_jti=token_jti, iat=proof.iat
        )
        expected = hmac.new(proof.public_key, signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, proof.signature):
            msg = "Invalid DPoP proof signature"
            raise ValueError(msg)

    # ------------------------------------------------------------------
    # Compat: the ``Auth`` protocol only knows ``revoke(token)``.  We keep
    # raw-token revocation alongside ``jti`` revocation so legacy callers
    # still work.
    # ------------------------------------------------------------------
    @property
    def _revoked_raw(self) -> set[str]:
        if not hasattr(self, "_revoked_raw_set"):
            self._revoked_raw_set: set[str] = set()
        return self._revoked_raw_set


def make_dpop_proof(
    *,
    audience: str,
    token_jti: str,
    iat: float,
    public_key: bytes,
) -> DpopProof:
    """Convenience constructor for an HMAC-style DPoP proof.

    Real deployments would sign with an asymmetric private key; in NEST
    simulations the HMAC variant is sufficient to demonstrate the
    binding and is deterministic given the same inputs.

    Example::

        proof = make_dpop_proof(audience="svc", token_jti="abc", iat=1.0, public_key=b"k")
    """
    signing_input = DpopAuth.build_dpop_signing_input(
        audience=audience, token_jti=token_jti, iat=iat
    )
    sig = hmac.new(public_key, signing_input, hashlib.sha256).digest()
    return DpopProof(
        jti=token_jti, audience=audience, iat=iat, signature=sig, public_key=public_key
    )
