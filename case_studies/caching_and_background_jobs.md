# Case Study 05 — Caching, Concurrency & Background Processing

**Selected source adaptations for service performance, resilient work coordination and concurrent requests.** This is a technical portfolio example, **not an unchanged or deployed production platform**. Redis caching and queues come from historical work; PostgreSQL jobs come from a newer, separate processing architecture. They are intentionally not presented as one combined deployed system.

## A. Local LRU + shared Redis

The historical design combined a thread-safe bounded in-memory LRU cache (L1) with a Redis key/value layer (L2). The demonstration preserves the important choices: L1 lookup first, L2 fallback and promotion, distinct TTLs, explicit invalidation and degradation to local caching when Redis is unavailable.

| Concern | Evidence |
| --- | --- |
| Eviction / TTL / locking | [lru_cache.py](../src/osint_research/platform_engineering/lru_cache.py) |
| L1/L2 read/write / promotion | [two_tier_cache.py](../src/osint_research/platform_engineering/two_tier_cache.py) |
| Recovery and deterministic expiry tests | [test_caching.py](../tests/platform_engineering/test_caching.py) |

This standalone sample uses **per-instance** local cache objects and an injectable Redis backend. Hit, miss, and Redis-error counters use a small dedicated lock for thread-safe updates and coherent per-instance snapshots. The underlying L1 cache has its own lock and its metrics are read separately, so **there is no single atomic cross-layer snapshot** or cross-process aggregation.

**Read safety:** a bounded set of striped locks coordinates per-key generations while L2 reads are in flight, preventing an older L2 response from overwriting a newer L1 `set`, `delete` or promotion. Redis reads run outside those locks. Transient per-key read state is removed when the last reader finishes.

**Write ordering:** separate, reference-counted **per-key write locks** serialize the entire L1-to-Redis `set` or `delete` operation within one cache instance. The lock for a key is reclaimed after its last writer/waiter finishes; unrelated keys can write to Redis concurrently. While a write-through is in progress, a local cache miss does not fetch a potentially older Redis value (notably when a deletion has cleared L1 but Redis has not processed the deletion). Exceptions still release locks and in-flight state.

**Bounded network waits:** `RedisBackend.from_url()` creates redis-py clients with explicit 2-second socket and connection timeouts. Injected redis-py clients must expose both finite, positive timeout settings (at most 30 seconds), or construction fails before writes can hold a per-key lock. The offline tests exercise timeout fallback and explicitly synchronise a second writer at the real lock-acquisition attempt. Socket timeouts bound individual waits—not DNS resolution, user-defined retry chains or a complete request's wall-clock time.

**Limits:** this is **not distributed versioning**. Independent processes or cache instances can still interleave Redis writes and invalidate each other's state. A Redis write failure can leave L2 stale despite the correct L1 change; after that failure an old remote value can reappear on a future miss. Writes do not provide cross-process coherence, Redis outage reconciliation, or exactly-once effects. An L1 copy may remain stale until TTL, and the sample does not implement distributed stampede prevention. Neither hit rates nor performance improvements are claimed without a benchmark.

## B. Redis queue, worker claims and recovery

The historical Redis queue used list-based pending jobs, worker processes, deduplication and distributed locks. The original demonstration had fault-tolerance limitations, so this *corrected adaptation* makes queue state transitions explicit:

1. **Enqueue:** a Lua script atomically creates a bounded job-ID deduplication key and inserts work into a FIFO Redis list.
2. **Claim:** one Lua script safely inspects and validates the oldest pending job before removing it, checks the active owner hash, then records ownership, its lease and a **deadline-ordered processing index** entry. Bad entries are moved to the `dead_letter` list with original raw content and source/reason metadata; the bounded scan continues to healthy work. Pending copies of an actively claimed job ID are rotated behind other waiting work.
3. **Acknowledge:** a worker with the correct, unexpired token removes its claimed entry and corresponding deadline-index entry before marking completion.
4. **Retry:** only the current worker with an **unexpired lease** can schedule a failure or terminal outcome; the same Lua transition removes its deadline-index entry. Accepted retryable failures enter a Redis sorted set with bounded exponential delays, indexed by **unique claim token + serialized job** so identical retry payloads cannot overwrite one another. Due-job promotion validates token-prefixed and legacy bare-JSON entries, quarantines malformed scheduled members to the same `dead_letter` list, and promotes valid jobs without aborting the batch.
5. **Recovery:** a bounded `ZRANGEBYSCORE` query selects the **oldest expired claims** from the processing deadline index regardless of their position in the processing list. Each recovered claim is token-checked, removed from processing and its index, and returned to pending. Repeated small batches progress through due claims while newer healthy claims stay in processing. Handlers must therefore be **idempotent**.
6. **Locks:** `SET NX EX` establishes ownership, and token-checked Lua prevents a former holder from releasing somebody else's lock.

See [redis_jobs.py](../src/osint_research/platform_engineering/redis_jobs.py) and [offline contract tests](../tests/platform_engineering/test_redis_jobs.py).

**Quarantine:** `dead_letter` is a persistent Redis list of JSON envelopes recording `source` (`pending` or `retry`), `reason` and the *original raw entry*. Invalid entries are retained for inspection; this demonstration does not automatically replay or expire them. Review and correct quarantined records before explicitly re-enqueueing. Successful `promote_due()` counts only healthy jobs promoted, while its `limit` caps **examined** sorted-set members (including quarantined ones). Redis script atomicity does not imply database-style rollback after unexpected command errors or corrupt Redis key types. Corruption of the *processing deadline index* is outside these pending/retry guards.

**Boundaries:** This sample offers *at-least-once*, not exactly-once, delivery. Recovery requires a running worker invoking the recovery scan; pending retries require a worker invoking the due-job promotion. Claim leases are not renewed automatically. Workers must finish within the lease or implement renewal and, for critical writes, fencing. A rejected failure transition leaves the job in processing **and in the deadline index** for recovery; the worker surfaces lost ownership rather than silently claiming a successful failure transition. Recovery is bounded by the number of *expired indexed claims* rather than the newest portion of the processing list; a large due backlog still needs repeated batches, and each recovery uses Redis list removal, so this is not a production-optimised queue.

**Upgrade boundary:** The new index is populated by the revised claim script. Jobs already in a processing list from an earlier version are **not automatically indexed**. An existing deployment must drain/migrate those claims or switch to a fresh queue namespace before relying on indexed recovery; the standalone demo uses isolated namespaces. Job deduplication has a bounded lifetime, but overlapping active claims for the same job ID are blocked by the atomic claim script. Duplicate pending records may still run sequentially after the first claim finishes; separate retry attempts are retained without introducing global uniqueness or exactly-once guarantees. Already-corrupted legacy processing entries still require migration or a fresh namespace. The live Redis integration tests include an expired claim hidden behind more than 100 newer healthy claims and multiple limited recovery batches; no full load/failure-injection benchmark is implied.

### Worked worker-failure example (synthetic)

A worker claims job `refresh-001`, moving it from `pending` to `processing` with an owner token and lease deadline. If the worker crashes before acknowledgement, a **subsequent call to** `recover_expired()` finds the expired claim, token-checks it and returns the job to `pending`. A new worker may process it; the original worker's expired token cannot acknowledge the new claim. Handlers must be idempotent because work may run more than once. An ordinary handler failure instead schedules a bounded retry (or marks a terminal failure); malformed pending or retry records are quarantined for inspection rather than silently discarded.

```mermaid
flowchart LR
    P[Pending] -->|Claim with token and lease| C[Processing]
    C -->|Acknowledge before expiry| D[Completed]
    C -->|Retryable handler failure| R[Scheduled retry]
    R -->|Due promotion| P
    C -->|Expired lease and recovery scan| P
    C -->|Terminal failure| F[Failed]
    P -->|Malformed input| Q[Dead letter]
    R -->|Malformed retry member| Q
```

**Scope of diagram:** this is the standalone Redis queue only, **not** the separate PostgreSQL run-coordination design below. Lease recovery and retry promotion require workers to invoke the respective scans; no autonomous supervisor or lease renewal is implied.

## C. PostgreSQL-coordinated on-demand jobs

The newer processing architecture solves a concrete race: two concurrent requests for the same company must not start two expensive background ingestions.

[pg_jobs.py](../src/osint_research/platform_engineering/pg_jobs.py) illustrates the solution: obtain a transaction-scoped advisory lock on a company key, check for an existing active run, atomically create a durable run and its task rows, commit, then dispatch a separate worker. A partial unique index provides an additional database-level guard. Subsequent requests reuse the active run ID. Dispatch errors transition the run to `FAILED`, allowing a later retry. Parent run rows are locked in the same database transaction as each stage-completion or finalisation operation, preventing a race between a worker completing a stage and another worker finalising its run. A stage can be completed only while the parent is `RUNNING`; a run can become `COMPLETED` only from `RUNNING` when it has at least one task and **every** task has completed. Failure from `PENDING` remains possible for dispatch errors; terminal runs reject late stage updates. These invariants are enforced through the Python adapter's transactional SQL, not through a database trigger that would protect arbitrary external writes.

[background_jobs.sql](../sql/background_jobs.sql) defines the isolated job schema. [test_pg_jobs.py](../tests/platform_engineering/test_pg_jobs.py) verifies query contracts and failure handling with fakes; [test_services_integration.py](../tests/platform_engineering/test_services_integration.py) optionally exercises *real* concurrent PostgreSQL requests and Redis Lua scripts.

The demonstration does **not** implement an always-on worker supervisor or reconciliation service. A process crash after database commit but before dispatch remains a recovery gap; a production outbox or lease-based sweeper would address it. Status polling here is a Python method, not a new API endpoint.

## Reproduce without services

```bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/platform_engineering
python scripts/demo_platform_engineering.py cache
python scripts/demo_platform_engineering.py jobs --offline
```

The offline job demo runs mocked Redis/SQL contract tests. These tests **cannot establish distributed correctness**, which is why the real-service integration suite is provided separately.

## Optional disposable service exercise

Run in a trusted local development environment. Do not point the demo at a production Redis or PostgreSQL database: integration tests create and delete test keys and rows, and examples contain hard-coded local development credentials.

```bash
docker compose up -d --wait
python -m pip install -e '.[dev,services]'
python scripts/demo_platform_engineering.py cache --redis
python scripts/demo_platform_engineering.py jobs --redis
python scripts/demo_platform_engineering.py jobs --postgres
OSINT_TEST_REDIS_URL=redis://127.0.0.1:6379/0 \
OSINT_TEST_PG_DSN=postgresql://postgres:postgres@127.0.0.1:5432/postgres \
  python -m pytest -q tests/platform_engineering/test_services_integration.py
docker compose down
```

The disposable PostgreSQL container loads both the company-search and background-job DDL on first initialization. GitHub Actions runs offline tests; the optional services job attempts live integration tests separately.

## Source and CV boundaries

This portfolio demonstrates **historically implemented two-tier caching and Redis job concepts**, alongside **current PostgreSQL company-job coordination**. Both are anonymised, refactored source adaptations, with some showcase-specific hardening. The source does not substantiate production Redis speedups, exactly-once delivery or horizontal scaling metrics. No original private project identifier, proprietary registry records or secrets are included.

**CV competency:** cache design and failure fallback; thread safety; Redis atomic operations and job recovery; PostgreSQL advisory locks, idempotent submission, durable task state and failure-aware dispatch.
