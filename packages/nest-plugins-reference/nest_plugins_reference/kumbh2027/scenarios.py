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
from nest_core.types import AgentCard, AgentId, Query
from nest_plugins_reference.kumbh2027.zone_registry_gossip import KumbhZoneGossipRegistry

# Periodic density re-broadcast interval (ticks)
_DENSITY_TICK_INTERVAL = 30
_DRIVER_TICK = b"__driver_tick__"
_ZONE_TICK = b"__zone_tick__"


# ---------------------------------------------------------------------------
# Shared agent base classes
# ---------------------------------------------------------------------------


class ZoneAgent(StateMachineAgent):
    """Simulates a physical Kumbh zone: publishes density, votes on closure.

    Schedules periodic self-ticks to re-broadcast density so the command
    bridge sees crowd dynamics over time.

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
        self._processed_wids: set[str] = set()
        self._crush = False
        # Crush threshold: 8.5 p/sqm (beyond safe capacity headroom)
        self._crush_threshold = 8.5
        # Adjacent zone_id → AgentId mapping for panic overflow routing
        self._adjacent: dict[str, AgentId] = {}

    def _recompute_density(self) -> None:
        # Normalize to [0, 8.0] scale: density=8.0 at full capacity.
        # Threshold 6.5 ≈ 81% capacity; SOS threshold 7.0 ≈ 87.5% capacity.
        self._density = self._count * 8.0 / max(1, self._capacity)

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
        self._recompute_density()
        # Announce initial density
        await ctx.broadcast(
            f"density:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
        )
        # Arm first periodic re-broadcast tick
        await ctx.schedule(_DENSITY_TICK_INTERVAL, _ZONE_TICK)

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        # Periodic self-tick: re-broadcast current density
        if payload == _ZONE_TICK:
            self._recompute_density()
            await ctx.broadcast(
                f"density:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
            )
            if not self._closed:
                await ctx.schedule(_DENSITY_TICK_INTERVAL, _ZONE_TICK)
            return

        msg = payload.decode("utf-8", errors="replace")

        if msg.startswith("arrival:"):
            # arrival:<zone_id>:<count>:<wid>  — wid deduplicates redundant copies
            parts = msg.split(":")
            if len(parts) >= 3 and parts[1] == self._zone_id:
                wid = parts[3] if len(parts) >= 4 else None
                if wid and wid in self._processed_wids:
                    return
                if wid:
                    self._processed_wids.add(wid)
                delta = int(parts[2])
                self._count += delta
                self._recompute_density()
                await ctx.broadcast(
                    f"density:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
                )
                # Hard cap check — Kushavart Kund
                if self._hard_cap and self._count > 1900 and not self._closed:
                    self._closed = True
                    await ctx.broadcast(f"close:{self._zone_id}:hard_cap".encode())
                # Crush detection: density > 8.5 triggers stampede chain
                if self._density > self._crush_threshold and not self._crush:
                    self._crush = True
                    casualties = max(1, int((self._density - self._crush_threshold) * 8))
                    await ctx.broadcast(
                        f"crush:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
                    )
                    await ctx.broadcast(
                        f"casualty:{self._zone_id}:{casualties}".encode()
                    )
                    # Panic overflow: push 20% of crowd into each adjacent zone
                    overflow = max(100, self._count // 5)
                    for adj_zone, adj_aid in self._adjacent.items():
                        await ctx.send(
                            adj_aid,
                            f"panic_overflow:{self._zone_id}:{adj_zone}:{overflow}".encode(),
                        )

        elif msg.startswith("panic_overflow:"):
            parts = msg.split(":")
            if len(parts) >= 4 and parts[2] == self._zone_id:
                overflow = int(parts[3])
                self._count += overflow
                self._recompute_density()
                await ctx.broadcast(
                    f"density:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
                )
                if self._density > self._crush_threshold and not self._crush:
                    self._crush = True
                    casualties = max(1, int((self._density - self._crush_threshold) * 8))
                    await ctx.broadcast(
                        f"crush:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
                    )
                    await ctx.broadcast(f"casualty:{self._zone_id}:{casualties}".encode())

        elif msg.startswith("departure:"):
            parts = msg.split(":")
            if len(parts) >= 3 and parts[1] == self._zone_id:
                wid = parts[3] if len(parts) >= 4 else None
                if wid and wid in self._processed_wids:
                    return
                if wid:
                    self._processed_wids.add(wid)
                delta = int(parts[2])
                self._count = max(0, self._count - delta)
                self._recompute_density()
                await ctx.broadcast(
                    f"density:{self._zone_id}:{self._density:.2f}:{self._count}".encode()
                )

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
            # For stampede dispatches, broadcast en_route so all agents know
            if "stampede" in msg:
                parts = msg.split(":")
                zone = parts[2] if len(parts) >= 3 else "unknown"
                await ctx.broadcast(
                    f"en_route:{self._id}:{zone}:{self._city}".encode()
                )


class PilgrimAgent(StateMachineAgent):
    """Pilgrim agent: sends SOS if in a crowded zone.

    Example::

        agent = PilgrimAgent(AgentId("pilgrim-agent-0"), zone_id="ramkund_main")
    """

    def __init__(self, agent_id: AgentId, zone_id: str = "ramkund_main", family_id: str = "family-0") -> None:
        self._id = agent_id
        self._zone_id = zone_id
        self._family_id = family_id
        self._sos_sent = False
        self._injured = False
        self._lost = False

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
        if msg.startswith("density:") and not self._sos_sent:
            parts = msg.split(":")
            if len(parts) >= 4 and parts[1] == self._zone_id:
                density = float(parts[2])
                # SOS when density is critically high
                if density > 7.0:
                    self._sos_sent = True
                    await ctx.broadcast(f"sos:{self._id}:{self._zone_id}:{density:.2f}".encode())
        if msg.startswith("close:") and not self._sos_sent:
            parts = msg.split(":")
            if len(parts) >= 2 and parts[1] == self._zone_id:
                self._sos_sent = True
                await ctx.broadcast(f"sos:{self._id}:{self._zone_id}:closed".encode())

        if msg.startswith("crush:") and not self._injured:
            parts = msg.split(":")
            if len(parts) >= 3 and parts[1] == self._zone_id:
                density = float(parts[2])
                # Injury probability scales with density above crush threshold
                injury_prob = min(0.9, (density - 8.5) / 3.0)
                if ctx.rng.random() < injury_prob:
                    self._injured = True
                    severity = "critical" if density > 10.5 else "moderate"
                    await ctx.broadcast(
                        f"injured:{self._id}:{self._zone_id}:{severity}:{density:.1f}".encode()
                    )
                # Separation probability: 30% chance of getting lost from family
                if ctx.rng.random() < 0.30 and not self._lost:
                    self._lost = True
                    await ctx.broadcast(
                        f"lost:{self._id}:{self._family_id}:{self._zone_id}".encode()
                    )


class CommandBridgeAgent(StateMachineAgent):
    """Central command: aggregates alerts, issues closures.

    Example::

        agent = CommandBridgeAgent(AgentId("command-bridge-0"))
    """

    def __init__(self, agent_id: AgentId, ambulance_ids: list[AgentId] | None = None) -> None:
        self._id = agent_id
        self._alerts: list[str] = []
        self._closures: list[str] = []
        self._density_log: dict[str, list[tuple[float, float, int]]] = {}
        self._stampede_zones: set[str] = set()
        # Pre-wired ambulance IDs for direct dispatch (no registry lookup needed)
        self._ambulance_ids: list[AgentId] = ambulance_ids or []

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
                zone_id = parts[1]
                density = float(parts[2])
                count = int(parts[3])
                log = self._density_log.setdefault(zone_id, [])
                log.append((ctx.time, density, count))
                if density > 6.5 or (zone_id == "kushavart_kund" and count > 1900):
                    alert = f"ALERT:{zone_id}:density={density:.2f}:count={count}:t={ctx.time:.0f}"
                    self._alerts.append(alert)
                    await ctx.broadcast(f"alert:{zone_id}:{density:.2f}:{count}:{ctx.time:.0f}".encode())
                    if zone_id not in self._closures:
                        await ctx.broadcast(f"close:{zone_id}:command".encode())
                        self._closures.append(zone_id)
                # Crush density (>8.5) — trigger full stampede response even if
                # the explicit crush: message was dropped by the network
                if density > 8.5 and zone_id not in self._stampede_zones:
                    self._stampede_zones.add(zone_id)
                    await ctx.broadcast(f"stampede_alert:{zone_id}:{ctx.time:.0f}".encode())
                    await ctx.broadcast(f"crowd_control:{zone_id}:disperse".encode())
                    for amb_id in self._ambulance_ids:
                        await ctx.send(
                            amb_id,
                            f"dispatch:stampede:{zone_id}:{ctx.time:.0f}".encode(),
                        )

        elif msg.startswith("sos:"):
            self._alerts.append(f"SOS@t={ctx.time:.0f}:{msg}")
            await ctx.broadcast(f"alert:sos:{msg}:{ctx.time:.0f}".encode())
            registry = ctx.plugins.get("registry")
            if registry is not None:
                ambulances = await registry.lookup(Query(capabilities=["ambulance:dispatch"]))
                if ambulances:
                    await ctx.send(ambulances[0].agent_id, f"dispatch:{msg}".encode())

        elif msg.startswith("crush:") or msg.startswith("stampede_alert:"):
            parts = msg.split(":")
            zone = parts[1] if len(parts) >= 2 else "unknown"
            if zone not in self._stampede_zones:
                self._stampede_zones.add(zone)
                await ctx.broadcast(
                    f"stampede_alert:{zone}:{ctx.time:.0f}".encode()
                )
                await ctx.broadcast(f"crowd_control:{zone}:disperse".encode())
                # Dispatch ALL ambulances to stampede zone
                for amb_id in self._ambulance_ids:
                    await ctx.send(
                        amb_id,
                        f"dispatch:stampede:{zone}:{ctx.time:.0f}".encode(),
                    )


class FloodWatchAgent(StateMachineAgent):
    """Monitors Godavari water level; issues flood alert when threshold crossed.

    Water level rises gradually per tick; broadcasts periodic level updates
    so correlated data shows the rising flood timeline.

    Example::

        agent = FloodWatchAgent(AgentId("flood-watch-agent-0"), level_cm=850, threshold_cm=900)
    """

    _FLOOD_TICK = b"__flood_tick__"
    _FLOOD_TICK_INTERVAL = 10

    def __init__(
        self,
        agent_id: AgentId,
        level_cm: int = 850,
        threshold_cm: int = 900,
        rise_per_tick: float = 0.0,
    ) -> None:
        self._id = agent_id
        self._level_cm = float(level_cm)
        self._threshold_cm = threshold_cm
        self._rise_per_tick = rise_per_tick
        self._alerted = False

    async def on_start(self, ctx: AgentContext) -> None:
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(AgentCard(
                agent_id=self._id,
                name="FloodWatch",
                capabilities=["flood:monitor"],
                metadata={"level_cm": str(int(self._level_cm))},
            ))
        await ctx.broadcast(f"water_level:godavari:{self._level_cm:.0f}".encode())
        if self._level_cm >= self._threshold_cm and not self._alerted:
            self._alerted = True
            await ctx.broadcast(f"flood_alert:godavari:{self._level_cm:.0f}".encode())
        if self._rise_per_tick > 0:
            await ctx.schedule(self._FLOOD_TICK_INTERVAL, self._FLOOD_TICK)

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if payload == self._FLOOD_TICK:
            self._level_cm += self._rise_per_tick * self._FLOOD_TICK_INTERVAL
            await ctx.broadcast(f"water_level:godavari:{self._level_cm:.0f}".encode())
            if self._level_cm >= self._threshold_cm and not self._alerted:
                self._alerted = True
                await ctx.broadcast(f"flood_alert:godavari:{self._level_cm:.0f}".encode())
            else:
                await ctx.schedule(self._FLOOD_TICK_INTERVAL, self._FLOOD_TICK)
        elif msg.startswith("water_level_update:"):
            # Driven by SimDriverAgent — not subject to Byzantine self-tick corruption
            parts = msg.split(":")
            if len(parts) >= 3:
                self._level_cm = float(parts[2])
                await ctx.broadcast(f"water_level:godavari:{self._level_cm:.0f}".encode())
                if self._level_cm >= self._threshold_cm and not self._alerted:
                    self._alerted = True
                    await ctx.broadcast(f"flood_alert:godavari:{self._level_cm:.0f}".encode())


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
                if zone not in self._evacuations:
                    self._evacuations.append(zone)
                    await ctx.broadcast(f"close:{zone}:flood".encode())
                    await ctx.broadcast(f"evacuation:{zone}:{ctx.time:.0f}".encode())
        elif msg.startswith("density:"):
            parts = msg.split(":")
            if len(parts) >= 4 and float(parts[2]) > 6.5:
                zone_id = parts[1]
                if zone_id not in self._evacuations:
                    self._evacuations.append(zone_id)
                    await ctx.broadcast(f"close:{zone_id}:surge".encode())


class LostAndFoundAgent(StateMachineAgent):
    """Collects lost-pilgrim signals and broadcasts reunification when a match is found.

    Lost pilgrims broadcast ``lost:<pilgrim_id>:<family_id>:<zone_id>``.
    When rescuers find someone they broadcast ``found:<pilgrim_id>:<zone_id>``.
    LostAndFound matches them and broadcasts ``reunited:``.

    Example::

        agent = LostAndFoundAgent(AgentId("lost-and-found-0"))
    """

    def __init__(self, agent_id: AgentId) -> None:
        self._id = agent_id
        # pilgrim_id → {family_id, zone_id, t}
        self._lost: dict[str, dict[str, Any]] = {}
        # family_id → [pilgrim_id, ...]
        self._family_index: dict[str, list[str]] = {}
        self._reunited: set[str] = set()

    async def on_start(self, ctx: AgentContext) -> None:
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(AgentCard(
                agent_id=self._id,
                name="LostAndFound",
                capabilities=["lost:register", "lost:search"],
                metadata={"role": "lost_and_found"},
            ))

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("lost:"):
            parts = msg.split(":")
            if len(parts) >= 4:
                pilgrim_id, family_id, zone_id = parts[1], parts[2], parts[3]
                if pilgrim_id not in self._lost:
                    self._lost[pilgrim_id] = {
                        "family_id": family_id, "zone_id": zone_id, "t": ctx.time
                    }
                    self._family_index.setdefault(family_id, []).append(pilgrim_id)
                    await ctx.broadcast(
                        f"lost_registered:{pilgrim_id}:{family_id}:{zone_id}".encode()
                    )
                    # Check if other family members are already lost — report group
                    group = self._family_index.get(family_id, [])
                    if len(group) > 1:
                        await ctx.broadcast(
                            f"family_separated:{family_id}:{len(group)}:{zone_id}".encode()
                        )

        elif msg.startswith("found:"):
            parts = msg.split(":")
            if len(parts) >= 3:
                pilgrim_id, zone_id = parts[1], parts[2]
                if pilgrim_id in self._lost and pilgrim_id not in self._reunited:
                    self._reunited.add(pilgrim_id)
                    family_id = self._lost[pilgrim_id]["family_id"]
                    await ctx.broadcast(
                        f"reunited:{pilgrim_id}:{family_id}:{zone_id}:{ctx.time:.0f}".encode()
                    )


class HospitalAgent(StateMachineAgent):
    """Receives casualty and injury reports; tracks capacity; alerts on overflow.

    Example::

        agent = HospitalAgent(AgentId("hospital-0"), capacity=150, name="Civil Hospital Nashik")
    """

    def __init__(self, agent_id: AgentId, capacity: int = 150, name: str = "Civil Hospital") -> None:
        self._id = agent_id
        self._capacity = capacity
        self._name = name
        self._admitted = 0
        self._rejected = 0

    async def on_start(self, ctx: AgentContext) -> None:
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(AgentCard(
                agent_id=self._id,
                name=self._name,
                capabilities=["hospital:admit", "hospital:status"],
                metadata={"capacity": str(self._capacity), "role": "hospital"},
            ))
        await ctx.broadcast(
            f"hospital_ready:{self._id}:{self._capacity}".encode()
        )

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("casualty:") or msg.startswith("injured:"):
            parts = msg.split(":")
            zone = parts[1] if len(parts) >= 2 else "unknown"
            # Estimate admission need
            count = int(parts[2]) if msg.startswith("casualty:") and len(parts) >= 3 else 1
            available = self._capacity - self._admitted
            accepting = min(count, available)
            overflow = count - accepting
            self._admitted += accepting
            self._rejected += overflow
            if accepting > 0:
                await ctx.broadcast(
                    f"hospital_accepting:{self._id}:{accepting}:{zone}:{self._admitted}/{self._capacity}".encode()
                )
            if overflow > 0 or self._admitted >= self._capacity:
                await ctx.broadcast(
                    f"hospital_overflow:{self._id}:{self._admitted}:{overflow}".encode()
                )

        elif msg.startswith("en_route:"):
            # Ambulance en route — acknowledge
            parts = msg.split(":")
            ambulance_id = parts[1] if len(parts) >= 2 else "unknown"
            await ctx.send(
                sender,
                f"hospital_directions:{self._id}:{self._admitted}/{self._capacity}".encode(),
            )


class CrowdControlAgent(StateMachineAgent):
    """Police crowd control: receives stampede alerts, issues dispersal and cordon orders.

    Coordinates with ambulances, directs pilgrim flow away from crush zones.

    Example::

        agent = CrowdControlAgent(AgentId("crowd-control-0"), sector="nashik-riverside")
    """

    def __init__(self, agent_id: AgentId, sector: str = "nashik") -> None:
        self._id = agent_id
        self._sector = sector
        self._cordoned: set[str] = set()

    async def on_start(self, ctx: AgentContext) -> None:
        registry = ctx.plugins.get("registry")
        if registry:
            await registry.register(AgentCard(
                agent_id=self._id,
                name=f"CrowdControl-{self._sector}",
                capabilities=["crowd:cordon", "crowd:disperse"],
                metadata={"sector": self._sector, "role": "crowd_control"},
            ))

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        msg = payload.decode("utf-8", errors="replace")
        if msg.startswith("stampede_alert:") or msg.startswith("crush:"):
            parts = msg.split(":")
            zone = parts[1] if len(parts) >= 2 else "unknown"
            if zone not in self._cordoned:
                self._cordoned.add(zone)
                await ctx.broadcast(f"cordon:{zone}:{self._sector}:{ctx.time:.0f}".encode())
                await ctx.broadcast(f"disperse:{zone}:all_exits:{ctx.time:.0f}".encode())

        elif msg.startswith("crowd_control:"):
            parts = msg.split(":")
            zone = parts[1] if len(parts) >= 2 else "unknown"
            action = parts[2] if len(parts) >= 3 else "hold"
            await ctx.broadcast(f"police_action:{zone}:{action}:{ctx.time:.0f}".encode())


class SimDriverAgent(StateMachineAgent):
    """Injects arrival/departure waves into the simulation at configured ticks.

    Uses direct ``ctx.send()`` to the specific zone agent (bypassing partition
    for the simulation driver itself) so crowd dynamics are delivered reliably
    even under high message-drop rates.  Zone agents still receive 30% drop on
    their subsequent *broadcasts* to peers, preserving the adversarial fidelity.

    Example::

        agent = SimDriverAgent(AgentId("sim-driver-0"),
                               waves=[{"tick": 60, "zone": "ramkund_main", "count": 2000}],
                               zone_agents={"ramkund_main": AgentId("zone-agent-1")})
    """

    def __init__(
        self,
        agent_id: AgentId,
        arrival_waves: list[dict[str, Any]],
        departure_waves: list[dict[str, Any]] | None = None,
        zone_agents: dict[str, AgentId] | None = None,
        flood_watch_id: AgentId | None = None,
        flood_waves: list[dict[str, Any]] | None = None,
    ) -> None:
        self._id = agent_id
        self._arrival_waves = sorted(arrival_waves, key=lambda w: w.get("tick", 0))
        self._departure_waves = sorted(departure_waves or [], key=lambda w: w.get("tick", 0))
        self._zone_agents: dict[str, AgentId] = zone_agents or {}
        self._flood_watch_id = flood_watch_id
        self._flood_waves = sorted(flood_waves or [], key=lambda w: w.get("tick", 0))

    async def on_start(self, ctx: AgentContext) -> None:
        # Each wave is scheduled 3x (at tick, tick+0.5, tick+1.0) so that
        # 40% random message drop (which applies to ctx.schedule self-messages)
        # is unlikely to kill all copies: P(all 3 dropped) = 0.064 per wave.
        # Arrival/departure waves include a wave-id so ZoneAgent deduplicates.
        all_waves: list[tuple[float, bytes, AgentId | None]] = []
        for wid, wave in enumerate(self._arrival_waves):
            tick = float(wave.get("tick", 0))
            zone = wave["zone"]
            count = int(wave.get("count", 0))
            target = self._zone_agents.get(zone)
            msg = f"arrival:{zone}:{count}:w{wid}".encode()
            all_waves.append((tick, msg, target))
            all_waves.append((tick + 0.5, msg, target))
            all_waves.append((tick + 1.0, msg, target))
        offset = len(self._arrival_waves)
        for wid, wave in enumerate(self._departure_waves):
            tick = float(wave.get("tick", 0))
            zone = wave["zone"]
            count = int(wave.get("count", 0))
            target = self._zone_agents.get(zone)
            msg = f"departure:{zone}:{count}:w{offset + wid}".encode()
            all_waves.append((tick, msg, target))
            all_waves.append((tick + 0.5, msg, target))
            all_waves.append((tick + 1.0, msg, target))
        for wave in self._flood_waves:
            tick = float(wave.get("tick", 0))
            level = int(wave.get("level_cm", 0))
            # Flood level updates are idempotent (no dedup needed)
            flood_msg = f"water_level_update:godavari:{level}".encode()
            for offset_f in (0.0, 0.5, 1.0):
                all_waves.append((tick + offset_f, flood_msg, self._flood_watch_id))

        for idx, (tick, payload, target) in enumerate(all_waves):
            delay = max(0.0, tick - ctx.time)
            await ctx.schedule(delay, f"wave:{idx}".encode())
        self._scheduled_waves = all_waves

    async def on_message(self, ctx: AgentContext, sender: AgentId, payload: bytes) -> None:
        if payload.startswith(b"wave:"):
            try:
                idx = int(payload[5:])
                _tick, inner, target = self._scheduled_waves[idx]
            except (ValueError, IndexError):
                return
            if target:
                await ctx.send(target, inner)
            else:
                await ctx.broadcast(inner)


# ---------------------------------------------------------------------------
# Scenario factories
# ---------------------------------------------------------------------------


def _build_zone_agents(
    config: ScenarioConfig,
) -> tuple[dict[AgentId, Any], dict[str, AgentId]]:
    """Build zone agents from scenario config zones.

    Returns agents dict and zone_id→AgentId mapping for the SimDriver.
    """
    task_cfg = config.task.config or {}
    zones: list[dict[str, Any]] = task_cfg.get("zones", [])

    agents: dict[AgentId, Any] = {}
    zone_map: dict[str, AgentId] = {}
    for zone_idx, z in enumerate(zones):
        aid = AgentId(f"zone-agent-{zone_idx}")
        agents[aid] = ZoneAgent(
            agent_id=aid,
            zone_id=z["id"],
            capacity=z.get("capacity", 5000),
            density=0.0,
            count=z.get("initial_count", 0),
            hard_cap=z.get("hard_cap", False),
            city=z.get("city", "nashik"),
        )
        zone_map[z["id"]] = aid

    return agents, zone_map


def kumbh_peak_bathing_factory(
    config: ScenarioConfig, plugins: dict[str, Any]
) -> dict[AgentId, Any]:
    """Build peak-bathing-day fleet with a simulation driver for crowd dynamics.

    Roles: 12 zone agents, 5 ambulance agents, 100 pilgrim agents,
    1 command bridge, 1 simulation driver.

    Example::

        agents = kumbh_peak_bathing_factory(config, plugins)
    """
    agents: dict[AgentId, Any] = {}
    task_cfg = config.task.config or {}

    # Zone agents
    zone_agents, zone_map = _build_zone_agents(config)
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
    zones = task_cfg.get("zones", [])
    for i in range(n_pilgrims):
        aid = AgentId(f"pilgrim-agent-{i}")
        zone_id = zones[i % len(zones)]["id"] if zones else "ramkund_main"
        agents[aid] = PilgrimAgent(aid, zone_id=zone_id)

    # Command bridge
    agents[AgentId("command-bridge-0")] = CommandBridgeAgent(AgentId("command-bridge-0"))

    # Simulation driver: injects arrival/departure waves over time
    arrival_waves: list[dict[str, Any]] = task_cfg.get("arrival_waves", [])
    departure_waves: list[dict[str, Any]] = task_cfg.get("departure_waves", [])
    drv_id = AgentId("sim-driver-0")
    agents[drv_id] = SimDriverAgent(drv_id, arrival_waves, departure_waves, zone_map)

    # Inject per-agent KumbhZoneGossipRegistry instances
    agent_plugins: dict[AgentId, dict[str, Any]] = {
        aid: {"registry": KumbhZoneGossipRegistry(aid)} for aid in agents
    }
    plugins["_agent_plugins"] = agent_plugins

    return agents


def kumbh_flood_surge_factory(
    config: ScenarioConfig, plugins: dict[str, Any]
) -> dict[AgentId, Any]:
    """Build flood-surge fleet with rising water level and crowd dynamics.

    Roles: 12 zone agents, 5 ambulances, FloodWatch (rising level),
    CrowdSentinel, MedEvac, CommandBridge, NDRF, 3 pilgrims, SimDriver.

    Example::

        agents = kumbh_flood_surge_factory(config, plugins)
    """
    agents: dict[AgentId, Any] = {}
    task_cfg = config.task.config or {}

    surge_zone = task_cfg.get("surge_zone", "ramkund_main")
    surge_count = task_cfg.get("surge_count", 7500)

    zone_names = [
        "ramkund_main", "ramkund_west", "godavari_ghat_1", "godavari_ghat_2",
        "panchavati_main", "tapovan_ghat", "dudhsagar_ghat", "saraswati_kund",
        "kushavart_kund", "kushavart_approach", "trimbakeshwar_main", "brahmagiri_ghat",
    ]
    zone_map: dict[str, AgentId] = {}
    for i, zone_id in enumerate(zone_names):
        aid = AgentId(f"zone-agent-{i}")
        zone_map[zone_id] = aid
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

    # FloodWatch with gradual water level rise
    agents[AgentId("flood-watch-agent-0")] = FloodWatchAgent(
        AgentId("flood-watch-agent-0"),
        level_cm=task_cfg.get("godavari_level_cm", 820),
        threshold_cm=900,
        rise_per_tick=task_cfg.get("godavari_rise_per_tick", 1.0),
    )
    agents[AgentId("crowd-sentinel-agent-0")] = CommandBridgeAgent(
        AgentId("crowd-sentinel-agent-0")
    )
    agents[AgentId("med-evac-agent-0")] = AmbulanceAgent(
        AgentId("med-evac-agent-0"), city="nashik"
    )
    agents[AgentId("command-bridge-0")] = CommandBridgeAgent(AgentId("command-bridge-0"))
    agents[AgentId("ndrf-agent-0")] = NDRFAgent(AgentId("ndrf-agent-0"))

    # 3 pilgrims in the surge zone
    for i in range(3):
        aid = AgentId(f"pilgrim-agent-{i}")
        agents[aid] = PilgrimAgent(aid, zone_id=surge_zone)

    # Simulation driver: drives arrival/departure waves AND flood level updates
    arrival_waves: list[dict[str, Any]] = task_cfg.get("arrival_waves", [])
    departure_waves: list[dict[str, Any]] = task_cfg.get("departure_waves", [])
    flood_waves: list[dict[str, Any]] = task_cfg.get("flood_waves", [])
    flood_watch_id = AgentId("flood-watch-agent-0")
    drv_id = AgentId("sim-driver-0")
    agents[drv_id] = SimDriverAgent(
        drv_id, arrival_waves, departure_waves, zone_map,
        flood_watch_id=flood_watch_id, flood_waves=flood_waves,
    )

    # Inject per-agent KumbhZoneGossipRegistry instances
    agent_plugins: dict[AgentId, dict[str, Any]] = {
        aid: {"registry": KumbhZoneGossipRegistry(aid)} for aid in agents
    }
    plugins["_agent_plugins"] = agent_plugins

    return agents


# ---------------------------------------------------------------------------
# Auto-register on import
# ---------------------------------------------------------------------------

def kumbh_stampede_factory(
    config: ScenarioConfig, plugins: dict[str, Any]
) -> dict[AgentId, Any]:
    """Build stampede simulation fleet.

    Roles: 12 zone agents (high initial density), 8 ambulances,
    50 pilgrims (with family groups), LostAndFound, 2 hospitals,
    2 CrowdControl, CommandBridge, NDRF, SimDriver.

    Example::

        agents = kumbh_stampede_factory(config, plugins)
    """
    agents: dict[AgentId, Any] = {}
    task_cfg = config.task.config or {}

    # ── Zone agents ────────────────────────────────────────────────────
    zone_cfgs: list[dict[str, Any]] = task_cfg.get("zones", [])
    zone_map: dict[str, AgentId] = {}
    zone_agents: dict[AgentId, ZoneAgent] = {}
    for i, z in enumerate(zone_cfgs):
        aid = AgentId(f"zone-agent-{i}")
        za = ZoneAgent(
            agent_id=aid,
            zone_id=z["id"],
            capacity=z.get("capacity", 5000),
            count=z.get("initial_count", 0),
            hard_cap=z.get("hard_cap", False),
            city=z.get("city", "nashik"),
        )
        agents[aid] = za
        zone_agents[aid] = za
        zone_map[z["id"]] = aid

    # Wire adjacency so crush zones can push overflow to neighbours
    adjacency: dict[str, list[str]] = task_cfg.get("adjacency", {})
    for zone_id, neighbours in adjacency.items():
        src_aid = zone_map.get(zone_id)
        if src_aid and src_aid in zone_agents:
            zone_agents[src_aid]._adjacent = {
                n: zone_map[n] for n in neighbours if n in zone_map
            }

    # ── Ambulances ─────────────────────────────────────────────────────
    n_ambulances: int = next(
        (r.count for r in config.agents.roles if r.name == "ambulance_agent"), 8
    )
    ambulance_ids: list[AgentId] = []
    for i in range(n_ambulances):
        aid = AgentId(f"ambulance-agent-{i}")
        agents[aid] = AmbulanceAgent(aid, city="nashik" if i < 5 else "trimbakeshwar")
        ambulance_ids.append(aid)

    # ── Pilgrims in family groups ───────────────────────────────────────
    n_pilgrims: int = next(
        (r.count for r in config.agents.roles if r.name == "pilgrim_agent"), 50
    )
    zones_list = [z["id"] for z in zone_cfgs]
    # Distribute pilgrims with family groups of 3
    for i in range(n_pilgrims):
        aid = AgentId(f"pilgrim-agent-{i}")
        zone_id = zones_list[i % len(zones_list)] if zones_list else "ramkund_main"
        family_id = f"family-{i // 3}"
        agents[aid] = PilgrimAgent(aid, zone_id=zone_id, family_id=family_id)

    # ── Lost and Found ─────────────────────────────────────────────────
    lnf_id = AgentId("lost-and-found-0")
    agents[lnf_id] = LostAndFoundAgent(lnf_id)

    # ── Hospitals ──────────────────────────────────────────────────────
    hospitals: list[dict[str, Any]] = task_cfg.get("hospitals", [
        {"name": "Civil Hospital Nashik", "capacity": 150},
        {"name": "Wockhardt Hospital",    "capacity": 80},
    ])
    for i, h in enumerate(hospitals):
        hid = AgentId(f"hospital-agent-{i}")
        agents[hid] = HospitalAgent(hid, capacity=h.get("capacity", 100), name=h.get("name", f"Hospital-{i}"))

    # ── Crowd Control (police) ──────────────────────────────────────────
    for i, sector in enumerate(["nashik-riverside", "nashik-inner"]):
        cid = AgentId(f"crowd-control-{i}")
        agents[cid] = CrowdControlAgent(cid, sector=sector)

    # ── Command Bridge — pre-wired with ambulance IDs for direct dispatch ─
    agents[AgentId("command-bridge-0")] = CommandBridgeAgent(
        AgentId("command-bridge-0"), ambulance_ids=ambulance_ids
    )

    # ── NDRF ──────────────────────────────────────────────────────────
    agents[AgentId("ndrf-agent-0")] = NDRFAgent(AgentId("ndrf-agent-0"))

    # ── Simulation driver ──────────────────────────────────────────────
    arrival_waves: list[dict[str, Any]] = task_cfg.get("arrival_waves", [])
    departure_waves: list[dict[str, Any]] = task_cfg.get("departure_waves", [])
    drv_id = AgentId("sim-driver-0")
    agents[drv_id] = SimDriverAgent(drv_id, arrival_waves, departure_waves, zone_map)

    # ── Per-agent registry instances ────────────────────────────────────
    agent_plugins: dict[AgentId, dict[str, Any]] = {
        aid: {"registry": KumbhZoneGossipRegistry(aid)} for aid in agents
    }
    plugins["_agent_plugins"] = agent_plugins

    return agents


register_scenario("kumbh_peak_bathing", kumbh_peak_bathing_factory)
register_scenario("kumbh_flood_surge", kumbh_flood_surge_factory)
register_scenario("kumbh_stampede", kumbh_stampede_factory)
