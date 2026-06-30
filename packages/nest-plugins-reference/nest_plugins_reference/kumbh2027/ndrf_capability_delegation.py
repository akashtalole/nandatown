# SPDX-License-Identifier: Apache-2.0
"""KumbhNet NDRF capability delegation auth plugin.

The stock ``jwt`` auth plugin issues flat tokens with a scopes list.
Any agent with ``zone:close`` can close any zone at any time —
no authority chain, no expiry tied to operational windows, no revocation
that cascades down a delegation tree.

This plugin implements **time-bounded, revocable capability chains**
that model the actual authority structure of Indian disaster management:

Authority hierarchy (highest → lowest)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
1. ``district_collector`` — root; can issue, revoke, delegate anything.
2. ``ndrf_commander``     — can delegate ``zone:close`` during an active incident.
3. ``zone_commander``     — can act on delegated ``zone:close``; cannot re-delegate.
4. ``iccc_operator``      — can issue entry holds (``zone:hold``) without delegation.
5. ``medical_staff``      — scoped to ``ambulance:dispatch``.

Delegation rules
~~~~~~~~~~~~~~~~
* A token may only grant scopes that the issuer's own token already holds.
* ``zone:close`` tokens are time-bounded to the bathing window
  (``window_start``…``window_end`` epoch seconds; passed at construction).
* Any principal in the chain can revoke their own subtree by calling
  ``revoke(token)``.  Revocation propagates: if an intermediate token
  is revoked, all tokens derived from it become invalid.
* Delegation depth is capped at ``MAX_DEPTH = 3`` to prevent unbounded
  chains.

Wire format (deterministic)
~~~~~~~~~~~~~~~~~~~~~~~~~~~
``Token`` is a pipe-separated string::

    base64(json payload) | HMAC-SHA256 hex

Payload fields::

    sub          AgentId of the holder
    scopes       list[str]
    iat          float (issued at — logical simulation time, not wall clock)
    exp          float (expiry — logical time)
    delegated_by AgentId of the issuing principal (None for root)
    depth        int delegation depth (0 = root)
    token_id     str UUID for revocation tracking

Determinism guarantee
~~~~~~~~~~~~~~~~~~~~~
``iat`` and ``exp`` are passed in by the caller; never read from
``time.time()``.  The simulation clock value must be supplied via
``issue(..., now=ctx.time)`` or the optional ``clock`` constructor arg
(defaults to 0.0 for tests).

Example::

    from nest_plugins_reference.kumbh2027.ndrf_capability_delegation import NDRFCapabilityDelegation
    from nest_core.types import AgentId, Token

    auth = NDRFCapabilityDelegation(window_start=0.0, window_end=57600.0)
    # Root issues a token to district collector
    root_token = await auth.issue(AgentId("dc-nashik"), ["zone:close", "zone:hold"])
    ctx = await auth.verify(root_token)
    assert "zone:close" in ctx.scopes
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

from nest_core.types import AgentId, AuthContext, Token

_SECRET = b"ndrf-kumbh-2027"
MAX_DEPTH = 3
"""Maximum delegation chain depth (root = 0, zone commander = 2)."""


def _sign(payload: str) -> str:
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def _encode_token(payload: dict[str, object]) -> Token:
    raw = json.dumps(payload, sort_keys=True)
    b64 = base64.b64encode(raw.encode()).decode()
    sig = _sign(b64)
    return Token(f"{b64}|{sig}")


def _decode_token(token: Token) -> dict[str, object]:
    """Decode and verify a token; raise ValueError if invalid."""
    raw = str(token)
    parts = raw.rsplit("|", 1)
    if len(parts) != 2:
        msg = "Invalid token format"
        raise ValueError(msg)
    b64, sig = parts
    expected = _sign(b64)
    if not hmac.compare_digest(sig, expected):
        msg = "Invalid token signature"
        raise ValueError(msg)
    return json.loads(base64.b64decode(b64.encode()).decode())


class NDRFCapabilityDelegation:
    """Time-bounded, revocable, delegatable auth for Kumbh zone authority.

    Example::

        auth = NDRFCapabilityDelegation(window_start=0.0, window_end=57600.0)
        token = await auth.issue(AgentId("ndrf-1"), ["zone:close"])
        ctx = await auth.verify(token)
        assert "zone:close" in ctx.scopes
    """

    def __init__(
        self,
        window_start: float = 0.0,
        window_end: float = 57_600.0,  # 16-hour bathing window
        clock: float = 0.0,
    ) -> None:
        self._window_start = window_start
        self._window_end = window_end
        self._clock = clock
        # token_id → payload dict; used for revocation cascade checks.
        self._issued: dict[str, dict[str, object]] = {}
        # Revoked token_ids (transitive closure computed at verify time).
        self._revoked: set[str] = set()

    def _now(self) -> float:
        return self._clock

    async def issue(
        self,
        subject: AgentId,
        scopes: list[str],
        *,
        now: float | None = None,
        exp: float | None = None,
    ) -> Token:
        """Issue a root-level token (depth=0) with no parent.

        ``zone:close`` scope tokens automatically expire at ``window_end``.

        Example::

            token = await auth.issue(AgentId("ndrf-1"), ["zone:close"])
        """
        t = now if now is not None else self._now()
        token_exp = exp if exp is not None else self._window_end
        if "zone:close" in scopes:
            token_exp = min(token_exp, self._window_end)

        payload: dict[str, object] = {
            "sub": str(subject),
            "scopes": sorted(scopes),
            "iat": t,
            "exp": token_exp,
            "delegated_by": None,
            "depth": 0,
            "token_id": str(uuid.uuid4()),
        }
        token = _encode_token(payload)
        self._issued[str(payload["token_id"])] = payload
        return token

    async def delegate(
        self,
        parent_token: Token,
        to: AgentId,
        scopes: list[str],
        *,
        now: float | None = None,
        exp: float | None = None,
    ) -> Token:
        """Delegate a subset of scopes to another agent.

        The delegated scopes must be a subset of the parent token's scopes.
        Depth is parent depth + 1; capped at MAX_DEPTH.

        Raises ``ValueError`` if:
        * the parent token is invalid or revoked,
        * ``scopes`` contains a scope not held by the parent,
        * the delegation would exceed MAX_DEPTH.

        Example::

            child = await auth.delegate(root_token, AgentId("zone-cmd-1"), ["zone:close"])
            ctx = await auth.verify(child)
            assert ctx.subject == AgentId("zone-cmd-1")
        """
        parent = await self.verify(parent_token)  # raises if invalid/revoked
        parent_payload = _decode_token(parent_token)
        parent_depth = int(parent_payload.get("depth", 0))  # type: ignore[arg-type]

        if parent_depth >= MAX_DEPTH:
            msg = f"Delegation depth {parent_depth} already at maximum {MAX_DEPTH}"
            raise ValueError(msg)

        parent_scopes = set(parent.scopes)
        extra = set(scopes) - parent_scopes
        if extra:
            msg = f"Cannot delegate scopes not held by parent: {extra}"
            raise ValueError(msg)

        t = now if now is not None else self._now()
        parent_exp = float(parent_payload.get("exp", self._window_end))  # type: ignore[arg-type]
        token_exp = min(exp if exp is not None else parent_exp, parent_exp)
        if "zone:close" in scopes:
            token_exp = min(token_exp, self._window_end)

        payload: dict[str, object] = {
            "sub": str(to),
            "scopes": sorted(scopes),
            "iat": t,
            "exp": token_exp,
            "delegated_by": str(parent_payload["token_id"]),
            "depth": parent_depth + 1,
            "token_id": str(uuid.uuid4()),
        }
        token = _encode_token(payload)
        self._issued[str(payload["token_id"])] = payload
        return token

    async def verify(self, token: Token, *, now: float | None = None) -> AuthContext:
        """Verify a token and its full delegation chain.

        Raises ``ValueError`` if the token is expired, revoked, or its
        parent chain contains a revoked ancestor.

        Example::

            ctx = await auth.verify(token)
            assert "zone:close" in ctx.scopes
        """
        t = now if now is not None else self._now()
        payload = _decode_token(token)

        token_id = str(payload["token_id"])
        if token_id in self._revoked:
            msg = "Token revoked"
            raise ValueError(msg)

        exp = float(payload.get("exp", 0))  # type: ignore[arg-type]
        if exp < t:
            msg = f"Token expired at {exp} (now={t})"
            raise ValueError(msg)

        # Walk parent chain for revocation.
        parent_id = payload.get("delegated_by")
        while parent_id is not None:
            if str(parent_id) in self._revoked:
                msg = f"Parent token {parent_id} revoked; child token invalid"
                raise ValueError(msg)
            parent_payload = self._issued.get(str(parent_id))
            if parent_payload is None:
                break
            parent_id = parent_payload.get("delegated_by")

        scopes: list[str] = list(payload.get("scopes", []))  # type: ignore[arg-type]
        iat = float(payload.get("iat", 0))  # type: ignore[arg-type]
        return AuthContext(
            subject=AgentId(str(payload["sub"])),
            scopes=scopes,
            issued_at=iat,
            expires_at=exp,
        )

    async def revoke(self, token: Token) -> None:
        """Revoke a token; all tokens delegated from it become invalid.

        Example::

            await auth.revoke(token)
        """
        payload = _decode_token(token)
        self._revoked.add(str(payload["token_id"]))
