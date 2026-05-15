# SPDX-License-Identifier: Apache-2.0
"""Byzantine fault-tolerant consensus scenario.

A leader proposes values, followers vote accept/reject,
and consensus is reached when a quorum agrees.

Example::

    agents = consensus_factory(config, plugins)
"""

from __future__ import annotations

from typing import Any

from nest_core.scenario import ScenarioConfig
from nest_core.sim.agent import AgentContext, StateMachineAgent
from nest_core.types import AgentId


class LeaderAgent(StateMachineAgent):
    """Proposes values and collects votes until quorum or max rounds."""

    def __init__(
        self,
        agent_id: AgentId,
        num_followers: int,
        rounds: int = 5,
        quorum: float = 2 / 3,
    ) -> None:
        self._id = agent_id
        self._num_followers = num_followers
        self._rounds = rounds
        self._quorum = quorum
        self._round = 0
        self._votes: dict[str, list[str]] = {}
        self._decided: set[str] = set()

    async def on_start(self, ctx: AgentContext) -> None:
        self._round = 1
        value = ctx.rng.randint(1, 100)
        for i in range(self._num_followers):
            follower = AgentId(f"follower-{i}")
            await ctx.send(follower, f"propose:{self._round}:{value}".encode())

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if not msg.startswith("vote:"):
            return
        parts = msg.split(":")
        if len(parts) < 3:
            return
        rnd, vote = parts[1], parts[2]
        self._votes.setdefault(rnd, []).append(vote)
        if len(self._votes[rnd]) < self._num_followers:
            return
        if rnd in self._decided:
            return
        self._decided.add(rnd)
        accepts = sum(1 for v in self._votes[rnd] if v == "accept")
        total = len(self._votes[rnd])
        reached = accepts / total >= self._quorum
        result = "committed" if reached else "aborted"
        for i in range(self._num_followers):
            follower = AgentId(f"follower-{i}")
            await ctx.send(follower, f"result:{rnd}:{result}:{accepts}/{total}".encode())
        self._round += 1
        if self._round <= self._rounds and not reached:
            value = ctx.rng.randint(1, 100)
            for i in range(self._num_followers):
                follower = AgentId(f"follower-{i}")
                await ctx.send(follower, f"propose:{self._round}:{value}".encode())


class FollowerAgent(StateMachineAgent):
    """Votes accept/reject on proposals based on a random threshold."""

    def __init__(self, agent_id: AgentId, leader: AgentId) -> None:
        self._id = agent_id
        self._leader = leader

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if not msg.startswith("propose:"):
            return
        parts = msg.split(":")
        if len(parts) < 3:
            return
        rnd = parts[1]
        vote = "accept" if ctx.rng.random() > 0.3 else "reject"
        await ctx.send(self._leader, f"vote:{rnd}:{vote}".encode())


def consensus_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
) -> dict[AgentId, StateMachineAgent]:
    """Create leader and follower agents for consensus.

    Example::

        agents = consensus_factory(config, plugins)
    """
    task_config = config.task.config
    rounds = task_config.get("rounds", 5)
    quorum = task_config.get("quorum", 2 / 3)

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
    agents[leader_id] = LeaderAgent(
        leader_id, num_followers=follower_count, rounds=rounds, quorum=quorum
    )

    for i in range(follower_count):
        aid = AgentId(f"follower-{i}")
        agents[aid] = FollowerAgent(aid, leader=leader_id)

    return agents
