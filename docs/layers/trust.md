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

## Default plugin

`score_average` — running mean of feedback scores. No Sybil resistance,
no decay, no stake economics.

Source: [`nest_plugins_reference/trust/score_average.py`](../../packages/nest-plugins-reference/nest_plugins_reference/trust/score_average.py).

The `reputation` scenario exercises this layer — 16 honest + 4
malicious + 1 observer that samples cheat reports probabilistically.

## Bundled alternative: `eigentrust`

`eigentrust` — transitive, Sybil-resistant reputation as the stationary
distribution of a teleporting random walk over the local-trust graph
(Kamvar, Schlosser, Garcia-Molina, WWW 2003). The fixed point is

```
t = (1 - alpha) * C^T * t + alpha * p
```

where `C` is the row-normalized local-trust matrix and `p` is a
distribution over pre-trusted peers. Properties the test suite checks:
simplex (`sum_i t_i == 1`), row-stochasticity of `C`, fixed-point
residual `< tol`, and a Sybil upper bound `t_sybil <= alpha * p_sybil`
when no honest agent endorses the Sybil.

Flip a scenario to use it:

```yaml
layers:
  trust: eigentrust
```

Source: [`nest_plugins_reference/trust/eigentrust.py`](../../packages/nest-plugins-reference/nest_plugins_reference/trust/eigentrust.py).
Tests: [`tests/test_eigentrust.py`](../../packages/nest-plugins-reference/tests/test_eigentrust.py).

## Writing your own

See [`writing-a-plugin.md`](../writing-a-plugin.md). Register under
entry point group `nest.plugins.trust`.

Good fits to test here: EigenTrust-style transitive reputation,
proof-of-stake reputation, decaying scores, attestation graphs.
