# SPDX-License-Identifier: Apache-2.0
"""Security-focused property validators for NEST traces.

Most :mod:`nest_core.validators` checks ask "did the protocol behave?"
This module asks "did something *malicious* happen and did the protocol
catch it?"  The questions overlap but the framing is different: a
scenario can pass functional validators while quietly leaking auth
tokens or accepting replays.

The validators are deliberately generic over auth-related trace events.
Any plugin that records auth activity into the trace stream (with the
shape documented below) becomes inspectable for the classic attacks:

* **Replay.**  Same ``jti`` accepted by the same verifier more than
  once, or by more verifiers than the policy allows.
* **Audience confusion.**  A token presented to an audience different
  from the one in its ``aud`` claim.
* **Subject impersonation.**  A token presented over a transport hop
  where the apparent sender does not match the token's ``sub`` claim.
* **Unbound bearer tokens.**  Tokens issued without ``cnf`` (DPoP binding)
  in scenarios that declare high-security requirements.
* **Expired token acceptance.**  An ``auth.verify_success`` event whose
  ``exp`` is older than the event's ``t``.

Trace event shape consumed by this module
-----------------------------------------

Each event is a dict.  Auth-related events use ``kind`` values prefixed
with ``auth.``::

    {"t": 12.0, "kind": "auth.issue",
     "agent": "issuer", "to": "a1",
     "token_jti": "abc", "aud": "payments", "exp": 13.0,
     "sub": "a1", "bound": True}

    {"t": 12.5, "kind": "auth.verify_attempt",
     "agent": "payments-svc", "from": "a1",
     "token_jti": "abc", "presented_aud": "payments",
     "sender_claimed": "a1"}

    {"t": 12.6, "kind": "auth.verify_success",
     "agent": "payments-svc", "from": "a1",
     "token_jti": "abc", "aud": "payments",
     "sub": "a1", "exp": 13.0, "bound": True}

    {"t": 12.7, "kind": "auth.verify_failure",
     "agent": "payments-svc", "reason": "replay"}

Fields that are absent are simply ignored — every validator degrades
gracefully on partial data.  This means traces from scenarios that do
*not* yet emit auth events get zero false positives.

Example::

    results = validate_security_events(events)
    for r in results:
        if not r.passed:
            print("ALERT:", r.name, r.detail)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from nest_core.validators import ValidationResult


def _load_events(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL trace into a list of event dicts.

    Mirrors :func:`nest_core.validators._load_events` but kept local
    so this module has no private-API dependency.
    """
    events: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            stripped = line.strip()
            if stripped:
                events.append(json.loads(stripped))
    return events


def _auth_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only the events that look auth-related."""
    out: list[dict[str, Any]] = []
    for ev in events:
        kind = str(ev.get("kind", ""))
        if kind.startswith("auth."):
            out.append(ev)
    return out


# ---------------------------------------------------------------------------
# 1. Replay
# ---------------------------------------------------------------------------


def validate_no_token_replay(
    events: list[dict[str, Any]],
) -> list[ValidationResult]:
    """No ``jti`` is verified successfully twice by the same verifier.

    Replay is the single most common bearer-token failure mode in agent
    swarms — an adversary captures a token from the wire (or a curious
    intermediate plays back something they saw earlier) and the verifier
    accepts it again.

    Example::

        results = validate_no_token_replay(events)
    """
    seen: dict[tuple[str, str], int] = defaultdict(int)
    replays: list[str] = []
    for ev in _auth_events(events):
        if ev.get("kind") != "auth.verify_success":
            continue
        verifier = str(ev.get("agent", ""))
        jti = ev.get("token_jti")
        if not isinstance(jti, str) or not jti:
            continue
        key = (verifier, jti)
        seen[key] += 1
        if seen[key] > 1:
            replays.append(f"{verifier} re-accepted jti={jti}")
    if replays:
        return [ValidationResult("no_token_replay", False, "; ".join(replays[:8]))]
    return [
        ValidationResult(
            "no_token_replay",
            True,
            f"checked {sum(seen.values())} verify-success events",
        )
    ]


# ---------------------------------------------------------------------------
# 2. Audience confusion
# ---------------------------------------------------------------------------


def validate_audience_binding(
    events: list[dict[str, Any]],
) -> list[ValidationResult]:
    """Verifiers only accept tokens whose ``aud`` matches their own audience.

    Maps each verifier to the audiences it actually issued tokens for
    (via ``auth.issue`` events with that verifier as ``to``) — but more
    directly, looks for ``auth.verify_success`` events where the token's
    ``aud`` does not match the audience the verifier was asked for.

    Example::

        results = validate_audience_binding(events)
    """
    violations: list[str] = []
    for ev in _auth_events(events):
        if ev.get("kind") != "auth.verify_success":
            continue
        presented = ev.get("presented_aud")
        token_aud = ev.get("aud")
        if presented is None or token_aud is None:
            continue
        if presented != token_aud:
            violations.append(
                f"{ev.get('agent', '?')} accepted token aud={token_aud!r} "
                f"as if for aud={presented!r}"
            )
    if violations:
        return [ValidationResult("audience_binding", False, "; ".join(violations[:8]))]
    return [
        ValidationResult(
            "audience_binding",
            True,
            "no audience mismatches observed",
        )
    ]


# ---------------------------------------------------------------------------
# 3. Subject impersonation
# ---------------------------------------------------------------------------


def validate_subject_matches_sender(
    events: list[dict[str, Any]],
) -> list[ValidationResult]:
    """A verifier accepting a token must see ``sub`` == claimed sender.

    If ``a1`` issues a token for itself but ``a9`` presents it over a
    transport hop, the verifier should refuse.  Concretely: every
    ``auth.verify_success`` should carry ``sub`` equal to the
    ``sender_claimed`` / ``from`` field of the event.

    Example::

        results = validate_subject_matches_sender(events)
    """
    violations: list[str] = []
    for ev in _auth_events(events):
        if ev.get("kind") != "auth.verify_success":
            continue
        sub = ev.get("sub")
        claimed = ev.get("sender_claimed") or ev.get("from")
        if sub is None or claimed is None:
            continue
        if sub != claimed:
            violations.append(
                f"{ev.get('agent', '?')} accepted sub={sub!r} from claimed sender {claimed!r}"
            )
    if violations:
        return [ValidationResult("subject_matches_sender", False, "; ".join(violations[:8]))]
    return [
        ValidationResult(
            "subject_matches_sender",
            True,
            "no impersonation patterns observed",
        )
    ]


# ---------------------------------------------------------------------------
# 4. Expired token acceptance
# ---------------------------------------------------------------------------


def validate_no_expired_acceptance(
    events: list[dict[str, Any]],
) -> list[ValidationResult]:
    """No ``auth.verify_success`` for a token whose ``exp`` was already past.

    Catches verifiers with broken clocks or generous skew that accept
    obviously-expired tokens.

    Example::

        results = validate_no_expired_acceptance(events)
    """
    violations: list[str] = []
    for ev in _auth_events(events):
        if ev.get("kind") != "auth.verify_success":
            continue
        exp = ev.get("exp")
        t = ev.get("t")
        if not isinstance(exp, (int, float)) or not isinstance(t, (int, float)):
            continue
        if exp < t:
            violations.append(
                f"{ev.get('agent', '?')} accepted token jti={ev.get('token_jti', '?')} "
                f"at t={t} with exp={exp}"
            )
    if violations:
        return [ValidationResult("no_expired_acceptance", False, "; ".join(violations[:8]))]
    return [
        ValidationResult(
            "no_expired_acceptance",
            True,
            "no expired-token acceptances observed",
        )
    ]


# ---------------------------------------------------------------------------
# 5. Bearer-token leakage (defence in depth signal)
# ---------------------------------------------------------------------------


def validate_dpop_binding_when_required(
    events: list[dict[str, Any]],
    *,
    required_audiences: set[str] | None = None,
) -> list[ValidationResult]:
    """For named audiences, every issued token must be DPoP-bound.

    Bearer tokens that float around an audience marked as high-security
    are a finding: any observer with read access to the trace could
    replay them on a less-careful verifier.

    Example::

        results = validate_dpop_binding_when_required(events, required_audiences={"payments"})
    """
    if not required_audiences:
        return [
            ValidationResult(
                "dpop_binding_when_required",
                True,
                "no required-binding audiences declared",
            )
        ]
    violations: list[str] = []
    for ev in _auth_events(events):
        if ev.get("kind") != "auth.issue":
            continue
        aud = ev.get("aud")
        if aud not in required_audiences:
            continue
        if not ev.get("bound", False):
            violations.append(
                f"issued unbound bearer token jti={ev.get('token_jti', '?')} for aud={aud!r}"
            )
    if violations:
        return [ValidationResult("dpop_binding_when_required", False, "; ".join(violations[:8]))]
    return [
        ValidationResult(
            "dpop_binding_when_required",
            True,
            f"all tokens for {sorted(required_audiences)} are DPoP-bound",
        )
    ]


# ---------------------------------------------------------------------------
# 6. Aggregate entry point
# ---------------------------------------------------------------------------


def validate_security_events(
    events: list[dict[str, Any]],
    *,
    required_dpop_audiences: set[str] | None = None,
) -> list[ValidationResult]:
    """Run all security validators against a list of events.

    Example::

        results = validate_security_events(events, required_dpop_audiences={"payments"})
    """
    results: list[ValidationResult] = []
    results.extend(validate_no_token_replay(events))
    results.extend(validate_audience_binding(events))
    results.extend(validate_subject_matches_sender(events))
    results.extend(validate_no_expired_acceptance(events))
    results.extend(
        validate_dpop_binding_when_required(events, required_audiences=required_dpop_audiences)
    )
    return results


def validate_security_trace(
    trace_path: Path,
    *,
    required_dpop_audiences: set[str] | None = None,
) -> list[ValidationResult]:
    """Convenience: load a JSONL trace and run all security validators.

    Example::

        results = validate_security_trace(Path("trace.jsonl"))
    """
    events = _load_events(trace_path)
    return validate_security_events(events, required_dpop_audiences=required_dpop_audiences)
