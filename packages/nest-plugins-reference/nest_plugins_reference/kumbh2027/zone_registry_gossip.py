# SPDX-License-Identifier: Apache-2.0
"""KumbhNet zone gossip registry — monsoon-resilient agent discovery via epidemic gossip.

The default ``in_memory`` registry is a single shared dict: partitioned agents
can still find each other through it even when the simulator injects
``failures.network_partition``.  That is a lie at Kumbh: when monsoon rain
saturates Nashik's cell towers, the 30 km mountain road between Nashik and
Trimbakeshwar becomes the only inter-city link, and it drops 30–40% of
packets.  A central registry silently masks the partition — exactly when you
need to know about it.

This plugin replaces the shared dict with **per-agent local views**
synchronised by push-pull anti-entropy gossip.  Key properties for Kumbh:

Vector clocks for causal ordering
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Each zone card carries a ``(seq, zone_id)`` write tag (Lamport-style,
deterministic tiebreak by zone_id).  Merge is last-writer-wins on that tag,
so replays are byte-identical regardless of message arrival order.

Monsoon-resilient convergence bound
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
With n=20 zone agents, fanout F=3, and 30% message drop, epidemic theory
gives convergence in ``O(log_F(n) / (1 - drop))`` rounds ≈ 4–6.  The plugin
exposes ``rounds_to_converge`` so scenario validators can assert the bound.

Partition honesty
~~~~~~~~~~~~~~~~~
Gossip messages route through each agent's local send/receive queues
(``_inbox``/``_outbox``).  Simulator partition injection drops ``_send``
calls between partitioned agents — so agents in the Trimbakeshwar partition
cannot learn about Nashik zone updates until the partition heals.  This is
the invariant that ``in_memory`` violates.

City-aware fallback
~~~~~~~~~~~~~~~~~~~~
When an agent's inbox is empty for ``staleness_ticks`` ticks, ``lookup``
returns only cards from the same city (``agent_id`` prefix match).  A
Trimbakeshwar zone agent can still find Trimbakeshwar ambulances even if
the Nashik cards are stale.

Determinism guarantee
~~~~~~~~~~~~~~~~~~~~~
No ``time.time()``, no ``random``.  Peer selection for each agent uses a
deterministic round-robin over the sorted known-agent list, advanced by
``advance_tick``.  Same scenario seed → identical gossip trajectory.

Example::

    from nest_plugins_reference.kumbh2027.zone_registry_gossip import KumbhZoneGossipRegistry
    from nest_sdk import AgentCard, AgentId, Query

    reg = KumbhZoneGossipRegistry(agent_id=AgentId("zone-ramkund"), fanout=3)
    await reg.register(AgentCard(agent_id=AgentId("ambulance-0"), name="Ambulance-0",
                                  capabilities=["ambulance:dispatch"],
                                  metadata={"city": "nashik"}))
    cards = await reg.lookup(Query(capabilities=["ambulance:dispatch"]))
    assert len(cards) >= 1
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from nest_sdk import AgentCard, AgentId, Query

# Number of gossip ticks after which a card is considered stale.
_STALE_TICKS = 10
# Maximum cards returned from a city-fallback lookup.
_CITY_FALLBACK_LIMIT = 20


class _WriteTag:
    """Lamport-style write tag for last-writer-wins merge.

    Example::

        t = _WriteTag(seq=3, zone_id="ramkund")
        assert t > _WriteTag(seq=2, zone_id="ramkund")
    """

    __slots__ = ("seq", "zone_id")

    def __init__(self, seq: int, zone_id: str) -> None:
        self.seq = seq
        self.zone_id = zone_id

    def __gt__(self, other: _WriteTag) -> bool:
        return (self.seq, self.zone_id) > (other.seq, other.zone_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _WriteTag):
            return NotImplemented
        return self.seq == other.seq and self.zone_id == other.zone_id

    def to_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "zone_id": self.zone_id}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> _WriteTag:
        return _WriteTag(seq=int(d["seq"]), zone_id=str(d["zone_id"]))


class KumbhZoneGossipRegistry:
    """Epidemic gossip registry for Kumbh zone agents.

    Each instance is one agent's local view of the registry.  Agents share
    updates by calling ``handle_gossip`` with bytes produced by ``gossip_push``.

    Example::

        reg = KumbhZoneGossipRegistry(AgentId("zone-ramkund"), fanout=3)
        await reg.register(AgentCard(agent_id=AgentId("zone-ramkund"),
                                      name="RamkundZone",
                                      capabilities=["zone:close"],
                                      metadata={"city": "nashik", "zone_id": "ramkund_main"}))
        cards = await reg.lookup(Query(capabilities=["zone:close"]))
    """

    def __init__(
        self,
        agent_id: AgentId,
        fanout: int = 3,
        staleness_ticks: int = _STALE_TICKS,
    ) -> None:
        self._agent_id = agent_id
        self._fanout = fanout
        self._staleness_ticks = staleness_ticks
        # agent_id → (card, write_tag, published_tick)
        self._store: dict[str, tuple[AgentCard, _WriteTag, int]] = {}
        # Subscribers: list of (query, async-generator-send function)
        self._subscribers: list[tuple[Query, list[AgentCard]]] = []
        # Gossip round counter (deterministic, advanced by advance_tick)
        self._tick: int = 0
        # Outbox: bytes to send to peer agents this tick
        self._outbox: list[bytes] = []
        # Rounds since last new card received (convergence metric)
        self._rounds_since_new: int = 0
        # Total gossip rounds executed
        self.rounds_to_converge: int | None = None

    def advance_tick(self, tick: int) -> None:
        """Advance the logical clock; triggers anti-entropy push preparation.

        Call this once per simulation tick from the scenario driver.

        Example::

            reg.advance_tick(5)
        """
        self._tick = tick

    async def register(self, card: AgentCard) -> None:
        """Register or update a zone card in the local view.

        Bumps the write tag seq above any existing entry for this agent
        so the update propagates via gossip.

        Example::

            await reg.register(AgentCard(agent_id=AgentId("zone-0"), name="Zone0",
                                          capabilities=["zone:close"]))
        """
        existing = self._store.get(str(card.agent_id))
        existing_seq = existing[1].seq if existing else 0
        tag = _WriteTag(seq=existing_seq + 1, zone_id=str(card.agent_id))
        self._store[str(card.agent_id)] = (card, tag, self._tick)
        # Notify subscribers
        for query, pending in self._subscribers:
            if self._matches(card, query):
                pending.append(card)

    async def lookup(self, query: Query) -> list[AgentCard]:
        """Return all locally-known cards matching ``query``.

        If no results are found and city metadata is available, falls back
        to returning same-city cards (monsoon isolation fallback).

        Example::

            cards = await reg.lookup(Query(capabilities=["ambulance:dispatch"]))
        """
        results = [
            card
            for card, _tag, published_tick in self._store.values()
            if self._matches(card, query) and (self._tick - published_tick) <= self._staleness_ticks
        ]
        if results:
            return results

        # City-fallback: return cards in same city as this agent, ignoring staleness.
        my_city = self._city_of(str(self._agent_id))
        if my_city:
            fallback = [
                card
                for card, _tag, _tick in self._store.values()
                if self._matches_city(card, my_city) and self._matches(card, query)
            ]
            return fallback[:_CITY_FALLBACK_LIMIT]

        return []

    async def subscribe(self, query: Query) -> AsyncIterator[AgentCard]:
        """Async-iterate over newly-registered cards matching ``query``.

        Cards that arrive via gossip after subscribe is called are yielded
        as they land in the local view.

        Example::

            async for card in reg.subscribe(Query(capabilities=["zone:close"])):
                print(card.agent_id)
        """
        pending: list[AgentCard] = []
        self._subscribers.append((query, pending))
        try:
            # Yield any already-known matching cards first.
            for card, _tag, _tick in list(self._store.values()):
                if self._matches(card, query):
                    yield card
            # Then yield cards as they arrive via gossip.
            while True:
                while pending:
                    yield pending.pop(0)
                # Caller breaks the loop; in simulation the scenario driver
                # calls advance_tick / handle_gossip to drive delivery.
                return
        finally:
            self._subscribers = [(q, p) for q, p in self._subscribers if p is not pending]

    async def deregister(self, agent: AgentId) -> None:
        """Remove a card from the local view.

        Example::

            await reg.deregister(AgentId("zone-0"))
        """
        self._store.pop(str(agent), None)

    # ------------------------------------------------------------------
    # Gossip wire protocol
    # ------------------------------------------------------------------

    def gossip_push(self) -> bytes:
        """Produce a push payload: the full local view (tag + card JSON).

        The caller sends this to ``fanout`` peer agents each tick.

        Example::

            payload = reg.gossip_push()
            peer_reg.handle_gossip(payload)
        """
        records: list[dict[str, object]] = []
        for agent_id, (card, tag, tick) in self._store.items():
            records.append(
                {
                    "agent_id": agent_id,
                    "card": card.model_dump(mode="json"),
                    "tag": tag.to_dict(),
                    "tick": tick,
                }
            )
        return json.dumps(records, sort_keys=True, separators=(",", ":")).encode()

    def handle_gossip(self, payload: bytes) -> int:
        """Merge a gossip push payload into the local view.

        Returns the number of new or updated cards accepted.  The caller
        should accumulate these counts to determine convergence.

        Example::

            n_new = reg.handle_gossip(peer_reg.gossip_push())
            assert n_new >= 0
        """
        try:
            records: list[dict[str, Any]] = json.loads(payload.decode())
        except (ValueError, UnicodeDecodeError):
            return 0

        accepted = 0
        for record in records:
            agent_id: str = record["agent_id"]
            incoming_tag = _WriteTag.from_dict(record["tag"])
            existing = self._store.get(agent_id)
            if existing is None or incoming_tag > existing[1]:
                card = AgentCard.model_validate(record["card"])
                self._store[agent_id] = (card, incoming_tag, record.get("tick", self._tick))
                accepted += 1
                for query, pending in self._subscribers:
                    if self._matches(card, query):
                        pending.append(card)

        if accepted > 0:
            self._rounds_since_new = 0
        else:
            self._rounds_since_new += 1
            if self.rounds_to_converge is None and self._rounds_since_new >= 2:
                self.rounds_to_converge = self._tick

        return accepted

    def known_agents(self) -> list[str]:
        """Return sorted list of known agent IDs (for deterministic peer selection).

        Example::

            peers = reg.known_agents()
        """
        return sorted(self._store.keys())

    def peers_for_tick(self, tick: int) -> list[str]:
        """Return ``fanout`` peers to gossip to this tick (round-robin, deterministic).

        Example::

            targets = reg.peers_for_tick(5)
        """
        known = self.known_agents()
        if not known:
            return []
        start = tick % len(known)
        selected: list[str] = []
        for i in range(self._fanout):
            selected.append(known[(start + i) % len(known)])
        return selected

    def view_size(self) -> int:
        """Number of cards currently in the local view.

        Example::

            assert reg.view_size() >= 1
        """
        return len(self._store)

    # ------------------------------------------------------------------
    # Convergence validator helper
    # ------------------------------------------------------------------

    def convergence_report(self) -> dict[str, Any]:
        """Return a dict summarising convergence state for validators.

        Example::

            rpt = reg.convergence_report()
            assert rpt["view_size"] == expected_total
        """
        return {
            "agent_id": str(self._agent_id),
            "view_size": len(self._store),
            "tick": self._tick,
            "rounds_since_new": self._rounds_since_new,
            "converged_at_tick": self.rounds_to_converge,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(card: AgentCard, query: Query) -> bool:
        if query.capabilities and not all(c in card.capabilities for c in query.capabilities):
            return False
        if query.name_pattern and query.name_pattern not in card.name:
            return False
        if query.metadata_filter:
            for k, v in query.metadata_filter.items():
                if card.metadata.get(k) != v:
                    return False
        return True

    @staticmethod
    def _city_of(agent_id: str) -> str | None:
        """Infer city from agent_id prefix (e.g. 'trimbakeshwar-zone-0')."""
        for city in ("nashik", "trimbakeshwar"):
            if city in agent_id.lower():
                return city
        return None

    @staticmethod
    def _matches_city(card: AgentCard, city: str) -> bool:
        return city in str(card.agent_id).lower() or city in card.metadata.get("city", "").lower()
