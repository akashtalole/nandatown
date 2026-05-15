# SPDX-License-Identifier: Apache-2.0
"""Multi-hop supply-chain scenario.

Goods flow through supplier -> manufacturer -> distributor -> retailer.
Each stage forwards items to the next, exercising multi-hop messaging.

Example::

    agents = supply_chain_factory(config, plugins)
"""

from __future__ import annotations

from typing import Any

from nest_core.scenario import ScenarioConfig
from nest_core.sim.agent import AgentContext, StateMachineAgent
from nest_core.types import AgentId


class SupplierAgent(StateMachineAgent):
    """Produces raw materials and sends them to manufacturers."""

    def __init__(
        self,
        agent_id: AgentId,
        next_stage: AgentId,
        items_per_round: int = 2,
        rounds: int = 3,
    ) -> None:
        self._id = agent_id
        self._next = next_stage
        self._items_per_round = items_per_round
        self._rounds = rounds

    async def on_start(self, ctx: AgentContext) -> None:
        for rnd in range(1, self._rounds + 1):
            for item in range(self._items_per_round):
                batch = f"raw-{self._id}-r{rnd}-i{item}"
                await ctx.send(self._next, f"material:{rnd}:{batch}".encode())


class ManufacturerAgent(StateMachineAgent):
    """Receives materials, produces goods, sends to distributors."""

    def __init__(self, agent_id: AgentId, next_stage: AgentId) -> None:
        self._id = agent_id
        self._next = next_stage
        self._produced = 0

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if not msg.startswith("material:"):
            return
        parts = msg.split(":")
        if len(parts) < 3:
            return
        rnd = parts[1]
        self._produced += 1
        product = f"good-{self._id}-{self._produced}"
        await ctx.send(self._next, f"product:{rnd}:{product}".encode())


class DistributorAgent(StateMachineAgent):
    """Receives goods from manufacturers and forwards to retailers."""

    def __init__(self, agent_id: AgentId, next_stage: AgentId) -> None:
        self._id = agent_id
        self._next = next_stage
        self._forwarded = 0

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if not msg.startswith("product:"):
            return
        parts = msg.split(":")
        if len(parts) < 3:
            return
        rnd, product = parts[1], parts[2]
        self._forwarded += 1
        await ctx.send(self._next, f"shipment:{rnd}:{product}".encode())


class RetailerAgent(StateMachineAgent):
    """Receives goods and reports completion back to the supplier."""

    def __init__(self, agent_id: AgentId, origin: AgentId) -> None:
        self._id = agent_id
        self._origin = origin
        self._received = 0

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if not msg.startswith("shipment:"):
            return
        parts = msg.split(":")
        if len(parts) < 3:
            return
        rnd, product = parts[1], parts[2]
        self._received += 1
        await ctx.send(self._origin, f"delivered:{rnd}:{product}".encode())


def supply_chain_factory(
    config: ScenarioConfig,
    plugins: dict[str, Any],
) -> dict[AgentId, StateMachineAgent]:
    """Create a linear supply-chain: supplier -> mfg -> dist -> retailer.

    Example::

        agents = supply_chain_factory(config, plugins)
    """
    task_config = config.task.config
    items_per_round = task_config.get("items_per_round", 2)
    rounds = task_config.get("rounds", 3)

    agents: dict[AgentId, StateMachineAgent] = {}

    retailer_id = AgentId("retailer-0")
    distributor_id = AgentId("distributor-0")
    manufacturer_id = AgentId("manufacturer-0")
    supplier_id = AgentId("supplier-0")

    agents[supplier_id] = SupplierAgent(
        supplier_id,
        next_stage=manufacturer_id,
        items_per_round=items_per_round,
        rounds=rounds,
    )
    agents[manufacturer_id] = ManufacturerAgent(manufacturer_id, next_stage=distributor_id)
    agents[distributor_id] = DistributorAgent(distributor_id, next_stage=retailer_id)
    agents[retailer_id] = RetailerAgent(retailer_id, origin=supplier_id)

    return agents
