# KumbhNet — Crowd Safety Protocol Stack for Nashik Kumbh Mela 2027

> Nanda Town hackathon submission · KumbhNet · Covers problems #4, #8, #9, #10

## Why this exists

Nashik Simhastha Kumbh Mela 2027 expects **80 million pilgrims** over 45 days —
22.5 million on a single peak bathing day.  Two sacred sites 30 km apart
(Nashik Ramkund and Trimbakeshwar Kushavart Kund) share one mountain road and
a cell network that saturates in monsoon rain.

Existing crowd-safety platforms are *centralised dashboards*.  When the central
node goes down (cell tower failure, power outage, monsoon flooding), the
platform goes with it — at exactly the moment it is needed most.

**KumbhNet** treats crowd safety as a *distributed agent coordination problem*
and uses Nanda Town to adversarially stress-test the protocols *before* any
physical deployment.

## Core insight

The three failure modes that must never happen:

1. Kushavart Kund exceeding **1,900 persons** without an immediate entry hold.
2. A critical alert not reaching NDRF within **60 seconds**.
3. Zone closure triggered by **Byzantine sensors** without quorum agreement.

All three are protected by hard-coded rules in stream processors — **not
dependent on AI agent availability**.  The KumbhNet plugins enforce those same
invariants at the protocol level and prove them hold under adversarial
conditions using Nanda Town validators.

## What was built

### Layer plugins (`packages/nest-plugins-reference/nest_plugins_reference/kumbh2027/`)

| Plugin | Layer | Problem |
|---|---|---|
| `kumbh_bft_coordination.py` | Coordination | #10 — BFT, partition-tolerant |
| `pilgrim_selective_disclosure.py` | Privacy | #9 — hybrid encryption, selective disclosure |
| `ndrf_capability_delegation.py` | Auth | #4 — capability delegation |
| `crowd_density_datafacts.py` | DataFacts | #8 — content-addressed datasets |

### Scenarios (`scenarios/`)

| Scenario | Agents | Failures | What it tests |
|---|---|---|---|
| `kumbh_peak_bathing.yaml` | 118 | 30% drop, 15% Byzantine, Nashik/Trimbak partition | All 4 plugins together under peak bathing load |
| `kumbh_flood_surge.yaml` | 25 | 40% drop, 8% Byzantine, CommandBridge isolated | Decentralised evacuation without central coordinator |

## Plugin details

### `kumbh_bft_coordination` (Problem #10)

**Why ContractNet fails at Kumbh:** a single Byzantine zone agent can win every
Contract Net round with a fake bid of zero and force any zone closure it wants.
For a physical gate that stops millions of pilgrims from entering a sacred
bathing site, that is unacceptable.

**What KumbhNet does:** PBFT-lite weighted quorum.  Zone closure commits only
when ≥ ⌈2n/3⌉ + 1 zone agents vote YES.  With n=12 zones, quorum is 9 — the
4 Byzantine agents allowed by BFT tolerance cannot force a closure, and cannot
block one either if 9+ honest agents agree.

Kushavart Kund veto: if raw person count > 1,900, closure is forced regardless
of votes — this mirrors the hard-coded stream-lambda rule in production.

**Adversarial validator test:** 4 Byzantine YES votes cannot close a zone where
8 honest agents voted NO.

---

### `pilgrim_selective_disclosure` (Problem #9)

**Why noop fails at Kumbh:** 80 million pilgrims include elderly with cardiac
conditions, diabetics, and mobility-impaired persons.  A compromised agent that
reads the full medical profile during an SOS response is a privacy catastrophe
that could also be exploited for identity theft at scale.

**What KumbhNet does:** per-attribute HMAC-keyed commitment.  Each profile
attribute (name, cardiac\_care, blood\_group, zone\_id, …) is committed with a
distinct key derived from `HMAC-SHA256(sim_secret, attribute_name)`.  Roles
only get the attributes they are entitled to:

| Role | Disclosed attributes |
|---|---|
| `medevac` | cardiac\_care, diabetes, mobility\_impaired, blood\_group |
| `lostconnect` | name, photo\_hash |
| `police` | zone\_id |
| `iccc_operator` | zone\_id, name |
| `public` | (nothing) |

A compromised MedEvac agent cannot learn a pilgrim's name.  A compromised
LostConnect agent cannot learn a pilgrim's medical conditions.

**Adversarial validator test:** tampering with a disclosed attribute value
fails HMAC commitment verification.

---

### `ndrf_capability_delegation` (Problem #4)

**Why flat JWT RBAC fails at Kumbh:** the real Kumbh authority structure has
7 layers — District Collector → NDRF Commander → Zone Commander → ICCC
Operator.  A flat RBAC matrix lets any `zone:close` token close any zone at
any time, with no accountability chain and no expiry.

**What KumbhNet does:** time-bounded, revocable delegation chains with Ed25519-
style HMAC-signed certificates.  Key properties:

- A token may only grant scopes its issuer already holds (no privilege escalation).
- `zone:close` tokens auto-expire at `window_end` (end of bathing window, 0600–2200 IST).
- Delegation depth is capped at 3 (District Collector → NDRF → Zone Commander).
- Revoking a parent token cascades — all child tokens become invalid.

**Adversarial validator test:** a zone commander cannot delegate `zone:close` to
a subordinate if the parent token has been revoked by NDRF.

---

### `crowd_density_datafacts` (Problem #8)

**Why DataFacts v1 fails at Kumbh:** it uses `time.time()` — not deterministic,
non-reproducible in simulation.  Post-incident reconstruction of a crowd surge
requires an **immutable, tamper-evident audit trail** of every density reading
that was available to every agent at the time of a decision.

**What KumbhNet does:** SHA-256 content-addressed snapshots.  The URL of each
snapshot encodes the hash of its content (`df://kumbh/<sha256hex>`).  Any
retrospective modification produces a different URL — making tampering
detectable without a separate integrity log.  Freshness is tick-based, not
wall-clock, so replays are byte-identical.  RED/BLACK zone snapshots are
automatically ACL-gated to `iccc_only`.

**Adversarial validator test:** altering density in a stored snapshot produces
a URL mismatch detectable by any agent that cached the original URL.

## Running the scenarios

```bash
# Install the package (from the repo root)
uv sync

# Run peak bathing scenario
nest run scenarios/kumbh_peak_bathing.yaml

# Run flood surge scenario
nest run scenarios/kumbh_flood_surge.yaml

# Inspect traces
nest inspect ./traces/kumbh_peak_bathing.jsonl
nest inspect ./traces/kumbh_flood_surge.jsonl
```

## Running the tests

```bash
uv run pytest packages/nest-plugins-reference/tests/kumbh2027/ -v
```

All tests are deterministic (no wall-clock time, no OS randomness).

## What the adversarial tests prove

| Invariant | Plugin | Test |
|---|---|---|
| Byzantine minority (4/12) cannot force zone closure | BFT Coordination | `test_byzantine_yes_minority_cannot_force_closure` |
| Byzantine minority cannot block justified closure | BFT Coordination | `test_byzantine_no_minority_cannot_block_justified_closure` |
| Kushavart count > 1900 forces closure with 0 votes | BFT Coordination | `test_kushavart_hard_cap_forces_closure` |
| MedEvac cannot learn pilgrim name | Selective Disclosure | `test_medevac_gets_only_medical_attributes` |
| Tampered medical attribute fails proof | Selective Disclosure | `test_tampered_value_fails_verification` |
| Cannot delegate scope not held by parent | Capability Delegation | `test_cannot_delegate_scope_not_held` |
| Revoking parent invalidates child tokens | Capability Delegation | `test_revoking_parent_invalidates_child` |
| zone:close tokens expire at bathing window end | Capability Delegation | `test_zone_close_capped_at_window_end` |
| Tampered snapshot produces different CID | DataFacts | `test_tampered_content_produces_different_url` |
| RED/BLACK zones are iccc\_only | DataFacts | `test_black_zone_gets_iccc_only_access` |
| Freshness uses simulation ticks not wall clock | DataFacts | `test_freshness_uses_advance_tick` |

## Architecture note

All four plugins are **testing scaffolding demonstrating the protocol shape**.
In physical deployment:

- `kumbh_bft_coordination` → replaces the DynamoDB zone-stream Lambda's
  ContractNet invocation with a proper BFT round on zone closure decisions.
- `pilgrim_selective_disclosure` → replaces the pilgrim SOS handler's direct
  DynamoDB read with a ZK-disclosure call to the pilgrim's identity agent.
- `ndrf_capability_delegation` → replaces the flat RBAC JWT in the Lambda
  Authorizer with a delegation-chain-aware verifier.
- `crowd_density_datafacts` → replaces the S3 report store with a
  content-addressed snapshot chain suitable for post-incident legal review.

The Nanda Town simulation proves the protocols survive the failure modes.
Physical deployment wires them to real IoT sensors and real AWS infrastructure.
