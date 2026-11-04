"""Offline SQL contract tests; see integration suite for true DB concurrency."""
from contextlib import contextmanager

import pytest

from osint_research.platform_engineering.pg_jobs import DispatchError, PostgresJobs


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, sql, values=()):
        self.conn.queries.append((sql, values))
        if "SELECT run_id::text" in sql:
            self.result = ("existing-run",) if self.conn.existing else None
        elif "FOR UPDATE" in sql and "osint_demo_jobs.runs" in sql:
            self.result = (1,) if self.conn.run_state == "RUNNING" else None
        elif "UPDATE osint_demo_jobs.runs" in sql and "NOT EXISTS" in sql:
            self.result = (
                ("changed",)
                if self.conn.has_tasks and not self.conn.has_unfinished_tasks
                else None
            )
        elif "RETURNING" in sql:
            self.result = ("changed",)
        else:
            self.result = None
    def executemany(self, sql, values):
        self.conn.queries.append((sql, values))
    def fetchone(self):
        return self.result
    def fetchall(self):
        return []


class FakeConn:
    autocommit = False
    def __init__(self, existing=False, *, run_state="RUNNING",
                 has_tasks=True, has_unfinished_tasks=False):
        self.existing = existing
        self.run_state = run_state
        self.has_tasks = has_tasks
        self.has_unfinished_tasks = has_unfinished_tasks
        self.queries = []
        self.transaction_calls = 0
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    @contextmanager
    def transaction(self):
        self.transaction_calls += 1
        yield
    def cursor(self):
        return FakeCursor(self)


def test_new_registration_is_locked_and_commits_before_dispatch():
    conn = FakeConn()
    p = PostgresJobs(lambda: conn)
    dispatched = []
    result = p.submit(" 00000001 ", ("fetch", "parse"), lambda rid, company: dispatched.append((rid, company)))
    assert result.started and dispatched == [(result.run_id, "00000001")]
    assert "pg_advisory_xact_lock" in conn.queries[0][0]
    assert "INSERT INTO osint_demo_jobs.runs" in conn.queries[2][0]
    assert len(conn.queries[3][1]) == 2


def test_existing_run_is_reused_without_dispatch():
    conn = FakeConn(existing=True)
    p = PostgresJobs(lambda: conn)
    result = p.submit("00000001", ("fetch",), lambda *_: pytest.fail("duplicate worker"))
    assert result.run_id == "existing-run"
    assert result.started is False
    assert not any("INSERT" in sql for sql, _ in conn.queries)


def test_dispatch_failure_marks_run_failed():
    conn = FakeConn()
    p = PostgresJobs(lambda: conn)
    def broken(*_):
        raise OSError("worker unavailable")
    with pytest.raises(DispatchError):
        p.submit("00000001", ("fetch",), broken)
    assert any("UPDATE osint_demo_jobs.runs" in sql and args[0] == "FAILED" for sql, args in conn.queries)


def test_invalid_input_rejected_before_database_access():
    p = PostgresJobs(lambda: pytest.fail("should not connect"))
    with pytest.raises(ValueError):
        p.submit("", ("fetch",), lambda *_: None)
    with pytest.raises(ValueError):
        p.submit("00000001", ("a", "a"), lambda *_: None)


def test_progress_and_status_uses_run_scoping():
    conn = FakeConn()
    p = PostgresJobs(lambda: conn)
    assert p.begin("00000000-0000-0000-0000-000000000001")
    assert p.complete_stage("00000000-0000-0000-0000-000000000001", "fetch")
    assert any("WHERE run_id=%s::uuid AND stage=%s" in sql for sql, _ in conn.queries)
    assert sum("FOR UPDATE" in sql for sql, _ in conn.queries) == 1
    with pytest.raises(ValueError):
        p.finish("run-1", "INVALID")


def test_stage_updates_refuse_nonrunning_parent_and_do_not_write():
    for state in ("PENDING", "COMPLETED", "FAILED"):
        conn = FakeConn(run_state=state)
        assert PostgresJobs(lambda: conn).complete_stage("run-1", "fetch") is False
        assert any("FOR UPDATE" in query for query, _ in conn.queries)
        assert not any("UPDATE osint_demo_jobs.tasks" in query for query, _ in conn.queries)


def test_completion_requires_running_parent_and_all_tasks_completed():
    for state in ("PENDING", "COMPLETED", "FAILED"):
        conn = FakeConn(run_state=state)
        assert PostgresJobs(lambda: conn).finish("run-1", "COMPLETED") is False
        assert not any("UPDATE osint_demo_jobs.runs" in sql for sql, _ in conn.queries)

    for kwargs in ({"has_tasks": False}, {"has_unfinished_tasks": True}):
        conn = FakeConn(**kwargs)
        assert PostgresJobs(lambda: conn).finish("run-1", "COMPLETED") is False
        assert any("EXISTS (" in sql and "NOT EXISTS (" in sql for sql, _ in conn.queries)

    conn = FakeConn()
    assert PostgresJobs(lambda: conn).finish("run-1", "COMPLETED") is True
    assert any("SET status='COMPLETED'" in sql for sql, _ in conn.queries)


def test_run_and_stage_transitions_share_parent_lock():
    run_id = "00000000-0000-0000-0000-000000000001"
    conn = FakeConn()
    jobs = PostgresJobs(lambda: conn)
    assert jobs.complete_stage(run_id, "fetch")
    assert jobs.finish(run_id, "COMPLETED")
    locks = [sql for sql, _ in conn.queries if "FOR UPDATE" in sql]
    assert len(locks) == 2
    assert conn.transaction_calls == 2
    assert all("WHERE run_id=%s::uuid AND status='RUNNING'" in sql for sql in locks)
    assert any("NOT EXISTS" in sql and "t.status <> 'COMPLETED'" in sql
               for sql, _ in conn.queries)


def test_failed_dispatch_still_transitions_pending_run():
    conn = FakeConn(run_state="PENDING")
    jobs = PostgresJobs(lambda: conn)
    assert jobs.finish("run-1", "FAILED", error="dispatch failed")
    assert any("status=ANY(%s)" in sql and args[0] == "FAILED"
               for sql, args in conn.queries)

