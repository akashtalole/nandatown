# SPDX-License-Identifier: Apache-2.0
"""In-memory transport wired to the simulator's event queue.

Example::

    transport = InMemoryTransport(agent_id, event_queue, clock)
    await transport.send(AgentId("a2"), b"hello")
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nest_core.types import AgentId, CorrelationId, TransportCapabilities

if TYPE_CHECKING:
    from nest_core.sim.clock import VirtualClock
    from nest_core.sim.delay_model import DelayModel
    from nest_core.sim.events import EventQueue


class InMemoryTransport:
    """Transport that routes messages through the simulator's event queue.

    When a :class:`~nest_core.sim.delay_model.DelayModel` is attached, each
    send consults the model to compute a per-message delay (and may be
    dropped at the model layer); otherwise messages are delivered at
    ``time = now`` exactly as before — preserving backwards compatibility
    with every existing scenario.

    Example::

        transport = InMemoryTransport(AgentId("a1"), queue, clock)
        await transport.send(AgentId("a2"), b"data")
    """

    capabilities = TransportCapabilities(
        supports_streaming=False,
        ordered=True,
        reliable=True,
    )

    def __init__(
        self,
        agent_id: AgentId,
        event_queue: EventQueue,
        clock: VirtualClock,
        all_agents: list[AgentId] | None = None,
        delay_model: DelayModel | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._queue = event_queue
        self._clock = clock
        self.all_agents = all_agents or []
        self._delay_model = delay_model

    @property
    def delay_model(self) -> DelayModel | None:
        return self._delay_model

    def set_delay_model(self, model: DelayModel | None) -> None:
        """Attach or detach the delay model.  Used by the simulator at wiring time.

        Example::

            transport.set_delay_model(model)
        """
        self._delay_model = model

    async def send(
        self,
        to: AgentId,
        payload: bytes,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        """Enqueue a message delivery event.

        If a delay model is attached, the model's ``compute_delay`` is
        consulted: ``None`` means the message is dropped at the wire (a
        ``dropped`` trace event is *not* emitted here — that is the
        simulator's job for symmetry with the existing ``message_drop``
        knob).  Otherwise the delivery event is scheduled at
        ``now + delay``.

        Example::

            await transport.send(AgentId("a2"), b"hello")
        """
        from nest_core.sim.events import Event

        delay = 0.0
        if self._delay_model is not None:
            sampled = self._delay_model.compute_delay(self._agent_id, to, len(payload))
            if sampled is None:
                # Surface the drop through the same path as the simulator's
                # message_drop knob so traces stay consistent.  We piggyback
                # on the existing 'deliver' event with a marker and let the
                # simulator turn it into a 'dropped' record at delivery time.
                self._queue.push(
                    Event(
                        time=self._clock.now,
                        kind="netem_drop",
                        agent_id=to,
                        target_id=self._agent_id,
                        payload=payload,
                        correlation_id=correlation_id,
                    )
                )
                return
            delay = sampled

        self._queue.push(
            Event(
                time=self._clock.now + delay,
                kind="deliver",
                agent_id=to,
                target_id=self._agent_id,
                payload=payload,
                correlation_id=correlation_id,
            )
        )

    async def receive(self) -> tuple[AgentId, bytes]:
        """Not used in Tier 1 — the simulator pushes events to agents.

        Example::

            # Not applicable in simulation mode
        """
        raise NotImplementedError("Tier 1 transport is push-based via the event queue")

    async def broadcast(
        self,
        payload: bytes,
        correlation_id: CorrelationId | None = None,
    ) -> None:
        """Broadcast to all known agents.

        Example::

            await transport.broadcast(b"announcement")
        """
        for aid in self.all_agents:
            if aid != self._agent_id:
                await self.send(aid, payload, correlation_id=correlation_id)
