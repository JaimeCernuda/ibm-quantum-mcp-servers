# This code is part of Qiskit.
#
# (C) Copyright IBM 2025.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""One job model for local and hardware runs.

Every run gets a record in the job store, a folder of JSON files under the server's
state folder: local jobs (ids ``local-…``) with their circuit, PUBs, backend, options
and results, and hardware jobs with what was submitted (their results stay with IBM).
Local jobs run in this process on a worker thread, off the event loop.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from platformdirs import user_state_dir


if TYPE_CHECKING:
    from collections.abc import Callable


LOCAL_PREFIX = "local-"
#: Overrides where the server keeps its state (jobs, device positions).
STATE_DIR_ENV = "IBM_QUANTUM_MCP_STATE_DIR"

ACTIVE = ("QUEUED", "RUNNING")
FINAL = ("DONE", "ERROR", "CANCELLED")


def state_dir() -> Path:
    """The server's state folder."""
    configured = os.getenv(STATE_DIR_ENV, "").strip()
    path = Path(configured) if configured else Path(user_state_dir("ibm-quantum-mcp-server"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_local_id(job_id: str) -> bool:
    return job_id.startswith(LOCAL_PREFIX)


def new_local_id() -> str:
    return LOCAL_PREFIX + uuid.uuid4().hex[:16]


def circuit_sha256(qasm: str) -> str:
    """Identity of a logical circuit, to tell when two jobs ran the same one."""
    return hashlib.sha256(qasm.strip().encode("utf-8")).hexdigest()


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class JobStore:
    """Job records as ``jobs/<id>.json``, written atomically."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or state_dir()) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
        if not safe or safe != job_id:
            raise KeyError(job_id)
        return self.root / f"{safe}.json"

    def save(self, record: dict[str, Any]) -> None:
        path = self._path(record["job_id"])
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        with self._lock:
            tmp.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
            tmp.replace(path)

    def get(self, job_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(job_id)
        except KeyError:
            return None
        if not path.exists():
            return None
        with self._lock:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return data

    def update(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        record = self.get(job_id)
        if record is None:
            return None
        record.update(fields)
        self.save(record)
        return record

    def list(self, kind: str | None = None) -> list[dict[str, Any]]:
        """Records, newest first."""
        records = []
        for path in self.root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if kind is None or record.get("kind") == kind:
                records.append(record)
        records.sort(key=lambda r: (r.get("created_at") or "", r["job_id"]), reverse=True)
        return records

    def settle_interrupted(self) -> int:
        """Mark local jobs a previous server process left running as interrupted."""
        count = 0
        for record in self.list("local"):
            if record.get("status") in ACTIVE:
                record.update(
                    status="ERROR",
                    error={
                        "reason": "interrupted",
                        "message": "The server stopped before this job finished. Run it again.",
                    },
                    finished_at=now(),
                )
                self.save(record)
                count += 1
        return count


class LocalRunner:
    """Runs local jobs on worker threads and keeps their records current."""

    def __init__(self, store: JobStore, workers: int = 2) -> None:
        self.store = store
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="local-job")
        self._futures: dict[str, Future[Any]] = {}
        self._cancelled: set[str] = set()
        #: Status changes and cancellation happen one at a time, so a cancelled job never
        #: reads as running or done afterwards.
        self._transition = threading.Lock()

    def submit(self, record: dict[str, Any], work: Callable[[], dict[str, Any]]) -> Future[Any]:
        """Queue a job whose ``work`` returns its result."""
        record.setdefault("status", "QUEUED")
        record.setdefault("created_at", now())
        self.store.save(record)
        job_id = record["job_id"]

        def run() -> dict[str, Any] | None:
            with self._transition:
                if job_id in self._cancelled:
                    return None
                self.store.update(job_id, status="RUNNING", started_at=now())
            started = time.monotonic()
            try:
                result = work()
            except Exception as exc:  # the job's own failure, kept on its record
                with self._transition:
                    if job_id in self._cancelled:
                        return None
                    self.store.update(
                        job_id,
                        status="ERROR",
                        error={"reason": "run_failed", "message": f"{type(exc).__name__}: {exc}"},
                        finished_at=now(),
                        run_seconds=round(time.monotonic() - started, 3),
                    )
                return None
            with self._transition:
                if job_id in self._cancelled:
                    return None
                self.store.update(
                    job_id,
                    status="DONE",
                    result=result,
                    finished_at=now(),
                    run_seconds=round(time.monotonic() - started, 3),
                )
            return result

        future = self._pool.submit(run)
        self._futures[job_id] = future
        future.add_done_callback(lambda _f: self._futures.pop(job_id, None))
        return future

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or running local job. A running one's result is discarded."""
        with self._transition:
            record = self.store.get(job_id)
            if record is None or record.get("status") not in ACTIVE:
                return False
            self._cancelled.add(job_id)
            future = self._futures.get(job_id)
            if future is not None:
                future.cancel()
            self.store.update(job_id, status="CANCELLED", finished_at=now())
        return True

    def wait(self, job_id: str, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for a job to finish."""
        future = self._futures.get(job_id)
        if future is not None:
            with contextlib.suppress(Exception):
                future.result(timeout=timeout)
