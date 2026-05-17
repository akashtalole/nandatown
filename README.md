[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/mariagorskikh/nest/actions/workflows/ci.yml/badge.svg)](https://github.com/mariagorskikh/nest/actions/workflows/ci.yml)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)]()

# NEST -- Network Environment for Swarm Testing

NEST is a discrete-event simulation framework for testing multi-agent protocols. It provides deterministic, reproducible execution with pluggable protocol layers, failure injection, and trace-based analysis.

---

## Table of Contents

- [Motivation](#motivation)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Scenarios](#scenarios)
- [The 12 Protocol Layers](#the-12-protocol-layers)
- [Protocol Validation](#protocol-validation)
- [Fidelity Tiers](#fidelity-tiers)
- [Writing a Plugin](#writing-a-plugin)
- [Project Structure](#project-structure)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [Citation](#citation)
- [License](#license)

---

## Motivation

Multi-agent systems are increasingly deployed for tasks ranging from marketplace coordination to distributed consensus. Testing these systems is difficult: you cannot unit-test emergent behavior, and real-world deployments are expensive, slow, and non-reproducible. Existing simulation tools tend to be either too abstract (missing protocol-level detail) or too tightly coupled to a specific agent framework.

NEST addresses this gap. Given a scenario definition, NEST instantiates N agents with configurable protocol layers, runs them through a discrete-event simulator with a virtual clock, injects failures (message drops, Byzantine agents, network partitions), and produces a deterministic JSONL trace. The same seed always produces the same trace. Researchers can then analyze, diff, and replay traces to understand exactly what happened and why.

NEST is part of [Project NANDA](https://projectnanda.org), an open research initiative exploring infrastructure for AI agent ecosystems. It is open-source under the Apache 2.0 license.

## Architecture

```
Scenario YAML --> Plugin Resolution --> Agent Creation --> Discrete-Event Simulator --> JSONL Trace --> Analysis
```

Key design decisions:

- **Deterministic seeded RNG.** A master RNG seeded once at startup derives per-agent RNGs and a separate failure-injection RNG. Same seed = identical trace, regardless of host platform or execution timing.
- **Virtual clock with event priority queue.** Events are ordered by `(time, sequence)` in a min-heap. The clock advances only when the next event is popped. No wall-clock dependency.
- **Per-agent RNG isolation.** Each agent receives its own `random.Random` instance derived from the master seed. Adding or removing agents does not change other agents' random sequences.
- **Correlation IDs.** Every message is tagged with a monotonically increasing correlation ID, enabling end-to-end latency tracking across the trace.
- **Failure injection.** Three failure modes are configurable per scenario:
  - *Message drops:* probabilistic per-message drop rate
  - *Byzantine agents:* a fraction of agents have their payloads garbled (XOR with random bytes)
  - *Network partitions:* agents are assigned to groups; cross-group messages are silently dropped

## Quick Start

```bash
# Install (uv workspace)
git clone https://github.com/mariagorskikh/nest.git
cd nest
uv sync

# Run a scenario
nest run scenarios/marketplace.yaml

# Inspect the trace
nest inspect traces/marketplace.jsonl

# Generate an HTML report
nest report traces/marketplace.jsonl -o report.html

# Check your setup
nest doctor
```

Or install from PyPI:

```bash
pip install "nest-core[plugins]"
nest run scenarios/marketplace.yaml
```

## Scenarios

NEST ships with seven reference scenarios. Each is a YAML file that configures agents, protocol layers, failure parameters, and metrics.

| Scenario | Agents | What it tests | Notes |
|---|---|---|---|
| `marketplace` | 50 buyers, 50 sellers | Price negotiation via bilateral messaging. Buyers send offers; sellers accept/reject based on a minimum price threshold. | Exercises negotiation, payments, and registry layers. |
| `auction` | 1 auctioneer, 19 bidders | Sealed-bid auction with multiple rounds. Auctioneer announces items; highest bidder wins each round. | Tests coordination and multi-round messaging. |
| `voting` | 1 proposer, 1 coordinator, 18 voters | Majority-threshold voting. Proposer broadcasts proposals; voters send yes/no to coordinator; coordinator tallies and announces results. | Simple majority rule, not BFT. |
| `consensus` | 1 leader, 19 followers | Leader-based quorum voting with configurable quorum threshold (default 2/3). Leader proposes values; followers vote accept/reject; leader commits if quorum is reached. | Simplified quorum protocol. Not a full BFT implementation -- useful for testing quorum-based agreement patterns. |
| `supply_chain` | 4 (supplier, manufacturer, distributor, retailer) | Multi-hop message forwarding through a linear pipeline. Each stage processes and forwards to the next. | Tests message reliability and multi-hop latency under failure injection. |
| `reputation` | 16 honest, 4 malicious, 1 observer | Reputation tracking with adversarial agents. Malicious agents cheat probabilistically; observer tracks scores and broadcasts warnings. | Tests trust layer and detection of misbehavior. |
| `shell_marketplace` | 3 buyers, 3 sellers (LLM-backed) | Same as marketplace but using LLM-backed agents (Tier 2). | Experimental. Non-deterministic. Uses mock LLM backend by default. |

Each scenario can be run with failure injection by editing the YAML:

```yaml
failures:
  message_drop: 0.05       # 5% of messages dropped
  byzantine_agents: 0.10    # 10% of agents send garbled messages
  network_partition:
    groups: [["agent-0", "agent-1"], ["agent-2", "agent-3"]]
```

## The 12 Protocol Layers

Every layer is defined as a Python `Protocol` (structural typing). Plugins are resolved by name at scenario load time via entry points or built-in defaults.

| # | Layer | Interface | Default Plugin | Notes |
|---|---|---|---|---|
| 1 | Transport | `Transport` | `in_memory` | In-process message routing via event queue. No network I/O. |
| 2 | Communication | `CommsProtocol` | `nest_native` | JSON envelope with base64 payload encoding. |
| 3 | Identity | `Identity` | `did_key` | HMAC-SHA256 signatures with hash-derived keys. Not Ed25519 -- simplified for simulation. |
| 4 | Registry | `Registry` | `in_memory` | Dict-based agent lookup. No persistence or replication. |
| 5 | Auth | `Auth` | `jwt` | HMAC-SHA256 signed JSON tokens. Not real JWT (no header, no standard claims). |
| 6 | Trust | `Trust` | `score_average` | Running-mean reputation from reported evidence. No Sybil resistance. |
| 7 | Payments | `Payments` | `prepaid_credits` | In-memory debit/credit ledger. No double-spend protection beyond single-process state. |
| 8 | Coordination | `Coordination` | `contract_net` | FIPA Contract Net Protocol: propose, bid, resolve, commit. |
| 9 | Negotiation | `Negotiation` | `alternating_offers` | Rubinstein-style alternating offers with patience discount factor. |
| 10 | Memory | `Memory` | `blackboard` | Shared key-value store with subscribe and compare-and-swap. |
| 11 | Privacy | `Privacy` | `noop` | Stub -- passthrough. Encrypt/decrypt are identity functions; proofs always verify. |
| 12 | Data Facts | `DataFacts` | `datafacts_v1` | Dataset metadata publish/fetch/access-control. |

All default plugins are reference implementations intended for testing. They prioritize clarity over completeness.

## Protocol Validation

NEST includes protocol validators (`nest_core.validators`) that check scenario-specific correctness invariants against JSONL traces -- not just message counts, but actual protocol properties.

| Scenario | Validators |
|---|---|
| Marketplace | No double-sell (same product to two buyers); every buy request gets a sold/reject response; sale prices match offered prices. |
| Auction | Winner has the highest bid; exactly one winner per item; all bidders notified of outcome. |
| Voting | Announced tally matches actual vote count; every vote is counted; no voter votes twice per round. |
| Consensus | Committed rounds have >= 2/3 accept votes; only proposed values are committed; at most one value committed per round. |
| Supply chain | Delivered goods trace through all four pipeline hops; no materials lost in transit. |
| Reputation | Cheating agents receive bad reports; agents with score <= -3 are warned. |

```bash
# Run validators programmatically
from nest_core.validators import validate_trace
results = validate_trace(Path("traces/marketplace.jsonl"), "marketplace")
for r in results:
    print(f"{'PASS' if r.passed else 'FAIL'}: {r.name}")
```

## Fidelity Tiers

| Tier | Agent type | Clock | Scale | Deterministic | Use case |
|---|---|---|---|---|---|
| **Tier 1** | State-machine (`StateMachineAgent`) | Virtual (discrete-event) | 10,000+ agents | Yes | Protocol correctness testing, parameter sweeps, regression benchmarks. |
| **Tier 2** (Experimental) | LLM-backed (`ShellAgent`) via OpenAI/Anthropic SDK | Virtual (discrete-event) | 10--100 agents | No | Exploring emergent behavior with language model agents. Not suitable for reproducible benchmarks. |

Tier 2 agents use the same simulator, virtual clock, and trace format as Tier 1. The difference is that agent decisions are delegated to an LLM backend (or a mock backend for CI). Because LLM outputs are non-deterministic, Tier 2 traces are not reproducible across runs even with the same seed.

## Writing a Plugin

A plugin implements one layer interface and registers via Python entry points.

```python
# my_payments/plugin.py
from nest_sdk import Payments, AgentId, Money, PaymentRef, Receipt, Quote, ServiceRef, PaymentStatus

class MyPaymentProtocol(Payments):
    """Custom payment protocol implementation."""

    async def quote(self, service: ServiceRef) -> Quote:
        ...

    async def pay(self, to: AgentId, amount: Money, ref: PaymentRef) -> Receipt:
        ...

    async def verify_payment(self, ref: PaymentRef) -> PaymentStatus:
        ...

    async def refund(self, ref: PaymentRef) -> None:
        ...
```

```toml
# pyproject.toml
[project.entry-points."nest.plugins.payments"]
my_payment = "my_payments.plugin:MyPaymentProtocol"
```

Then reference it in a scenario:

```yaml
layers:
  payments: my_payment
```

## Project Structure

```
nest/
+-- packages/
|   +-- nest-core/              # Simulator engine, CLI, layer interfaces, scenario runner
|   +-- nest-sdk/               # Public API re-exports for plugin authors
|   +-- nest-cli/               # (Deprecated — CLI is now in nest-core)
|   +-- nest-mocks/             # In-memory mock services for testing
|   +-- nest-shell/             # Tier 2 LLM-backed agent (OpenAI, Anthropic, mock)
|   +-- nest-scenarios/         # Scenario registration and discovery
|   +-- nest-plugins-reference/ # Default plugins for all 12 layers
+-- scenarios/                  # Reference scenario YAML files
+-- templates/                  # Agent prompt templates for Tier 2
+-- apps/
|   +-- dashboard/              # Interactive trace viewer (HTML/JS)
+-- .github/workflows/          # CI: lint (ruff), typecheck (pyright), test (pytest)
```

## Limitations

- **Reference plugins are simplified.** The default plugins are intended for testing protocol interactions, not production use. For example, the identity plugin uses HMAC-SHA256 instead of Ed25519, the auth plugin is not standards-compliant JWT, and the privacy plugin is a no-op passthrough.
- **Scenarios test messaging patterns, not formal protocol properties.** The built-in scenarios verify that agents exchange the right messages in the right order, and metrics (success rate, latency, throughput) are computed from traces. There is no formal verification, model checking, or TLA+ integration.
- **Tier 2 (LLM) agents are experimental and non-deterministic.** LLM-backed agents are useful for exploring how language models behave in multi-agent settings, but traces are not reproducible and should not be used for benchmarking.
- **Single-process only.** The simulator runs in a single Python process. There is no distributed execution or multi-node support.
- **No real networking.** The transport layer is in-memory. There is no TCP, HTTP, or gRPC transport plugin yet.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and how to add scenarios and plugins.

Issues and pull requests are welcome at [github.com/mariagorskikh/nest](https://github.com/mariagorskikh/nest).

## Citation

If you use NEST in academic work, please cite:

```bibtex
@software{nest2026,
  title  = {NEST: Network Environment for Swarm Testing},
  author = {MIT Media Lab},
  year   = {2026},
  url    = {https://github.com/mariagorskikh/nest},
  license = {Apache-2.0}
}
```

## License

Apache 2.0 -- see [LICENSE](LICENSE).
