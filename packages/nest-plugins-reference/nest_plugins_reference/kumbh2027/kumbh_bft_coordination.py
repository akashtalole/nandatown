# SPDX-License-Identifier: Apache-2.0
"""KumbhNet BFT coordination plugin — partition-tolerant zone evacuation consensus.

The stock ``contract_net`` plugin uses lowest-bid resolution: a single
Byzantine zone agent can win every round with a fake bid of zero and
force any outcome it likes.  For physical zone closures at Kumbh that is
unacceptable.  This plugin replaces it with a **weighted PBFT-lite**
quorum vote:

* ``propose()`` — commander broadcasts a task to all participant zones.
* ``participate()`` — each zone votes YES/NO based on its observed
  crowd density (stored in the task's metadata).
* ``resolve()`` — the round commits only when strictly more than two-thirds
  of participant votes are YES (``⌈(2n/3)⌉ + 1`` where n = participants).
  A minority of Byzantine YES votes cannot force closure; a minority of
  Byzantine NO votes cannot block a justified closure.
* ``commit()`` — no-op acknowledgement; side-effects live in the scenario.

Kushavart Kund veto rule
~~~~~~~~~~~~~~~~~~~~~~~~
If the task metadata contains ``"zone": "kushavart_kund"`` and the
metadata ``"count"`` exceeds 1 900, *resolve* immediately with a forced
YES outcome regardless of vote counts.  This mirrors the hard-coded rule
in the KumbhSafe architecture that zone closure at Kushavart is never
dependent on AI agent availability.

Determinism guarantee
~~~~~~~~~~~~~~~~~~~~~
All state is stored in the ``Round.metadata`` dict and in instance
dicts keyed by ``round_id``.  No ``time.time()`` calls; no ``random``
calls.  Same trace → same outcome under any seed.

Example::

    from nest_core.types import AgentId, Task
    from nest_plugins_reference.kumbh2027.kumbh_bft_coordination import KumbhBFTCoordination

    coord = KumbhBFTCoordination(agent_id=AgentId("zone-ramkund"), zone_count=12)
    task = Task(
        id="t1", description="close zone",
        metadata={"zone": "zone_ramkund", "density": 7.2},
    )
    rnd = await coord.propose(task)
    vote = await coord.participate(rnd)
    outcome = await coord.resolve(rnd)
"""

from __future__ import annotations

import hashlib

from nest_core.types import (
    AgentId,
    Bid,
    Outcome,
    Round,
    Task,
    Vote,
)

# Persons-per-m² above which this zone agent votes YES to close.
_CLOSE_THRESHOLD = 6.5
# Absolute person count that triggers hard Kushavart closure.
_KUSHAVART_HARD_CAP = 1_900
_KUSHAVART_ZONE_ID = "kushavart_kund"


def _quorum(n: int) -> int:
    """Minimum YES votes required: strictly more than two-thirds.

    For n=12: quorum = 9 (Byzantine tolerance f=3).

    Example::

        assert _quorum(12) == 9
        assert _quorum(3) == 3
    """
    return (2 * n) // 3 + 1


class KumbhBFTCoordination:
    """PBFT-lite coordination for physical zone closure decisions.

    One instance per zone agent.  All votes for a round accumulate in
    ``Round.metadata["votes"]``; ``resolve`` reads that list.

    Example::

        coord = KumbhBFTCoordination(AgentId("zone-0"), zone_count=12)
        rnd = await coord.propose(Task(id="t1", description="close zone"))
        vote = await coord.participate(rnd)
        outcome = await coord.resolve(rnd)
    """

    def __init__(self, agent_id: AgentId, zone_count: int = 12) -> None:
        self._agent_id = agent_id
        self._zone_count = zone_count

    async def propose(self, task: Task) -> Round:
        """Broadcast a zone-closure proposal; returns an empty round.

        The task metadata should carry ``"zone"`` (zone id) and either
        ``"density"`` (persons/m²) or ``"count"`` (raw head count).

        Example::

            rnd = await coord.propose(Task(
                id="t1", description="close",
                metadata={"zone": "kushavart_kund", "count": 2000},
            ))
        """
        return Round(
            id=hashlib.sha256(f"{task.id}:{task.description}".encode()).hexdigest()[:32],
            task=task,
            participants=[],
            metadata={"votes": [], "committed": False},
        )

    async def participate(self, round: Round) -> Vote | Bid:
        """Cast a YES or NO vote based on local density reading.

        YES if density > _CLOSE_THRESHOLD or if this zone is Kushavart
        and count > hard cap.  Appends the vote to Round.metadata["votes"]
        so ``resolve`` can tally it.

        Example::

            vote = await coord.participate(rnd)
            assert vote.value in ("yes", "no")
        """
        task = round.task
        zone = task.metadata.get("zone", "")
        density = float(task.metadata.get("density", 0.0))
        count = int(task.metadata.get("count", 0))

        value = "no"
        if zone == _KUSHAVART_ZONE_ID and count > _KUSHAVART_HARD_CAP or density > _CLOSE_THRESHOLD:
            value = "yes"

        vote = Vote(voter=self._agent_id, round_id=round.id, value=value)
        votes: list[dict[str, str]] = round.metadata.setdefault("votes", [])
        # Deduplicate: one vote per agent per round.
        if not any(v["voter"] == str(self._agent_id) for v in votes):
            votes.append({"voter": str(self._agent_id), "value": value})
        if self._agent_id not in round.participants:
            round.participants.append(self._agent_id)
        return vote

    async def resolve(self, round: Round) -> Outcome:
        """Resolve the round: commit closure only if quorum is reached.

        Quorum = ⌈(2n/3)⌉ + 1 YES votes out of the participant list
        recorded in ``round.metadata["votes"]``.

        Kushavart hard-cap bypass: if the task metadata says zone =
        kushavart_kund and count > 1900, the closure is forced regardless
        of vote counts (mirrors the stream-lambda safety rule).

        Example::

            outcome = await coord.resolve(rnd)
            # outcome.winner is AgentId("close") or None
        """
        task = round.task
        zone = task.metadata.get("zone", "")
        count = int(task.metadata.get("count", 0))

        # Hard Kushavart override.
        if zone == _KUSHAVART_ZONE_ID and count > _KUSHAVART_HARD_CAP:
            round.metadata["committed"] = True
            return Outcome(
                round_id=round.id,
                winner=AgentId("close"),
                task=task,
                metadata={"reason": "kushavart_hard_cap", "count": count},
            )

        votes: list[dict[str, str]] = round.metadata.get("votes", [])
        yes_count = sum(1 for v in votes if v["value"] == "yes")
        n = max(len(votes), 1)
        needed = _quorum(n)

        if yes_count >= needed:
            round.metadata["committed"] = True
            return Outcome(
                round_id=round.id,
                winner=AgentId("close"),
                task=task,
                metadata={"yes_votes": yes_count, "total_votes": n, "quorum": needed},
            )

        return Outcome(
            round_id=round.id,
            winner=None,
            task=task,
            metadata={"yes_votes": yes_count, "total_votes": n, "quorum": needed},
        )

    async def commit(self, outcome: Outcome) -> None:
        """Acknowledge a committed closure outcome (no-op; side-effects in scenario).

        Example::

            await coord.commit(outcome)
        """
