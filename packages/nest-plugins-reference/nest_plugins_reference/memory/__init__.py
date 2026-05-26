# SPDX-License-Identifier: Apache-2.0
"""Memory layer reference plugins.

- ``blackboard`` (built-in default): shared KV store with subscribe/CAS.
- ``semantic``: drop-in ``Memory`` with similarity recall, TTL, and LRU
  eviction. Designed for retrieval-augmented LLM agent swarms while
  staying deterministic for Tier 1 simulation.
"""

from nest_plugins_reference.memory.blackboard import Blackboard as Blackboard
from nest_plugins_reference.memory.semantic import RecallHit as RecallHit
from nest_plugins_reference.memory.semantic import SemanticMemory as SemanticMemory

__all__ = ["Blackboard", "RecallHit", "SemanticMemory"]
