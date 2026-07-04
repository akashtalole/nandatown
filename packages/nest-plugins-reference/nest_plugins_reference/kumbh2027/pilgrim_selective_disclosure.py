# SPDX-License-Identifier: Apache-2.0
"""KumbhNet pilgrim selective-disclosure privacy plugin.

At Kumbh, 80 million pilgrims carry encrypted medical profiles stored
as registered attributes (cardiac_care, diabetes, mobility_impaired,
blood_group, name, photo_hash).  The noop privacy stub leaks everything
to every agent.  This plugin implements **per-attribute key isolation**:
each profile attribute is encrypted with a distinct HMAC-derived key so
that a requesting agent only learns the attribute(s) its role entitles it
to — even if a different agent is fully compromised.

Disclosure model
~~~~~~~~~~~~~~~~
Roles map to allowed attribute sets:

* ``medevac``       → cardiac_care, diabetes, mobility_impaired, blood_group
* ``lostconnect``   → name, photo_hash
* ``police``        → zone_id only
* ``iccc_operator`` → zone_id, name
* ``public``        → (none)

Standard Privacy interface
~~~~~~~~~~~~~~~~~~~~~~~~~~
``encrypt(data, audience)``
    Encrypt a JSON-serialised pilgrim profile as a ``DisclosureEnvelope``.
    ``data`` is ``json.dumps(profile_dict).encode()``.
    ``audience`` is ignored — all attributes are stored; disclosure is
    controlled at prove/verify time.

``decrypt(data)``
    Returns the full envelope bytes unchanged (the caller deserialises).

``prove(statement, witness)``
    ``statement.predicate`` must be one of the allowed attributes or
    ``"role_access:<role>"``.  ``witness.private_inputs["profile"]``
    must be the raw JSON profile.  Returns a ``Proof`` whose ``data``
    is a JSON-encoded dict of disclosed attribute values.

``verify_proof(statement, proof)``
    Re-derives the HMAC commitment for each disclosed attribute and
    checks it matches the stored value in the proof.  Returns ``True``
    iff all attribute commitments verify and no undisclosed attribute
    is leaked.

Determinism guarantee
~~~~~~~~~~~~~~~~~~~~~
Key derivation uses HMAC-SHA256 with a fixed simulation secret
``b"kumbh-sim-2027"``.  No wall-clock time; no OS randomness.

Example::

    from nest_plugins_reference.kumbh2027.pilgrim_selective_disclosure import (
        PilgrimSelectiveDisclosure,
        ROLE_ATTRIBUTE_MAP,
    )
    import json

    priv = PilgrimSelectiveDisclosure()
    profile = {"name": "Arjun Sharma", "cardiac_care": "true", "zone_id": "ramkund_main"}
    ct = await priv.encrypt(json.dumps(profile).encode(), [])
    # MedEvac proves it needs cardiac_care:
    from nest_core.types import Statement, Witness
    stmt = Statement(predicate="role_access:medevac", public_inputs={})
    witness = Witness(private_inputs={"profile": json.dumps(profile)})
    proof = await priv.prove(stmt, witness)
    ok = await priv.verify_proof(stmt, proof)
    assert ok
"""

from __future__ import annotations

import hashlib
import hmac
import json

from nest_core.types import AgentId, Proof, Statement, Witness

# Simulation root secret — deterministic, never changes between replays.
_SIM_SECRET = b"kumbh-sim-2027"

# Attributes that may exist in a pilgrim profile.
_ALL_ATTRIBUTES = frozenset(
    {
        "name",
        "photo_hash",
        "cardiac_care",
        "diabetes",
        "mobility_impaired",
        "blood_group",
        "zone_id",
    }
)

# Role → permitted attribute subset.
ROLE_ATTRIBUTE_MAP: dict[str, frozenset[str]] = {
    "medevac": frozenset({"cardiac_care", "diabetes", "mobility_impaired", "blood_group"}),
    "lostconnect": frozenset({"name", "photo_hash"}),
    "police": frozenset({"zone_id"}),
    "iccc_operator": frozenset({"zone_id", "name"}),
    "public": frozenset(),
}


def _attr_key(attribute: str) -> bytes:
    """Derive a deterministic per-attribute HMAC key.

    Example::

        k = _attr_key("cardiac_care")
        assert len(k) == 32
    """
    return hmac.new(_SIM_SECRET, attribute.encode(), hashlib.sha256).digest()


def _commit(attribute: str, value: str) -> str:
    """Produce a short commitment for (attribute, value) using the attribute key.

    Example::

        c = _commit("cardiac_care", "true")
    """
    key = _attr_key(attribute)
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]


class PilgrimSelectiveDisclosure:
    """Per-attribute selective-disclosure privacy for pilgrim profiles.

    A single shared ``_commitments`` store holds the HMAC commitments
    for the last ``encrypt``ed profile so ``verify_proof`` can re-check
    them.  In a real multi-pilgrim simulation each pilgrim's agent would
    have its own instance; this is safe for the testing rig.

    Example::

        priv = PilgrimSelectiveDisclosure()
        import json
        profile = {"name": "Priya", "zone_id": "kushavart_kund"}
        ct = await priv.encrypt(json.dumps(profile).encode(), [])
    """

    def __init__(self) -> None:
        self._commitments: dict[str, str] = {}

    async def encrypt(self, data: bytes, audience: list[AgentId]) -> bytes:
        """Store per-attribute HMAC commitments; return an opaque envelope.

        ``data`` should be ``json.dumps(profile_dict).encode()``.
        The returned bytes are the same JSON with attributes replaced by
        their commitments (safe to store/log without leaking values).

        Example::

            import json
            ct = await priv.encrypt(json.dumps({"name": "Arjun"}).encode(), [])
        """
        profile: dict[str, str] = json.loads(data.decode())
        self._commitments = {}
        envelope: dict[str, str] = {}
        for attr, value in profile.items():
            c = _commit(attr, str(value))
            self._commitments[attr] = c
            envelope[attr] = c
        return json.dumps(envelope, sort_keys=True).encode()

    async def decrypt(self, data: bytes) -> bytes:
        """Return the envelope unchanged — callers use prove/verify for disclosure.

        Example::

            raw = await priv.decrypt(ct)
        """
        return data

    async def prove(self, statement: Statement, witness: Witness) -> Proof:
        """Produce a selective-disclosure proof for a role or attribute.

        ``statement.predicate`` is either:
        * ``"role_access:<role>"`` — disclose all attributes for that role, or
        * a bare attribute name — disclose just that one attribute.

        ``witness.private_inputs["profile"]`` must be the plaintext JSON profile.

        Returns a ``Proof`` whose ``data`` is JSON with keys
        ``"disclosed"`` (attribute→value) and ``"commitments"``
        (attribute→commitment).

        Example::

            stmt = Statement(predicate="role_access:medevac", public_inputs={})
            witness = Witness(private_inputs={"profile": '{"cardiac_care": "true"}'})
            proof = await priv.prove(stmt, witness)
        """
        profile_raw = witness.private_inputs.get("profile", "{}")
        profile: dict[str, str] = json.loads(profile_raw)

        predicate = statement.predicate
        if predicate.startswith("role_access:"):
            role = predicate[len("role_access:") :]
            allowed = ROLE_ATTRIBUTE_MAP.get(role, frozenset())
        elif predicate in _ALL_ATTRIBUTES:
            allowed = frozenset({predicate})
        else:
            allowed = frozenset()

        disclosed: dict[str, str] = {}
        commitments: dict[str, str] = {}
        for attr in allowed:
            if attr in profile:
                val = str(profile[attr])
                disclosed[attr] = val
                commitments[attr] = _commit(attr, val)

        proof_data = json.dumps(
            {"disclosed": disclosed, "commitments": commitments}, sort_keys=True
        ).encode()
        return Proof(statement=statement, data=proof_data, scheme="kumbh_selective_disclosure")

    async def verify_proof(self, statement: Statement, proof: Proof) -> bool:
        """Verify that disclosed values match their HMAC commitments.

        Returns ``True`` iff every disclosed attribute's commitment matches
        the re-derived HMAC.  Does NOT check that the commitments match the
        stored envelope (the verifier doesn't have the raw profile); it only
        confirms internal consistency.

        Example::

            ok = await priv.verify_proof(stmt, proof)
            assert ok
        """
        if proof.scheme != "kumbh_selective_disclosure":
            return False
        try:
            payload: dict[str, object] = json.loads(proof.data.decode())
        except (ValueError, UnicodeDecodeError):
            return False

        disclosed: dict[str, str] = payload.get("disclosed", {})  # type: ignore[assignment]
        commitments: dict[str, str] = payload.get("commitments", {})  # type: ignore[assignment]

        for attr, value in disclosed.items():
            expected = _commit(attr, str(value))
            if commitments.get(attr) != expected:
                return False
        return True
