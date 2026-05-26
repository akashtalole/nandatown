# Memory layer

**What it does.** Shared key-value store with subscribe and
compare-and-swap.

## Interface

```python
class Memory(Protocol):
    async def read(self, key: str) -> bytes | None: ...
    async def write(self, key: str, value: bytes) -> None: ...
    async def subscribe(self, key: str) -> AsyncIterator[bytes]: ...
    async def cas(self, key: str, expected: bytes, new: bytes) -> bool: ...
```

Full definition: [`nest_core/layers/memory.py`](../../packages/nest-core/nest_core/layers/memory.py).

## Built-in plugins

| Name | What it is | When to use |
|---|---|---|
| `blackboard` (default) | Shared in-process dict with subscribe + CAS. | Coordination via shared state; you already know the key you want. |
| `semantic` | Drop-in `Memory` with **similarity recall**, **TTL**, and **LRU eviction**. Deterministic hashed-trigram embedder, no API key required. | Retrieval-augmented LLM agents; stressing what gets remembered vs. evicted under capacity bounds. |

Sources:
- [`nest_plugins_reference/memory/blackboard.py`](../../packages/nest-plugins-reference/nest_plugins_reference/memory/blackboard.py)
- [`nest_plugins_reference/memory/semantic.py`](../../packages/nest-plugins-reference/nest_plugins_reference/memory/semantic.py)

### `semantic` — extra surface

Implements the full `Memory` protocol so it slots in anywhere
`blackboard` does. On top of that:

```python
mem = SemanticMemory(capacity=128, ttl=100)
await mem.write("buyer-3:greet", b"hello, I want to buy apples")
[hit] = await mem.recall("apple buyer", k=1)
hit.key    # "buyer-3:greet"
hit.score  # cosine similarity, in [-1, 1]
await mem.forget("buyer-3:greet")
mem.stats()  # {size, capacity, writes, recalls, evictions, expirations, tick}
```

The embedder is a deterministic hashed bag of (tokens + character
trigrams) — so `recall("apple")` finds memories mentioning "apples"
without any external service and without breaking NEST's "same seed
→ identical trace" guarantee.

For a learned embedding model, wrap the OpenAI/Anthropic embeddings
API behind the same surface and register it as a separate plugin
(e.g. `memory:openai_embeddings`). That one will *not* be
deterministic — keep it for Tier 2 only.

## Writing your own

See [`writing-a-plugin.md`](../writing-a-plugin.md). Register under
entry point group `nest.plugins.memory`.

Good fits to test here: CRDTs (LWW-Register, OR-Set), tuple spaces,
eventually-consistent stores, snapshot isolation, vector stores
(FAISS / pgvector / Chroma) behind the `semantic` surface.
