# `netem` transport: realistic latency in NEST

The bundled `in_memory` transport delivers messages at `time = now` so every
trace shows zero latency. That is fine for correctness work — and useless
for SLO / tail-latency / capacity work. `netem` is a sim-aware drop-in that
injects per-message latency, jitter, bandwidth-bound serialization delay,
reordering, and per-link drops, all driven by the seeded simulator RNG.

## Use it

In any scenario YAML:

```yaml
layers:
  transport: netem

transport:
  latency:
    kind: lognormal    # constant | uniform | lognormal
    p50_ms: 20
    p99_ms: 200        # heavy-tail by construction
  jitter_ms: 2
  bandwidth_kbps: 1000 # optional — adds size-based serialization delay
  reorder_prob: 0.0
  drop_prob: 0.0
  seed_salt: 0         # XORed with the scenario seed for the model rng
```

## Why this matters

Real systems fail under tail latency, not average latency. With `netem`:

- `mean_latency` becomes meaningful.
- New `p50_latency`, `p95_latency`, `p99_latency`, `max_latency` metrics
  reflect the actual distribution.
- A scenario-level `slo:` block lets you assert latency / availability
  budgets as part of the validators run, e.g.:

  ```yaml
  slo:
    p99_latency: 0.250      # 250ms, in seconds
    min_delivery_rate: 0.99
  ```

  Pass-or-fail per budget shows up in `runner.validations` and via
  `nest_core.validators.validate_trace(..., slo=...)`.

## Determinism

`netem` uses an independent PRNG seeded by `scenario.seed XOR transport.seed_salt`
so:

- The same scenario YAML reruns produce byte-identical traces.
- You can perturb only the transport (e.g. for chaos tests) by changing
  `seed_salt` without changing agent behaviour.

## Standalone (Tier 2)

`StandaloneNetemTransport` lives in `nest_plugins_reference.transport.netem`
and uses `asyncio.sleep` for delays — useful for shell / LLM agents.

```python
from nest_plugins_reference.transport.in_memory import InMemoryNetwork
from nest_plugins_reference.transport.netem import (
    StandaloneNetemTransport,
    make_delay_model,
)

network = InMemoryNetwork()
model = make_delay_model({
    "latency": {"kind": "lognormal", "p50_ms": 20, "p99_ms": 200},
    "jitter_ms": 2,
    "bandwidth_kbps": 1000,
})
t = StandaloneNetemTransport("a1", network, model)
await t.send("a2", b"hello")
```

## Try it

`marketplace-netem.yaml` here is the bundled marketplace with `netem` and
an attached SLO. Save it locally and run:

```bash
nest run marketplace-netem.yaml -o ./traces/netem.jsonl
python -c "
from pathlib import Path
from nest_core.validators import validate_trace
import yaml
cfg = yaml.safe_load(Path('marketplace-netem.yaml').read_text())
results = validate_trace(Path('traces/netem.jsonl'), cfg['task']['type'], slo=cfg['slo'])
for r in results:
    print(('PASS' if r.passed else 'FAIL'), r.name, r.detail)
"
```
