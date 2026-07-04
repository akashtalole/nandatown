# SPDX-License-Identifier: Apache-2.0
"""KumbhNet plugins for Nashik Kumbh Mela 2027 crowd safety protocols.

Five Nanda Town layer plugins that have been adversarially stress-tested
against simulated peak-bathing scenarios before physical deployment.

Layers covered:

* **Coordination** — ``kumbh_bft_coordination``: PBFT-lite zone evacuation consensus.
* **Privacy**      — ``pilgrim_selective_disclosure``: per-attribute encrypted pilgrim profiles.
* **Auth**         — ``ndrf_capability_delegation``: time-bounded, revocable delegation chains.
* **DataFacts**    — ``crowd_density_datafacts``: content-addressed tamper-evident snapshots.

Example::

    from nest_plugins_reference.kumbh2027.kumbh_bft_coordination import (
        KumbhBFTCoordination,
    )
    from nest_plugins_reference.kumbh2027.pilgrim_selective_disclosure import (
        PilgrimSelectiveDisclosure,
    )
    from nest_plugins_reference.kumbh2027.ndrf_capability_delegation import (
        NDRFCapabilityDelegation,
    )
    from nest_plugins_reference.kumbh2027.crowd_density_datafacts import (
        CrowdDensityDataFacts,
    )
"""
