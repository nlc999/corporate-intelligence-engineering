#!/usr/bin/env python3
"""Offline or explicitly opt-in Redis/PostgreSQL engineering demonstration.

All company identifiers are invented; only local disposable services are
intended for the real-service modes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from uuid import uuid4

from osint_research.platform_engineering.pg_jobs import PostgresJobs
from osint_research.platform_engineering.redis_jobs import Job, RedisJobQueue
from osint_research.platform_engineering.two_tier_cache import RedisBackend, TwoTierCache


class FakeBackend:
    def __init__(self):
        self.items = {}
        self.available = True
    def get(self, key):
        if not self.available:
            raise ConnectionError("simulated Redis outage")
        return self.items.get(key)
    def set(self, key, value, ttl):
        if not self.available:
            raise ConnectionError("simulated Redis outage")
        self.items[key] = value
        return True
    def delete(self, key):
        if not self.available:
            raise ConnectionError("simulated Redis outage")
        return self.items.pop(key, None) is not None


def cache_demo(backend):
    cache = TwoTierCache("invented_financials", redis_backend=backend,
                         l1_max_size=4, l1_ttl=300, l2_ttl=3600)
    data = {"company_number": "00000001", "revenue_gbp": 1200000}
    print("First read:", cache.get_or_compute("00000001", lambda: data))
    print("Local L1 hit:", cache.get("00000001"))
    cache.l1.clear()
    print("L2 promotion:", cache.get("00000001"))
    print("Invalidation:", cache.delete("00000001"))
    print("Cache stats:", json.dumps(cache.stats(), sort_keys=True))


def redis_jobs_demo(client):
    namespace = "osint_demo_" + uuid4().hex[:10]
    queue = RedisJobQueue(client, namespace=namespace)
    job = Job(job_id=uuid4().hex, kind="financial_summary",
              payload={"company_number": "00000001"})
    try:
        print("Queued:", queue.enqueue(job))
        print("Duplicate suppressed:", not queue.enqueue(job))
        seen = []
        def handle(message):
            seen.append(message.payload["company_number"])
        print("Processed:", queue.process_once({"financial_summary": handle}))
        print("Fictional companies processed:", seen)
    finally:
        for key in client.scan_iter(match=f"{namespace}:*"):
            client.delete(key)


def pg_jobs_demo(dsn):
    import psycopg
    jobs = PostgresJobs(lambda: psycopg.connect(dsn, autocommit=False))
    company = "SYN" + uuid4().hex[:9].upper()
    result = jobs.submit(company, ("fetch", "normalise", "validate"),
                         lambda run_id, company_id: print("Dispatched:", run_id, company_id))
    again = jobs.submit(company, ("fetch", "normalise", "validate"),
                        lambda *_: (_ for _ in ()).throw(RuntimeError("duplicate dispatch")))
    print("Second request joined first:", not again.started and again.run_id == result.run_id)
    jobs.begin(result.run_id)
    for stage in ("fetch", "normalise", "validate"):
        jobs.complete_stage(result.run_id, stage)
    jobs.finish(result.run_id, "COMPLETED")
    print(json.dumps(jobs.status(result.run_id), indent=2))
    print("Run retained in disposable database to illustrate durable state.")


def main():
    parser = argparse.ArgumentParser(description="Synthetic platform engineering samples")
    parser.add_argument("mode", choices=("cache", "jobs"))
    backend = parser.add_mutually_exclusive_group()
    backend.add_argument("--redis", action="store_true", help="Real local Redis")
    backend.add_argument("--postgres", action="store_true", help="Real local PostgreSQL")
    backend.add_argument("--offline", action="store_true", help="Offline mocked job contracts")
    args = parser.parse_args()
    if args.mode == "cache":
        if args.postgres or args.offline:
            parser.error("cache mode supports the default synthetic backend or --redis")
        if args.redis:
            backend = RedisBackend.from_url(
                os.getenv("OSINT_TEST_REDIS_URL", "redis://127.0.0.1:6379/0"),
            )
            backend.client.ping()
            cache_demo(backend)
        else:
            print("Simulated Redis: no network calls.")
            cache_demo(FakeBackend())
    else:
        if args.redis:
            import redis
            client = redis.Redis.from_url(
                os.getenv("OSINT_TEST_REDIS_URL", "redis://127.0.0.1:6379/0"),
                decode_responses=True, socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            client.ping()
            redis_jobs_demo(client)
        elif args.postgres:
            pg_jobs_demo(os.getenv(
                "OSINT_TEST_PG_DSN",
                "postgresql://postgres:postgres@127.0.0.1:5432/postgres",
            ))
        else:
            print("Offline job contract tests (mock Redis + mock SQL, no real persistence):")
            raise SystemExit(subprocess.call([
                sys.executable, "-m", "pytest", "-q",
                "tests/platform_engineering/test_redis_jobs.py",
                "tests/platform_engineering/test_pg_jobs.py",
            ]))


if __name__ == "__main__":
    main()
