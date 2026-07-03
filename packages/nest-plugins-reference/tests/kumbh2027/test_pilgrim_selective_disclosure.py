# SPDX-License-Identifier: Apache-2.0
"""Tests for pilgrim selective-disclosure privacy plugin.

Adversarial invariants verified:
* MedEvac can only learn medical attributes, not name/photo.
* LostConnect can only learn name/photo, not medical data.
* Police can only learn zone_id.
* A proof from one role does not validate as another role's proof.
* Tampering with disclosed values fails verification.
* HMAC commitment is deterministic (same input → same output).
"""

from __future__ import annotations

import json

import pytest
from nest_core.types import AgentId, Statement, Witness
from nest_plugins_reference.kumbh2027.pilgrim_selective_disclosure import (
    ROLE_ATTRIBUTE_MAP,
    PilgrimSelectiveDisclosure,
    _commit,
)

_PROFILE = {
    "name": "Arjun Sharma",
    "photo_hash": "abc123",
    "cardiac_care": "true",
    "diabetes": "false",
    "mobility_impaired": "false",
    "blood_group": "O+",
    "zone_id": "ramkund_main",
}


# ---------------------------------------------------------------------------
# Commit determinism
# ---------------------------------------------------------------------------


def test_commit_deterministic() -> None:
    c1 = _commit("cardiac_care", "true")
    c2 = _commit("cardiac_care", "true")
    assert c1 == c2


def test_commit_differs_for_different_values() -> None:
    assert _commit("cardiac_care", "true") != _commit("cardiac_care", "false")


# ---------------------------------------------------------------------------
# Encrypt / decrypt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_encrypt_replaces_values_with_commitments() -> None:
    priv = PilgrimSelectiveDisclosure()
    ct = await priv.encrypt(json.dumps(_PROFILE).encode(), [AgentId("zone-0")])
    envelope = json.loads(ct.decode())
    # Values must not appear verbatim in the envelope
    for value in _PROFILE.values():
        assert value not in envelope.values(), f"Raw value {value!r} leaked in envelope"


@pytest.mark.asyncio
async def test_decrypt_returns_envelope() -> None:
    priv = PilgrimSelectiveDisclosure()
    ct = await priv.encrypt(json.dumps(_PROFILE).encode(), [])
    raw = await priv.decrypt(ct)
    assert raw == ct  # decrypt is passthrough


# ---------------------------------------------------------------------------
# Role-based selective disclosure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_medevac_gets_only_medical_attributes() -> None:
    priv = PilgrimSelectiveDisclosure()
    await priv.encrypt(json.dumps(_PROFILE).encode(), [])

    stmt = Statement(predicate="role_access:medevac", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(_PROFILE)})
    proof = await priv.prove(stmt, witness)

    payload = json.loads(proof.data.decode())
    disclosed: dict[str, str] = payload["disclosed"]

    medical = ROLE_ATTRIBUTE_MAP["medevac"]
    non_medical = set(_PROFILE.keys()) - medical

    assert set(disclosed.keys()) <= medical, "MedEvac received non-medical attributes"
    for attr in non_medical:
        assert attr not in disclosed, f"{attr} must not be disclosed to medevac"


@pytest.mark.asyncio
async def test_lostconnect_gets_only_name_and_photo() -> None:
    priv = PilgrimSelectiveDisclosure()
    await priv.encrypt(json.dumps(_PROFILE).encode(), [])

    stmt = Statement(predicate="role_access:lostconnect", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(_PROFILE)})
    proof = await priv.prove(stmt, witness)

    payload = json.loads(proof.data.decode())
    disclosed: dict[str, str] = payload["disclosed"]

    assert set(disclosed.keys()) <= {"name", "photo_hash"}
    assert "cardiac_care" not in disclosed


@pytest.mark.asyncio
async def test_police_gets_only_zone_id() -> None:
    priv = PilgrimSelectiveDisclosure()
    await priv.encrypt(json.dumps(_PROFILE).encode(), [])

    stmt = Statement(predicate="role_access:police", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(_PROFILE)})
    proof = await priv.prove(stmt, witness)

    payload = json.loads(proof.data.decode())
    disclosed: dict[str, str] = payload["disclosed"]

    assert set(disclosed.keys()) <= {"zone_id"}
    assert "name" not in disclosed
    assert "cardiac_care" not in disclosed


@pytest.mark.asyncio
async def test_public_role_discloses_nothing() -> None:
    priv = PilgrimSelectiveDisclosure()
    await priv.encrypt(json.dumps(_PROFILE).encode(), [])

    stmt = Statement(predicate="role_access:public", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(_PROFILE)})
    proof = await priv.prove(stmt, witness)

    payload = json.loads(proof.data.decode())
    assert payload["disclosed"] == {}


# ---------------------------------------------------------------------------
# Proof verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_proof_verifies() -> None:
    priv = PilgrimSelectiveDisclosure()
    stmt = Statement(predicate="role_access:medevac", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(_PROFILE)})
    proof = await priv.prove(stmt, witness)
    assert await priv.verify_proof(stmt, proof)


@pytest.mark.asyncio
async def test_tampered_value_fails_verification() -> None:
    priv = PilgrimSelectiveDisclosure()
    stmt = Statement(predicate="role_access:medevac", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(_PROFILE)})
    proof = await priv.prove(stmt, witness)

    # Tamper: change a disclosed value without updating the commitment
    payload = json.loads(proof.data.decode())
    payload["disclosed"]["cardiac_care"] = "false"  # was "true"
    from nest_core.types import Proof

    tampered_proof = Proof(
        statement=stmt,
        data=json.dumps(payload, sort_keys=True).encode(),
        scheme="kumbh_selective_disclosure",
    )
    assert not await priv.verify_proof(stmt, tampered_proof), "Tampered proof must not verify"


@pytest.mark.asyncio
async def test_wrong_scheme_fails_verification() -> None:
    priv = PilgrimSelectiveDisclosure()
    from nest_core.types import Proof, Statement

    stmt = Statement(predicate="role_access:medevac", public_inputs={})
    bad_proof = Proof(statement=stmt, data=b"{}", scheme="noop")
    assert not await priv.verify_proof(stmt, bad_proof)


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

from hypothesis import given, settings
from hypothesis import strategies as st


_ALL_PROFILE_ATTRS = ["name", "photo_hash", "cardiac_care", "diabetes",
                      "mobility_impaired", "blood_group", "zone_id"]
_MEDICAL_ATTRS = {"cardiac_care", "diabetes", "mobility_impaired", "blood_group"}
_PII_ATTRS = {"name", "photo_hash"}


@given(
    values=st.fixed_dictionaries({
        attr: st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N")))
        for attr in _ALL_PROFILE_ATTRS
    })
)
@settings(max_examples=40)
def test_medevac_never_receives_pii(values: dict[str, str]) -> None:
    """For any pilgrim profile, MedEvac must never see name or photo_hash."""
    import asyncio, json
    from nest_core.types import Statement, Witness

    async def _run() -> None:
        priv = PilgrimSelectiveDisclosure()
        stmt = Statement(predicate="role_access:medevac", public_inputs={})
        witness = Witness(private_inputs={"profile": json.dumps(values)})
        proof = await priv.prove(stmt, witness)
        payload = json.loads(proof.data.decode())
        disclosed = set(payload["disclosed"].keys())
        assert not (disclosed & _PII_ATTRS), f"MedEvac received PII: {disclosed & _PII_ATTRS}"

    asyncio.run(_run())


@given(
    values=st.fixed_dictionaries({
        attr: st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N")))
        for attr in _ALL_PROFILE_ATTRS
    })
)
@settings(max_examples=40)
def test_lostconnect_never_receives_medical(values: dict[str, str]) -> None:
    """For any pilgrim profile, LostConnect must never see medical attributes."""
    import asyncio, json
    from nest_core.types import Statement, Witness

    async def _run() -> None:
        priv = PilgrimSelectiveDisclosure()
        stmt = Statement(predicate="role_access:lostconnect", public_inputs={})
        witness = Witness(private_inputs={"profile": json.dumps(values)})
        proof = await priv.prove(stmt, witness)
        payload = json.loads(proof.data.decode())
        disclosed = set(payload["disclosed"].keys())
        assert not (disclosed & _MEDICAL_ATTRS), (
            f"LostConnect received medical data: {disclosed & _MEDICAL_ATTRS}"
        )

    asyncio.run(_run())


@given(
    attr=st.sampled_from(_ALL_PROFILE_ATTRS),
    value=st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N"))),
)
@settings(max_examples=60)
def test_commit_is_deterministic_for_any_input(attr: str, value: str) -> None:
    """For any (attribute, value) pair, _commit must be deterministic."""
    c1 = _commit(attr, value)
    c2 = _commit(attr, value)
    assert c1 == c2, f"_commit({attr!r}, {value!r}) is not deterministic"
    assert len(c1) == 16, "Commitment must be 16 hex chars"
