# SPDX-License-Identifier: Apache-2.0
"""Tests for ``nest_core.security_validators``.

These exercises pretend a swarm scenario emitted ``auth.*`` events into
its trace and assert that the security validators flag the right
adversarial patterns.

Run::

    uv run pytest packages/nest-core/tests/test_security_validators.py -v
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from nest_core.security_validators import (
    validate_audience_binding,
    validate_dpop_binding_when_required,
    validate_no_expired_acceptance,
    validate_no_token_replay,
    validate_security_events,
    validate_security_trace,
    validate_subject_matches_sender,
)
from nest_core.validators import ValidationResult


def _ok(name: str, results: Sequence[ValidationResult]) -> bool:
    for r in results:
        if r.name == name:
            return r.passed
    raise AssertionError(f"validator {name!r} not found in results")


# ---------------------------------------------------------------------------
# 1. Replay
# ---------------------------------------------------------------------------


class TestReplayValidator:
    def test_clean_trace_passes(self) -> None:
        events = [
            {
                "t": 1.0,
                "kind": "auth.verify_success",
                "agent": "verifier-a",
                "token_jti": "jti-1",
            },
            {
                "t": 2.0,
                "kind": "auth.verify_success",
                "agent": "verifier-a",
                "token_jti": "jti-2",
            },
        ]
        assert _ok("no_token_replay", validate_no_token_replay(events))

    def test_replay_is_caught(self) -> None:
        events = [
            {
                "t": 1.0,
                "kind": "auth.verify_success",
                "agent": "verifier-a",
                "token_jti": "jti-1",
            },
            {
                "t": 5.0,
                "kind": "auth.verify_success",
                "agent": "verifier-a",
                "token_jti": "jti-1",
            },
        ]
        results = validate_no_token_replay(events)
        assert not _ok("no_token_replay", results)
        assert "jti-1" in results[0].detail

    def test_same_jti_at_different_verifiers_is_not_flagged(self) -> None:
        # The validator scopes by verifier — two different verifiers
        # each accepting once is allowed.  (A stricter policy is up to
        # the operator; this matches a sensible default.)
        events = [
            {
                "t": 1.0,
                "kind": "auth.verify_success",
                "agent": "verifier-a",
                "token_jti": "jti-1",
            },
            {
                "t": 2.0,
                "kind": "auth.verify_success",
                "agent": "verifier-b",
                "token_jti": "jti-1",
            },
        ]
        assert _ok("no_token_replay", validate_no_token_replay(events))


# ---------------------------------------------------------------------------
# 2. Audience confusion
# ---------------------------------------------------------------------------


class TestAudienceBindingValidator:
    def test_matching_audience_passes(self) -> None:
        events = [
            {
                "kind": "auth.verify_success",
                "agent": "v",
                "presented_aud": "payments",
                "aud": "payments",
                "token_jti": "j",
            }
        ]
        assert _ok("audience_binding", validate_audience_binding(events))

    def test_audience_confusion_flagged(self) -> None:
        events = [
            {
                "kind": "auth.verify_success",
                "agent": "registry-svc",
                "presented_aud": "registry",
                "aud": "payments",
                "token_jti": "j",
            }
        ]
        results = validate_audience_binding(events)
        assert not _ok("audience_binding", results)
        assert "payments" in results[0].detail
        assert "registry" in results[0].detail


# ---------------------------------------------------------------------------
# 3. Subject impersonation
# ---------------------------------------------------------------------------


class TestSubjectMatchesSenderValidator:
    def test_matching_subject_passes(self) -> None:
        events = [
            {
                "kind": "auth.verify_success",
                "agent": "v",
                "from": "a1",
                "sub": "a1",
                "token_jti": "j",
            }
        ]
        assert _ok("subject_matches_sender", validate_subject_matches_sender(events))

    def test_impersonation_flagged(self) -> None:
        events = [
            {
                "kind": "auth.verify_success",
                "agent": "v",
                "from": "a9",
                "sub": "a1",  # token is for a1 but a9 is presenting it
                "token_jti": "j",
            }
        ]
        results = validate_subject_matches_sender(events)
        assert not _ok("subject_matches_sender", results)


# ---------------------------------------------------------------------------
# 4. Expired-token acceptance
# ---------------------------------------------------------------------------


class TestExpiredAcceptanceValidator:
    def test_fresh_accept_passes(self) -> None:
        events = [
            {
                "t": 10.0,
                "kind": "auth.verify_success",
                "agent": "v",
                "token_jti": "j",
                "exp": 20.0,
            }
        ]
        assert _ok("no_expired_acceptance", validate_no_expired_acceptance(events))

    def test_expired_accept_flagged(self) -> None:
        events = [
            {
                "t": 100.0,
                "kind": "auth.verify_success",
                "agent": "v",
                "token_jti": "j",
                "exp": 50.0,
            }
        ]
        results = validate_no_expired_acceptance(events)
        assert not _ok("no_expired_acceptance", results)


# ---------------------------------------------------------------------------
# 5. DPoP binding requirement
# ---------------------------------------------------------------------------


class TestDpopBindingRequiredValidator:
    def test_bound_token_for_required_aud_passes(self) -> None:
        events = [
            {
                "kind": "auth.issue",
                "agent": "issuer",
                "to": "a1",
                "aud": "payments",
                "token_jti": "j1",
                "bound": True,
            }
        ]
        results = validate_dpop_binding_when_required(events, required_audiences={"payments"})
        assert _ok("dpop_binding_when_required", results)

    def test_unbound_token_for_required_aud_flagged(self) -> None:
        events = [
            {
                "kind": "auth.issue",
                "agent": "issuer",
                "to": "a1",
                "aud": "payments",
                "token_jti": "j1",
                "bound": False,
            }
        ]
        results = validate_dpop_binding_when_required(events, required_audiences={"payments"})
        assert not _ok("dpop_binding_when_required", results)

    def test_unbound_outside_required_aud_is_fine(self) -> None:
        events = [
            {
                "kind": "auth.issue",
                "agent": "issuer",
                "to": "a1",
                "aud": "health",
                "token_jti": "j1",
                "bound": False,
            }
        ]
        results = validate_dpop_binding_when_required(events, required_audiences={"payments"})
        assert _ok("dpop_binding_when_required", results)


# ---------------------------------------------------------------------------
# 6. Aggregate + trace loader
# ---------------------------------------------------------------------------


class TestAggregateAndTraceLoader:
    def test_aggregate_runs_all_validators(self) -> None:
        events = [
            {
                "t": 1.0,
                "kind": "auth.verify_success",
                "agent": "v",
                "token_jti": "j1",
                "presented_aud": "svc",
                "aud": "svc",
                "from": "a1",
                "sub": "a1",
                "exp": 100.0,
            }
        ]
        results = validate_security_events(events)
        # All five validators emit one result.
        assert {r.name for r in results} == {
            "no_token_replay",
            "audience_binding",
            "subject_matches_sender",
            "no_expired_acceptance",
            "dpop_binding_when_required",
        }
        assert all(r.passed for r in results)

    def test_trace_loader_reads_jsonl(self, tmp_path: Path) -> None:
        events = [
            {
                "t": 1.0,
                "kind": "auth.verify_success",
                "agent": "v",
                "token_jti": "j1",
            },
            {
                "t": 2.0,
                "kind": "auth.verify_success",
                "agent": "v",
                "token_jti": "j1",
            },
        ]
        path = tmp_path / "trace.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in events))
        results = validate_security_trace(path)
        replay = next(r for r in results if r.name == "no_token_replay")
        assert not replay.passed

    def test_ignores_non_auth_events(self) -> None:
        events = [
            {"kind": "send", "msg": "buy:apple:5"},
            {"kind": "recv", "msg": "sold:apple:5"},
        ]
        # No auth events => everything passes vacuously.
        results = validate_security_events(events)
        assert all(r.passed for r in results)
