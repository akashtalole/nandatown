# Trust layer

**What it does.** Maintain per-agent reputation, accept attestations
and abuse reports, optionally support stake.

## Interface

```python
class Trust(Protocol):
    async def score(self, agent: AgentId) -> ReputationScore: ...
    async def attest(self, agent: AgentId, claim: Claim) -> Attestation: ...
    async def report(self, agent: AgentId, evidence: Evidence) -> None: ...
    async def stake(self, agent: AgentId, amount: int) -> None: ...
```

Full definition: [`nest_core/layers/trust.py`](../../packages/nest-core/nest_core/layers/trust.py).

## Built-in plugins

| name | summary | Sybil-resistant? |
|---|---|---|
| `score_average` (default) | Running mean of feedback scores. | No |
| `eigentrust` | Transitive reputation via the EigenTrust eigenvector (Kamvar et al., WWW '03). Weighs each report by the reporter's current trust, with a teleport to a configurable pre-trusted seed set. | Yes |

Sources:
- [`nest_plugins_reference/trust/score_average.py`](../../packages/nest-plugins-reference/nest_plugins_reference/trust/score_average.py)
- [`nest_plugins_reference/trust/eigentrust.py`](../../packages/nest-plugins-reference/nest_plugins_reference/trust/eigentrust.py)

The `reputation` scenario exercises this layer — 16 honest + 4
malicious + 1 observer that samples cheat reports probabilistically.
Swap `trust: score_average` for `trust: eigentrust` in the scenario
YAML to compare adversarial behaviour under the two ranking rules.

## Writing your own

See [`writing-a-plugin.md`](../writing-a-plugin.md). Register under
entry point group `nest.plugins.trust`.

Good fits to test here: proof-of-stake reputation, decaying / time-
weighted scores, attestation graphs, learned reputation models.
