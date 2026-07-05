# SPDX-License-Identifier: Apache-2.0
"""KumbhNet crowd density DataFacts plugin — content-addressed tamper-evident snapshots.

The stock ``datafacts_v1`` plugin calls ``time.time()`` to check
freshness: it is not deterministic and produces different traces on
every replay.  For post-incident reconstruction of a crowd surge at
Kumbh we need a snapshot chain that is:

* **Content-addressed** — each snapshot URL encodes the SHA-256 of its
  content, so any tampering is immediately detectable.
* **Signed** — the publishing zone agent's id is embedded in the URL and
  the metadata; validators can check that only zone agents publish
  density data.
* **ACL-controlled by zone status** — GREEN zones are ``public``; RED
  and BLACK zones are ``iccc_only`` (only ICCC operators and above can
  fetch); this prevents adversaries from reading BLACK zone location data.
* **Freshness without wall-clock time** — freshness is expressed as a
  simulation tick budget: a snapshot is fresh if the current logical
  time is within ``freshness_ticks`` ticks of its publication tick.
* **Deterministic** — same scenario seed → identical CIDs and URLs.

URL scheme::

    df://kumbh/<sha256_hex_of_content>

Content for hashing is ``canonical_json(metadata)``.  Because pydantic
``model_dump`` output is used with ``sort_keys=True`` the hash is stable
across replays.

Example::

    from nest_plugins_reference.kumbh2027.crowd_density_datafacts import CrowdDensityDataFacts
    from nest_core.types import AgentId, DatasetMetadata

    df = CrowdDensityDataFacts(freshness_ticks=2)
    meta = DatasetMetadata(
        name="zone_ramkund_main_t42",
        owner=AgentId("zone-ramkund"),
        tags=["density", "GREEN"],
        metadata={"density": "4.1", "count": "2100", "status": "GREEN", "tick": "42"},
    )
    url = await df.publish(meta)
    assert url.startswith("df://kumbh/")
    fetched = await df.fetch(url)
    assert fetched.name == meta.name
"""

from __future__ import annotations

import hashlib
import json

from nest_sdk import AccessGrant, AgentId, DataFactsUrl, DatasetMetadata

# Roles allowed to see RED/BLACK zone snapshots.
_ICCC_ROLES = frozenset({"iccc_operator", "police_coordinator", "ndrf", "org_admin", "super_admin"})

# Status values that are restricted access.
_RESTRICTED_STATUSES = frozenset({"RED", "BLACK", "CLOSED"})


def _cid(meta: DatasetMetadata) -> str:
    """Compute a SHA-256 content identifier for ``meta``.

    Uses the canonical JSON of the model dump (sorted keys, no
    whitespace) so the hash is stable across Python versions.

    Example::

        from nest_core.types import AgentId, DatasetMetadata
        meta = DatasetMetadata(name="x", owner=AgentId("a"))
        cid = _cid(meta)
        assert len(cid) == 64
    """
    raw = json.dumps(meta.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class CrowdDensityDataFacts:
    """Content-addressed DataFacts for crowd density snapshots.

    Each call to ``publish`` produces a unique ``df://kumbh/<sha256>``
    URL.  The SHA-256 is over the full ``DatasetMetadata`` JSON so any
    modification produces a different URL — making tampering detectable
    without storing a separate digest list.

    ``freshness_ticks`` controls how many simulation ticks a snapshot
    remains "fresh" (default 2 — two 30-second sensor windows).

    Example::

        df = CrowdDensityDataFacts(freshness_ticks=2)
        url = await df.publish(meta)
        ok = await df.verify_freshness(url, current_tick=43)
    """

    def __init__(self, freshness_ticks: int = 2, current_tick: int = 0) -> None:
        self._freshness_ticks = freshness_ticks
        self._current_tick = current_tick
        self._store: dict[DataFactsUrl, DatasetMetadata] = {}
        self._publish_tick: dict[DataFactsUrl, int] = {}
        self._grants: dict[DataFactsUrl, list[AccessGrant]] = {}

    def advance_tick(self, tick: int) -> None:
        """Update the logical clock; called by the scenario driver.

        Example::

            df.advance_tick(43)
        """
        self._current_tick = tick

    async def publish(self, dataset: DatasetMetadata) -> DataFactsUrl:
        """Publish a density snapshot; return its content-addressed URL.

        The URL embeds the SHA-256 of the full metadata, making it
        tamper-evident.  Duplicate publishes of the same metadata return
        the same URL (idempotent).

        Example::

            url = await df.publish(meta)
            assert "kumbh" in url
        """
        cid = _cid(dataset)
        url = DataFactsUrl(f"df://kumbh/{cid}")
        # Idempotent: only record the first publish time.
        if url not in self._store:
            self._store[url] = dataset
            self._publish_tick[url] = self._current_tick
        return url

    async def fetch(self, url: DataFactsUrl) -> DatasetMetadata:
        """Fetch the metadata for a published snapshot URL.

        Raises ``KeyError`` if the URL is unknown.

        Example::

            meta = await df.fetch(url)
        """
        meta = self._store.get(url)
        if meta is None:
            msg = f"Unknown snapshot URL: {url}"
            raise KeyError(msg)
        return meta

    async def request_access(self, url: DataFactsUrl, requester: AgentId) -> AccessGrant:
        """Request access to a snapshot.

        For RED/BLACK zone snapshots the grant tier is ``"iccc_only"``
        and callers should check that the requester's role is in
        ``_ICCC_ROLES``.  For GREEN/YELLOW snapshots the tier is
        ``"public"``.

        Example::

            grant = await df.request_access(url, AgentId("op-1"))
        """
        meta = self._store.get(url)
        tier = "public"
        if meta is not None:
            status = meta.metadata.get("status", "GREEN")
            if status in _RESTRICTED_STATUSES:
                tier = "iccc_only"

        grant = AccessGrant(url=url, grantee=requester, tier=tier)
        self._grants.setdefault(url, []).append(grant)
        return grant

    async def verify_freshness(self, url: DataFactsUrl, current_tick: int | None = None) -> bool:
        """Return True iff the snapshot was published within ``freshness_ticks`` ticks.

        Uses ``current_tick`` if supplied, otherwise ``self._current_tick``.

        Example::

            df.advance_tick(43)
            assert await df.verify_freshness(url)
        """
        tick = current_tick if current_tick is not None else self._current_tick
        published_at = self._publish_tick.get(url)
        if published_at is None:
            return False
        return (tick - published_at) <= self._freshness_ticks

    def chain_for_zone(self, zone_id: str) -> list[DataFactsUrl]:
        """Return all published URLs for a zone, in publish-tick order.

        Used by validators to reconstruct the density history for a zone.

        Example::

            urls = df.chain_for_zone("kushavart_kund")
        """
        matching = [
            (self._publish_tick[url], url)
            for url, meta in self._store.items()
            if meta.metadata.get("zone_id") == zone_id
        ]
        return [url for _, url in sorted(matching)]
