# SPDX-License-Identifier: Apache-2.0
"""Scenario factories for KumbhNet — registers kumbh_peak_bathing and kumbh_flood_surge.

Import this module once (e.g. in your run script or conftest) to register
both factories with Nanda Town's scenario registry so ``nest run`` can resolve
them.

Example::

    import nest_plugins_reference.kumbh2027.scenarios  # registers factories
    # then: nest run scenarios/kumbh_peak_bathing.yaml
"""

from __future__ import annotations

from typing import Any

from nest_core.scenario import ScenarioConfig
from nest_core.scenarios import register_scenario
from nest_core.sim.agent import AgentContext, StateMachineAgent
from nest_core.types import AgentCard, AgentId, Task
from nest_plugins_reference.kumbh2027.zone_registry_gossip import KumbhZoneGossipRegistry


# ---------------------------------------------------------------------------
# Shared agent base classes
# ---------------------------------------------------------------------------


class ZoneAgent(StateMachineAgent):
    """Simulates a physical Kumbh zone: publishes density, votes on closure.

    Example::

        agent = ZoneAgent(AgentId("zone-agent-0"), zone_id="ramkund_main",
                          capacity=8000, density=4.1)
    """

    def __init__(
        self,
        agent_id: AgentId,
        zone_id: str,
        capacity: int,
        density: float = 3.0,
        count: int = 0,
        hard_cap: bool = False,
        city: str = "nashik",
    ) -> None:
        self._id = agent_id
        self._zone_id = zone_id
        self._capacity = capacity
        self._density = density
        self._count = count
        self._hard_cap = hard_cap
        self._city = city
        self._closed = False

    async def on_start(self, ctx: AgentContext) -> None:
        card = AgentCard(
            agent_id=self._id,
            name=f"Zone-{self._zone_id}",
            capabilities=["zone:status", "zone:vote"],
            metadata={
                "zone_id": self._zone_id,
                "city": self._city,
                "capacity": str(self._capacity),
                "hard_cap": str(self._hard_cap),
            },
        )
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(card)
        # Announce initial density to all peers
        await ctx.broadcast(
            f"density:{self._zone_id}:{self._density}:{self._count}".encode()
        )

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")

        if msg.startswith("arrival:"):
            # arrival:<zone_id>:<count>
            parts = msg.split(":")
            if len(parts) >= 3 and parts[1] == self._zone_id:
                self._count += int(parts[2])
                self._density = self._count / max(1, self._capacity / 1000)
                await ctx.broadcast(
                    f"density:{self._zone_id}:{self._density}:{self._count}".encode()
                )
                # Hard cap check — Kushavart Kund
                if self._hard_cap and self._count > 1900 and not self._closed:
                    self._closed = True
                    await ctx.broadcast(f"close:{self._zone_id}:hard_cap".encode())

        elif msg.startswith("close:"):
            parts = msg.split(":")
            if len(parts) >= 2 and parts[1] == self._zone_id:
                self._closed = True


class AmbulanceAgent(StateMachineAgent):
    """Ambulance agent: waits for dispatch requests.

    Example::

        agent = AmbulanceAgent(AgentId("ambulance-agent-0"), city="nashik")
    """

    def __init__(self, agent_id: AgentId, city: str = "nashik") -> None:
        self._id = agent_id
        self._city = city
        self._dispatched = 0

    async def on_start(self, ctx: AgentContext) -> None:
        card = AgentCard(
            agent_id=self._id,
            name=f"Ambulance-{self._id}",
            capabilities=["ambulance:dispatch"],
            metadata={"city": self._city},
        )
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(card)

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("dispatch:"):
            self._dispatched += 1
            await ctx.send(sender, f"dispatched:{self._id}:{self._dispatched}".encode())


class PilgrimAgent(StateMachineAgent):
    """Pilgrim agent: sends SOS if in a crowded zone.

    Example::

        agent = PilgrimAgent(AgentId("pilgrim-agent-0"), zone_id="ramkund_main")
    """

    def __init__(self, agent_id: AgentId, zone_id: str = "ramkund_main") -> None:
        self._id = agent_id
        self._zone_id = zone_id
        self._sos_sent = False

    async def on_start(self, ctx: AgentContext) -> None:
        card = AgentCard(
            agent_id=self._id,
            name=f"Pilgrim-{self._id}",
            capabilities=["pilgrim:sos"],
            metadata={"zone_id": self._zone_id},
        )
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(card)

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("close:") and not self._sos_sent:
            parts = msg.split(":")
            if len(parts) >= 2 and parts[1] == self._zone_id:
                self._sos_sent = True
                await ctx.broadcast(f"sos:{self._id}:{self._zone_id}".encode())


class CommandBridgeAgent(StateMachineAgent):
    """Central command: aggregates alerts, issues closures.

    Example::

        agent = CommandBridgeAgent(AgentId("command-bridge-0"))
    """

    def __init__(self, agent_id: AgentId) -> None:
        self._id = agent_id
        self._alerts: list[str] = []
        self._closures: list[str] = []

    async def on_start(self, ctx: AgentContext) -> None:
        card = AgentCard(
            agent_id=self._id,
            name="CommandBridge",
            capabilities=["command:broadcast", "zone:close"],
            metadata={"role": "command_bridge"},
        )
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(card)

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("density:"):
            parts = msg.split(":")
            if len(parts) >= 4:
                zone_id, density, count = parts[1], float(parts[2]), int(parts[3])
                if density > 6.5 or (zone_id == "kushavart_kund" and count > 1900):
                    alert = f"ALERT:{zone_id}:density={density:.1f}"
                    self._alerts.append(alert)
                    await ctx.broadcast(f"close:{zone_id}:command".encode())
                    self._closures.append(zone_id)

        elif msg.startswith("sos:"):
            self._alerts.append(msg)
            # Try to dispatch an ambulance via per-agent gossip registry
            from nest_core.types import Query
            registry = ctx.plugins.get("registry")
            if registry is not None:
                ambulances = await registry.lookup(Query(capabilities=["ambulance:dispatch"]))
                if ambulances:
                    await ctx.send(ambulances[0].agent_id, f"dispatch:{msg}".encode())


class FloodWatchAgent(StateMachineAgent):
    """Monitors Godavari water level; issues flood alert when threshold crossed.

    Example::

        agent = FloodWatchAgent(AgentId("flood-watch-agent-0"), level_cm=850, threshold_cm=900)
    """

    def __init__(self, agent_id: AgentId, level_cm: int = 850, threshold_cm: int = 900) -> None:
        self._id = agent_id
        self._level_cm = level_cm
        self._threshold_cm = threshold_cm
        self._alerted = False

    async def on_start(self, ctx: AgentContext) -> None:
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(AgentCard(
                agent_id=self._id,
                name="FloodWatch",
                capabilities=["flood:monitor"],
                metadata={"level_cm": str(self._level_cm)},
            ))
        if self._level_cm >= self._threshold_cm and not self._alerted:
            self._alerted = True
            await ctx.broadcast(
                f"flood_alert:godavari:{self._level_cm}".encode()
            )

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        pass


class NDRFAgent(StateMachineAgent):
    """NDRF coordinator: responds to flood alerts with evacuation orders.

    Example::

        agent = NDRFAgent(AgentId("ndrf-agent-0"))
    """

    def __init__(self, agent_id: AgentId) -> None:
        self._id = agent_id
        self._evacuations: list[str] = []

    async def on_start(self, ctx: AgentContext) -> None:
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(AgentCard(
                agent_id=self._id,
                name="NDRF",
                capabilities=["zone:close", "evacuation:order"],
                metadata={"role": "ndrf"},
            ))

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("flood_alert:"):
            for zone in ["ramkund_main", "ramkund_west", "godavari_ghat_1", "godavari_ghat_2"]:
                self._evacuations.append(zone)
                await ctx.broadcast(f"close:{zone}:flood".encode())
        elif msg.startswith("density:"):
            parts = msg.split(":")
            if len(parts) >= 4 and float(parts[2]) > 6.5:
                zone_id = parts[1]
                if zone_id not in self._evacuations:
                    self._evacuations.append(zone_id)
                    await ctx.broadcast(f"close:{zone_id}:surge".encode())


# ---------------------------------------------------------------------------
# Scenario factories
# ---------------------------------------------------------------------------


def _build_zone_agents(
    config: ScenarioConfig,
) -> tuple[dict[AgentId, Any], int, int]:
    """Build zone and ambulance agents from scenario config zones."""
    task_cfg = config.task.config or {}
    zones: list[dict[str, Any]] = task_cfg.get("zones", [])
    arrival_waves: list[dict[str, Any]] = task_cfg.get("arrival_waves", [])

    # Map zone_id → initial count from first arrival wave
    zone_counts: dict[str, int] = {}
    for wave in arrival_waves:
        zid = wave.get("zone", "")
        zone_counts[zid] = zone_counts.get(zid, 0) + wave.get("count", 0)

    agents: dict[AgentId, Any] = {}
    zone_idx = 0
    for z in zones:
        aid = AgentId(f"zone-agent-{zone_idx}")
        agents[aid] = ZoneAgent(
            agent_id=aid,
            zone_id=z["id"],
            capacity=z.get("capacity", 5000),
            density=0.0,
            count=zone_counts.get(z["id"], 0),
            hard_cap=z.get("hard_cap", False),
            city=z.get("city", "nashik"),
        )
        zone_idx += 1

    return agents, zone_idx, len(zones)


def kumbh_peak_bathing_factory(
    config: ScenarioConfig, plugins: dict[str, Any]
) -> dict[AgentId, Any]:
    """Build 118-agent peak-bathing-day fleet.

    Roles: 12 zone agents, 5 ambulance agents, 100 pilgrim agents, 1 command bridge.

    Example::

        agents = kumbh_peak_bathing_factory(config, plugins)
    """
    agents: dict[AgentId, Any] = {}

    # Zone agents from YAML config
    zone_agents, zone_idx, n_zones = _build_zone_agents(config)
    agents.update(zone_agents)

    # Ambulance agents
    n_ambulances = 0
    for role in config.agents.roles:
        if role.name == "ambulance_agent":
            n_ambulances = role.count
            break
    for i in range(n_ambulances):
        aid = AgentId(f"ambulance-agent-{i}")
        city = "nashik" if i < 3 else "trimbakeshwar"
        agents[aid] = AmbulanceAgent(aid, city=city)

    # Pilgrim agents distributed across zones
    n_pilgrims = 0
    for role in config.agents.roles:
        if role.name == "pilgrim_agent":
            n_pilgrims = role.count
            break
    task_cfg = config.task.config or {}
    zones = task_cfg.get("zones", [])
    for i in range(n_pilgrims):
        aid = AgentId(f"pilgrim-agent-{i}")
        zone_id = zones[i % len(zones)]["id"] if zones else "ramkund_main"
        agents[aid] = PilgrimAgent(aid, zone_id=zone_id)

    # Command bridge
    agents[AgentId("command-bridge-0")] = CommandBridgeAgent(AgentId("command-bridge-0"))

    # Inject per-agent KumbhZoneGossipRegistry instances
    agent_plugins: dict[AgentId, dict[str, Any]] = {
        aid: {"registry": KumbhZoneGossipRegistry(aid)} for aid in agents
    }
    plugins["_agent_plugins"] = agent_plugins

    return agents


def kumbh_flood_surge_factory(
    config: ScenarioConfig, plugins: dict[str, Any]
) -> dict[AgentId, Any]:
    """Build 25-agent flood-surge fleet.

    Roles: 12 zone agents, 5 ambulances, FloodWatch, CrowdSentinel,
    MedEvac, CommandBridge (isolated), NDRF, 3 pilgrims.

    Example::

        agents = kumbh_flood_surge_factory(config, plugins)
    """
    agents: dict[AgentId, Any] = {}
    task_cfg = config.task.config or {}

    # 12 zone agents with flood-aware initial counts
    surge_zone = task_cfg.get("surge_zone", "ramkund_main")
    surge_count = task_cfg.get("surge_count", 7500)
    for i in range(12):
        aid = AgentId(f"zone-agent-{i}")
        zone_id = [
            "ramkund_main", "ramkund_west", "godavari_ghat_1", "godavari_ghat_2",
            "panchavati_main", "tapovan_ghat", "dudhsagar_ghat", "saraswati_kund",
            "kushavart_kund", "kushavart_approach", "trimbakeshwar_main", "brahmagiri_ghat",
        ][i]
        agents[aid] = ZoneAgent(
            agent_id=aid,
            zone_id=zone_id,
            capacity=8000,
            count=surge_count if zone_id == surge_zone else 1000,
            hard_cap=(zone_id == "kushavart_kund"),
            city="trimbakeshwar" if "trimbakeshwar" in zone_id or "kushavart" in zone_id
                 else "nashik",
        )

    # 5 ambulances
    for i in range(5):
        aid = AgentId(f"ambulance-agent-{i}")
        agents[aid] = AmbulanceAgent(aid, city="nashik" if i < 3 else "trimbakeshwar")

    # Specialist agents
    agents[AgentId("flood-watch-agent-0")] = FloodWatchAgent(
        AgentId("flood-watch-agent-0"),
        level_cm=task_cfg.get("godavari_level_cm", 850),
        threshold_cm=900,
    )
    agents[AgentId("crowd-sentinel-agent-0")] = CommandBridgeAgent(
        AgentId("crowd-sentinel-agent-0")
    )
    agents[AgentId("med-evac-agent-0")] = AmbulanceAgent(
        AgentId("med-evac-agent-0"), city="nashik"
    )
    agents[AgentId("command-bridge-0")] = CommandBridgeAgent(AgentId("command-bridge-0"))
    agents[AgentId("ndrf-agent-0")] = NDRFAgent(AgentId("ndrf-agent-0"))

    # 3 pilgrims
    for i in range(3):
        aid = AgentId(f"pilgrim-agent-{i}")
        agents[aid] = PilgrimAgent(aid, zone_id=surge_zone)

    # Inject per-agent KumbhZoneGossipRegistry instances
    agent_plugins: dict[AgentId, dict[str, Any]] = {
        aid: {"registry": KumbhZoneGossipRegistry(aid)} for aid in agents
    }
    plugins["_agent_plugins"] = agent_plugins

    return agents


# ---------------------------------------------------------------------------
# Auto-register on import
# ---------------------------------------------------------------------------

register_scenario("kumbh_peak_bathing", kumbh_peak_bathing_factory)
register_scenario("kumbh_flood_surge", kumbh_flood_surge_factory)
