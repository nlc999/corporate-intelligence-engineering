"""PostgreSQL-backed, company-scoped job registration and progress tracking.

Adapted from an existing on-demand ingestion workflow. The advisory transaction
lock covers the check-and-create critical section, not the execution itself.
A partial unique index provides a second guarantee against double registration.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

_LOCK_NAMESPACE = 0x0C0DE1


@dataclass(frozen=True, slots=True)
class Submission:
    run_id: str
    started: bool


class DispatchError(RuntimeError):
    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__(f"Worker dispatch failed for run {run_id}; state marked FAILED")


class PostgresJobs:
    """Supply psycopg.connect(..., autocommit=False) via conn_factory.

    Using only generated IDs and parametrised queries keeps example operations
    separate from existing source data and the company search schema.
    """

    def __init__(self, conn_factory: Callable[[], Any]):
        self.conn_factory = conn_factory

    def submit(
        self, company_number: str, stages: tuple[str, ...],
        dispatch: Callable[[str, str], None],
    ) -> Submission:
        company_number = company_number.strip().upper()
        if not company_number or len(company_number) > 32:
            raise ValueError("Invalid company number")
        if not stages or len(set(stages)) != len(stages):
            raise ValueError("Provide nonempty, unique stage names")
        if any(not stage.strip() or len(stage) > 100 for stage in stages):
            raise ValueError("Invalid stage name")

        run_id = str(uuid.uuid4())
        with self.conn_factory() as conn:
            if conn.autocommit:
                raise ValueError("Connection must use autocommit=False")
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                        (_LOCK_NAMESPACE, company_number),
                    )
                    cur.execute(
                        """SELECT run_id::text FROM osint_demo_jobs.runs
                           WHERE company_number = %s
                           AND status IN ('PENDING', 'RUNNING')
                           ORDER BY created_at DESC LIMIT 1""",
                        (company_number,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        return Submission(existing[0], False)

                    cur.execute(
                        """INSERT INTO osint_demo_jobs.runs
                           (run_id, company_number, status)
                           VALUES (%s::uuid, %s, 'PENDING')""",
                        (run_id, company_number),
                    )
                    cur.executemany(
                        """INSERT INTO osint_demo_jobs.tasks
                           (run_id, ordinal, stage, status)
                           VALUES (%s::uuid, %s, %s, 'PENDING')""",
                        [(run_id, i, stage) for i, stage in enumerate(stages)],
                    )

        # The transaction has committed before launching work. Dispatch must be
        # repeatable; a crash between commit and dispatch requires a reconciler.
        try:
            dispatch(run_id, company_number)
        except Exception as exc:
            self.finish(run_id, "FAILED", error="Worker dispatch failed")
            raise DispatchError(run_id) from exc
        return Submission(run_id, True)

    def begin(self, run_id: str) -> bool:
        return self._update_run(run_id, "RUNNING", allowed=("PENDING",))

    def finish(self, run_id: str, status: str, *, error: str | None = None) -> bool:
        if status not in {"COMPLETED", "FAILED"}:
            raise ValueError("Final state must be COMPLETED or FAILED")
        if status == "COMPLETED":
            return self._complete_run(run_id)
        # A failed dispatch may terminate a PENDING run before any stage begins.
        return self._update_run(run_id, "FAILED", allowed=("PENDING", "RUNNING"), error=error)

    def _complete_run(self, run_id: str) -> bool:
        """Only a RUNNING parent with >=1 fully completed tasks can finish.

        Lock the parent first: complete_stage() takes the same lock, avoiding
        a race between the task-status check and the terminal transition.
        """
        with self.conn_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT 1 FROM osint_demo_jobs.runs
                           WHERE run_id=%s::uuid AND status='RUNNING'
                           FOR UPDATE""",
                        (run_id,),
                    )
                    if cur.fetchone() is None:
                        return False
                    cur.execute(
                        """UPDATE osint_demo_jobs.runs
                           SET status='COMPLETED', error=NULL, finished_at=NOW()
                           WHERE run_id=%s::uuid AND status='RUNNING'
                             AND EXISTS (
                                 SELECT 1 FROM osint_demo_jobs.tasks t
                                 WHERE t.run_id=%s::uuid
                             )
                             AND NOT EXISTS (
                                 SELECT 1 FROM osint_demo_jobs.tasks t
                                 WHERE t.run_id=%s::uuid AND t.status <> 'COMPLETED'
                             )
                           RETURNING run_id""",
                        (run_id, run_id, run_id),
                    )
                    return cur.fetchone() is not None

    def _update_run(
        self, run_id: str, status: str, *, allowed: tuple[str, ...],
        error: str | None = None,
    ) -> bool:
        with self.conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE osint_demo_jobs.runs
                       SET status=%s, error=%s,
                           started_at = CASE WHEN %s='RUNNING'
                             THEN COALESCE(started_at, NOW()) ELSE started_at END,
                           finished_at = CASE WHEN %s IN ('COMPLETED','FAILED')
                             THEN NOW() ELSE finished_at END
                       WHERE run_id=%s::uuid AND status=ANY(%s)
                       RETURNING run_id""",
                    (status, error, status, status, run_id, list(allowed)),
                )
                return cur.fetchone() is not None

    def complete_stage(self, run_id: str, stage: str) -> bool:
        """Complete a pending stage only while its parent run is RUNNING.

        The parent row lock also serializes this operation with finish().
        """
        with self.conn_factory() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT 1 FROM osint_demo_jobs.runs
                           WHERE run_id=%s::uuid AND status='RUNNING'
                           FOR UPDATE""",
                        (run_id,),
                    )
                    if cur.fetchone() is None:
                        return False
                    cur.execute(
                        """UPDATE osint_demo_jobs.tasks SET status='COMPLETED',
                                  finished_at=NOW()
                           WHERE run_id=%s::uuid AND stage=%s AND status='PENDING'
                           RETURNING stage""",
                        (run_id, stage),
                    )
                    return cur.fetchone() is not None

    def status(self, run_id: str) -> dict[str, Any] | None:
        with self.conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT company_number, status, error FROM osint_demo_jobs.runs
                       WHERE run_id = %s::uuid""",
                    (run_id,),
                )
                run = cur.fetchone()
                if run is None:
                    return None
                cur.execute(
                    """SELECT stage, status FROM osint_demo_jobs.tasks
                       WHERE run_id=%s::uuid ORDER BY ordinal""",
                    (run_id,),
                )
                tasks = cur.fetchall()
                return {
                    "run_id": run_id, "company_number": run[0],
                    "status": run[1], "error": run[2],
                    "tasks": [{"stage": name, "status": state} for name, state in tasks],
                }
