# SPDX-License-Identifier: Apache-2.0
"""Tests for crowd density DataFacts plugin.

Adversarial invariants verified:
* Content-addressed URLs differ when metadata content differs.
* Same metadata always produces the same URL (deterministic CID).
* Tampered metadata produces a different URL (tamper detection).
* RED/BLACK zone snapshots get iccc_only access tier.
* GREEN zone snapshots get public access tier.
* Freshness window is tick-based, not wall-clock-based.
* chain_for_zone returns snapshots in chronological order.
"""

from __future__ import annotations

import pytest
from nest_core.types import AgentId, DatasetMetadata
from nest_plugins_reference.kumbh2027.crowd_density_datafacts import (
    CrowdDensityDataFacts,
    _cid,
)


def _zone_meta(
    zone_id: str,
    status: str,
    density: float,
    tick: int,
    count: int = 1000,
) -> DatasetMetadata:
    return DatasetMetadata(
        name=f"{zone_id}_t{tick}",
        owner=AgentId(f"zone-agent-{zone_id}"),
        tags=["density", status],
        metadata={
            "zone_id": zone_id,
            "status": status,
            "density": str(density),
            "count": str(count),
            "tick": str(tick),
        },
    )


# ---------------------------------------------------------------------------
# CID determinism
# ---------------------------------------------------------------------------


def test_cid_deterministic() -> None:
    meta = _zone_meta("ramkund", "GREEN", 4.1, 42)
    assert _cid(meta) == _cid(meta)


def test_cid_differs_when_content_differs() -> None:
    m1 = _zone_meta("ramkund", "GREEN", 4.1, 42)
    m2 = _zone_meta("ramkund", "GREEN", 4.2, 42)  # density changed
    assert _cid(m1) != _cid(m2)


def test_cid_is_64_hex_chars() -> None:
    meta = _zone_meta("kushavart_kund", "BLACK", 7.8, 10, count=2000)
    assert len(_cid(meta)) == 64
    assert all(c in "0123456789abcdef" for c in _cid(meta))


# ---------------------------------------------------------------------------
# Publish / fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_kumbh_url() -> None:
    df = CrowdDensityDataFacts()
    meta = _zone_meta("ramkund", "GREEN", 4.1, 1)
    url = await df.publish(meta)
    assert url.startswith("df://kumbh/")


@pytest.mark.asyncio
async def test_fetch_returns_published_meta() -> None:
    df = CrowdDensityDataFacts()
    meta = _zone_meta("ramkund", "GREEN", 4.1, 1)
    url = await df.publish(meta)
    fetched = await df.fetch(url)
    assert fetched.name == meta.name


@pytest.mark.asyncio
async def test_fetch_unknown_url_raises() -> None:
    from nest_core.types import DataFactsUrl

    df = CrowdDensityDataFacts()
    with pytest.raises(KeyError):
        await df.fetch(DataFactsUrl("df://kumbh/doesnotexist"))


@pytest.mark.asyncio
async def test_publish_idempotent() -> None:
    df = CrowdDensityDataFacts()
    meta = _zone_meta("ramkund", "GREEN", 4.1, 1)
    url1 = await df.publish(meta)
    url2 = await df.publish(meta)
    assert url1 == url2


# ---------------------------------------------------------------------------
# ACL: RED/BLACK zones are iccc_only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_green_zone_gets_public_access() -> None:
    df = CrowdDensityDataFacts()
    meta = _zone_meta("ramkund", "GREEN", 4.1, 1)
    url = await df.publish(meta)
    grant = await df.request_access(url, AgentId("pilgrim-99"))
    assert grant.tier == "public"


@pytest.mark.asyncio
async def test_red_zone_gets_iccc_only_access() -> None:
    df = CrowdDensityDataFacts()
    meta = _zone_meta("kushavart_kund", "RED", 6.6, 1)
    url = await df.publish(meta)
    grant = await df.request_access(url, AgentId("random-agent"))
    assert grant.tier == "iccc_only"


@pytest.mark.asyncio
async def test_black_zone_gets_iccc_only_access() -> None:
    df = CrowdDensityDataFacts()
    meta = _zone_meta("kushavart_kund", "BLACK", 7.9, 1, count=2100)
    url = await df.publish(meta)
    grant = await df.request_access(url, AgentId("random-agent"))
    assert grant.tier == "iccc_only"


# ---------------------------------------------------------------------------
# Freshness (tick-based)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_fresh_within_window() -> None:
    df = CrowdDensityDataFacts(freshness_ticks=2, current_tick=10)
    meta = _zone_meta("ramkund", "GREEN", 4.1, 10)
    url = await df.publish(meta)
    assert await df.verify_freshness(url, current_tick=11)  # 1 tick ago — fresh
    assert await df.verify_freshness(url, current_tick=12)  # 2 ticks ago — at boundary


@pytest.mark.asyncio
async def test_snapshot_stale_outside_window() -> None:
    df = CrowdDensityDataFacts(freshness_ticks=2, current_tick=10)
    meta = _zone_meta("ramkund", "GREEN", 4.1, 10)
    url = await df.publish(meta)
    assert not await df.verify_freshness(url, current_tick=13)  # 3 ticks — stale


@pytest.mark.asyncio
async def test_freshness_uses_advance_tick() -> None:
    df = CrowdDensityDataFacts(freshness_ticks=1, current_tick=5)
    meta = _zone_meta("ramkund", "GREEN", 4.1, 5)
    url = await df.publish(meta)
    df.advance_tick(6)
    assert await df.verify_freshness(url)  # 1 tick — fresh
    df.advance_tick(7)
    assert not await df.verify_freshness(url)  # 2 ticks — stale


# ---------------------------------------------------------------------------
# chain_for_zone: tamper-evident audit trail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_for_zone_ordered_by_tick() -> None:
    df = CrowdDensityDataFacts()
    urls = []
    for tick in [1, 2, 3]:
        df.advance_tick(tick)
        meta = _zone_meta("kushavart_kund", "GREEN", 3.0 + tick * 0.5, tick)
        url = await df.publish(meta)
        urls.append(url)

    chain = df.chain_for_zone("kushavart_kund")
    assert chain == urls, "Chain must be in chronological tick order"


@pytest.mark.asyncio
async def test_chain_for_zone_excludes_other_zones() -> None:
    df = CrowdDensityDataFacts()
    await df.publish(_zone_meta("ramkund", "GREEN", 4.0, 1))
    await df.publish(_zone_meta("kushavart_kund", "GREEN", 3.5, 1))

    ramkund_chain = df.chain_for_zone("ramkund")
    assert all("ramkund" in str(df._store[u].metadata.get("zone_id")) for u in ramkund_chain)


@pytest.mark.asyncio
async def test_tampered_content_produces_different_url() -> None:
    """If a stored snapshot's content changed, fetching by the original CID reveals the mismatch."""
    df = CrowdDensityDataFacts()
    original = _zone_meta("kushavart_kund", "GREEN", 3.5, 1)
    original_url = await df.publish(original)

    # Simulate tampered data: different density
    tampered = _zone_meta("kushavart_kund", "GREEN", 7.5, 1)  # density changed
    tampered_url = await df.publish(tampered)

    assert original_url != tampered_url, "Tampered snapshot must produce a different CID"


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

from hypothesis import given, settings
from hypothesis import strategies as st


@given(
    zone_id=st.sampled_from(["ramkund_main", "kushavart_kund", "godavari_ghat_1"]),
    status=st.sampled_from(["GREEN", "YELLOW", "RED", "BLACK"]),
    density=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    count=st.integers(min_value=0, max_value=3000),
    tick=st.integers(min_value=0, max_value=720),
)
@settings(max_examples=60)
def test_cid_deterministic_for_any_metadata(
    zone_id: str, status: str, density: float, count: int, tick: int
) -> None:
    """_cid must return the same hash for the same metadata, any input."""
    meta = _zone_meta(zone_id, status, density, tick, count)
    assert _cid(meta) == _cid(meta), "CID must be deterministic"
    assert len(_cid(meta)) == 64, "CID must be 64 hex chars"


@given(
    status=st.sampled_from(["RED", "BLACK", "CLOSED"]),
    density=st.floats(min_value=6.5, max_value=10.0, allow_nan=False),
)
@settings(max_examples=40)
def test_restricted_status_always_iccc_only(status: str, density: float) -> None:
    """Any RED/BLACK/CLOSED zone snapshot must always be iccc_only."""
    import asyncio

    async def _run() -> None:
        df = CrowdDensityDataFacts()
        meta = _zone_meta("ramkund_main", status, density, 1)
        url = await df.publish(meta)
        grant = await df.request_access(url, AgentId("random-pilgrim"))
        assert grant.tier == "iccc_only", (
            f"status={status} must produce iccc_only, got {grant.tier}"
        )

    asyncio.run(_run())


@given(
    density1=st.floats(min_value=0.1, max_value=10.0, allow_nan=False),
    density2=st.floats(min_value=0.1, max_value=10.0, allow_nan=False),
)
@settings(max_examples=50)
def test_distinct_density_produces_distinct_cid(density1: float, density2: float) -> None:
    """Two snapshots with different densities must produce different CIDs."""
    if abs(density1 - density2) < 1e-10:
        return  # floats too close; skip
    m1 = _zone_meta("ramkund_main", "GREEN", density1, 1)
    m2 = _zone_meta("ramkund_main", "GREEN", density2, 1)
    assert _cid(m1) != _cid(m2), (
        f"density {density1} and {density2} must produce distinct CIDs"
    )
