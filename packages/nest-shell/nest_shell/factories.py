# SPDX-License-Identifier: Apache-2.0
"""Shell agent factories for auction, voting, consensus, supply-chain, and reputation scenarios.

These factories create LLM-backed :class:`ShellAgent` instances with
scenario-appropriate system prompts so users can set ``brain: llm``
in their YAML files.

Example::

    agents = shell_auction_factory(config, plugins, backend=MockLLMBackend())
"""

from __future__ import annotations

from typing import Any

from nest_core.scenario import ScenarioConfig
from nest_core.sim.agent import StateMachineAgent
from nest_core.types import AgentId

from nest_shell.agent import ShellAgent
from nest_shell.llm import LLMBackend

_AUCTION_AUCTIONEER_PROMPT = """\
You are an auctioneer in a multi-agent auction simulation.
Your role is: auctioneer

When the simulation starts, announce an item for auction to all bidders.
When you receive bids, track them and pick the highest bidder.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Announce items with format: auction:<item>:<base_price>
- When all bids arrive, notify the winner with: won:<item>:<price>
- Notify losers with: lost:<item>:<winning_price>
- Start new rounds after announcing results.
"""

_AUCTION_BIDDER_PROMPT = """\
You are a bidder in a multi-agent auction simulation.
Your role is: bidder

When you receive an auction announcement, decide how much to bid.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- When you see auction:<item>:<base_price>, respond with bid:<item>:<your_bid>
- Your bid should be at or above the base price but within your budget.
- If you win, you receive won:<item>:<price>. If you lose, you receive lost:<item>:<price>.
"""

_VOTING_PROPOSER_PROMPT = """\
You are a proposer in a multi-agent voting simulation.
Your role is: proposer

You propose topics for voters to vote on.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Propose topics with format: propose:<round>:<topic>
- When you receive result:<round>:<outcome>:<tally>, start a new round.
- Topics can be: increase-budget, new-policy, elect-leader.
"""

_VOTING_VOTER_PROMPT = """\
You are a voter in a multi-agent voting simulation.
Your role is: voter

When you receive a proposal, cast your vote.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- When you see propose:<round>:<topic>, respond with vote:<round>:<yes_or_no>:<your_id>
- Send your vote to the coordinator (coordinator-0).
- Vote yes or no based on the topic.
"""

_VOTING_COORDINATOR_PROMPT = """\
You are a coordinator in a multi-agent voting simulation.
Your role is: coordinator

You tally votes and announce results.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Collect vote:<round>:<yes_or_no>:<voter_id> messages from voters.
- When all votes arrive, announce result:<round>:<passed_or_rejected>:<tally> to the proposer.
"""


def shell_auction_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
    backend: LLMBackend | None = None,
) -> dict[AgentId, StateMachineAgent]:
    """Create shell agents for the auction scenario.

    Example::

        agents = shell_auction_factory(config, plugins, backend=MockLLMBackend())
    """
    from nest_shell.llm import MockLLMBackend

    if backend is None:
        backend = MockLLMBackend()

    task_config = config.task.config
    rounds = task_config.get("rounds", 5)

    agents: dict[AgentId, StateMachineAgent] = {}

    if config.agents.roles:
        bidder_count = 0
        for role in config.agents.roles:
            if role.name == "bidder":
                bidder_count = role.count
        if bidder_count == 0:
            bidder_count = config.agents.count - 1
    else:
        bidder_count = config.agents.count - 1

    auctioneer_id = AgentId("auctioneer-0")
    agents[auctioneer_id] = ShellAgent(
        agent_id=auctioneer_id,
        role="auctioneer",
        backend=backend,
        system_prompt=_AUCTION_AUCTIONEER_PROMPT,
        num_sellers=bidder_count,
        rounds=rounds,
    )

    for i in range(bidder_count):
        aid = AgentId(f"bidder-{i}")
        agents[aid] = ShellAgent(
            agent_id=aid,
            role="bidder",
            backend=backend,
            system_prompt=_AUCTION_BIDDER_PROMPT,
            num_sellers=bidder_count,
            rounds=rounds,
        )

    return agents


def shell_voting_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
    backend: LLMBackend | None = None,
) -> dict[AgentId, StateMachineAgent]:
    """Create shell agents for the voting scenario.

    Example::

        agents = shell_voting_factory(config, plugins, backend=MockLLMBackend())
    """
    from nest_shell.llm import MockLLMBackend

    if backend is None:
        backend = MockLLMBackend()

    task_config = config.task.config
    rounds = task_config.get("rounds", 3)

    agents: dict[AgentId, StateMachineAgent] = {}

    if config.agents.roles:
        voter_count = 0
        for role in config.agents.roles:
            if role.name == "voter":
                voter_count = role.count
        if voter_count == 0:
            voter_count = max(1, config.agents.count - 2)
    else:
        voter_count = max(1, config.agents.count - 2)

    proposer_id = AgentId("proposer-0")
    coordinator_id = AgentId("coordinator-0")

    agents[proposer_id] = ShellAgent(
        agent_id=proposer_id,
        role="proposer",
        backend=backend,
        system_prompt=_VOTING_PROPOSER_PROMPT,
        num_sellers=voter_count,
        rounds=rounds,
    )
    agents[coordinator_id] = ShellAgent(
        agent_id=coordinator_id,
        role="coordinator",
        backend=backend,
        system_prompt=_VOTING_COORDINATOR_PROMPT,
        num_sellers=voter_count,
        rounds=rounds,
    )

    for i in range(voter_count):
        aid = AgentId(f"voter-{i}")
        agents[aid] = ShellAgent(
            agent_id=aid,
            role="voter",
            backend=backend,
            system_prompt=_VOTING_VOTER_PROMPT,
            num_sellers=voter_count,
            rounds=rounds,
        )

    return agents


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

_CONSENSUS_LEADER_PROMPT = """\
You are a leader in a Byzantine fault-tolerant consensus simulation.
Your role is: leader

Propose values to followers and collect their votes.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Propose values with format: propose:<round>:<value>
- Collect vote:<round>:<accept_or_reject> from followers.
- When all votes arrive, announce result:<round>:<committed_or_aborted>:<tally>.
"""

_CONSENSUS_FOLLOWER_PROMPT = """\
You are a follower in a Byzantine fault-tolerant consensus simulation.
Your role is: follower

Vote on proposals from the leader.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- When you see propose:<round>:<value>, respond with vote:<round>:<accept_or_reject>.
- Send your vote to the leader (leader-0).
"""


def shell_consensus_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
    backend: LLMBackend | None = None,
) -> dict[AgentId, StateMachineAgent]:
    """Create shell agents for the consensus scenario.

    Example::

        agents = shell_consensus_factory(config, plugins, backend=MockLLMBackend())
    """
    from nest_shell.llm import MockLLMBackend

    if backend is None:
        backend = MockLLMBackend()

    task_config = config.task.config
    rounds = task_config.get("rounds", 5)

    agents: dict[AgentId, StateMachineAgent] = {}

    if config.agents.roles:
        follower_count = 0
        for role in config.agents.roles:
            if role.name == "follower":
                follower_count = role.count
        if follower_count == 0:
            follower_count = config.agents.count - 1
    else:
        follower_count = config.agents.count - 1

    leader_id = AgentId("leader-0")
    agents[leader_id] = ShellAgent(
        agent_id=leader_id,
        role="leader",
        backend=backend,
        system_prompt=_CONSENSUS_LEADER_PROMPT,
        num_sellers=follower_count,
        rounds=rounds,
    )

    for i in range(follower_count):
        aid = AgentId(f"follower-{i}")
        agents[aid] = ShellAgent(
            agent_id=aid,
            role="follower",
            backend=backend,
            system_prompt=_CONSENSUS_FOLLOWER_PROMPT,
            num_sellers=follower_count,
            rounds=rounds,
        )

    return agents


# ---------------------------------------------------------------------------
# Supply-chain
# ---------------------------------------------------------------------------

_SUPPLY_CHAIN_SUPPLIER_PROMPT = """\
You are a supplier in a multi-hop supply-chain simulation.
Your role is: supplier

Produce raw materials and send them to manufacturers.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Send materials with format: material:<round>:<batch_id>
- Your target is manufacturer-0.
"""

_SUPPLY_CHAIN_MFG_PROMPT = """\
You are a manufacturer in a multi-hop supply-chain simulation.
Your role is: manufacturer

Receive materials, produce goods, and send to distributors.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- When you receive material:<round>:<batch>, produce and send product:<round>:<product_id>.
- Your target is distributor-0.
"""

_SUPPLY_CHAIN_DIST_PROMPT = """\
You are a distributor in a multi-hop supply-chain simulation.
Your role is: distributor

Receive goods and forward shipments to retailers.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- When you receive product:<round>:<product>, forward as shipment:<round>:<product>.
- Your target is retailer-0.
"""

_SUPPLY_CHAIN_RETAILER_PROMPT = """\
You are a retailer in a multi-hop supply-chain simulation.
Your role is: retailer

Receive goods and report delivery back to the supplier.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- When you receive shipment:<round>:<product>, report delivered:<round>:<product>.
- Your target is supplier-0.
"""


def shell_supply_chain_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
    backend: LLMBackend | None = None,
) -> dict[AgentId, StateMachineAgent]:
    """Create shell agents for the supply-chain scenario.

    Example::

        agents = shell_supply_chain_factory(config, plugins, backend=MockLLMBackend())
    """
    from nest_shell.llm import MockLLMBackend

    if backend is None:
        backend = MockLLMBackend()

    task_config = config.task.config
    rounds = task_config.get("rounds", 3)

    agents: dict[AgentId, StateMachineAgent] = {}

    for role_name, prompt in [
        ("supplier", _SUPPLY_CHAIN_SUPPLIER_PROMPT),
        ("manufacturer", _SUPPLY_CHAIN_MFG_PROMPT),
        ("distributor", _SUPPLY_CHAIN_DIST_PROMPT),
        ("retailer", _SUPPLY_CHAIN_RETAILER_PROMPT),
    ]:
        aid = AgentId(f"{role_name}-0")
        agents[aid] = ShellAgent(
            agent_id=aid,
            role=role_name,
            backend=backend,
            system_prompt=prompt,
            num_sellers=1,
            rounds=rounds,
        )

    return agents


# ---------------------------------------------------------------------------
# Reputation
# ---------------------------------------------------------------------------

_REPUTATION_HONEST_PROMPT = """\
You are an honest trader in a reputation simulation.
Your role is: honest

Always deliver on trades and report bad actors.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Initiate trades with format: trade:<round>:<your_id>
- Always respond to trades with: deliver:<round>:<your_id>
- Report outcomes to observer-0 with: report:<round>:<agent>:<good_or_bad>
"""

_REPUTATION_MALICIOUS_PROMPT = """\
You are a malicious trader in a reputation simulation.
Your role is: malicious

Sometimes cheat on trades to game the system.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Initiate trades with format: trade:<round>:<your_id>
- Sometimes respond with: cheat:<round>:<your_id> instead of delivering.
- You may also deliver honestly to build reputation.
"""

_REPUTATION_OBSERVER_PROMPT = """\
You are an observer in a reputation simulation.
Your role is: observer

Track reputation scores and broadcast warnings about bad actors.

Respond in this exact format:

ACTION: send
TO: <agent-id>
MESSAGE: <message-content>

Or if no action is needed:
ACTION: none

Rules:
- Collect report:<round>:<agent>:<good_or_bad> messages.
- Track scores: +1 for good, -2 for bad.
- When an agent's score drops to -3 or below, broadcast warning:<round>:<agent>:untrusted.
"""


def shell_reputation_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
    backend: LLMBackend | None = None,
) -> dict[AgentId, StateMachineAgent]:
    """Create shell agents for the reputation scenario.

    Example::

        agents = shell_reputation_factory(config, plugins, backend=MockLLMBackend())
    """
    from nest_shell.llm import MockLLMBackend

    if backend is None:
        backend = MockLLMBackend()

    task_config = config.task.config
    rounds = task_config.get("rounds", 5)
    malicious_fraction = task_config.get("malicious_fraction", 0.2)

    agents: dict[AgentId, StateMachineAgent] = {}

    trader_count = config.agents.count - 1
    malicious_count = max(1, int(trader_count * malicious_fraction))
    honest_count = trader_count - malicious_count

    if config.agents.roles:
        for role in config.agents.roles:
            if role.name == "honest":
                honest_count = role.count
            elif role.name == "malicious":
                malicious_count = role.count

    observer_id = AgentId("observer-0")
    agents[observer_id] = ShellAgent(
        agent_id=observer_id,
        role="observer",
        backend=backend,
        system_prompt=_REPUTATION_OBSERVER_PROMPT,
        num_sellers=honest_count + malicious_count,
        rounds=rounds,
    )

    for i in range(honest_count):
        aid = AgentId(f"honest-{i}")
        agents[aid] = ShellAgent(
            agent_id=aid,
            role="honest",
            backend=backend,
            system_prompt=_REPUTATION_HONEST_PROMPT,
            num_sellers=honest_count + malicious_count,
            rounds=rounds,
        )

    for i in range(malicious_count):
        aid = AgentId(f"malicious-{i}")
        agents[aid] = ShellAgent(
            agent_id=aid,
            role="malicious",
            backend=backend,
            system_prompt=_REPUTATION_MALICIOUS_PROMPT,
            num_sellers=honest_count + malicious_count,
            rounds=rounds,
        )

    return agents
