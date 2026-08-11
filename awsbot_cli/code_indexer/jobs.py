from __future__ import annotations

import datetime as dt
import threading
import uuid
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Callable, Dict

from .db import connect, execute, fetch_one
from .config import Config

JobCallback = Callable[[float, int, int, str], None]


@dataclass
class JobManager:
    cfg: Config
    db_path: Path

    def __post_init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._futures: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        workspace_id: str,
        runner: Callable[[JobCallback], dict],
        message: str = "starting",
    ) -> str:
        job_id = str(uuid.uuid4())
        conn = connect(self.db_path)
        now = dt.datetime.utcnow().isoformat()
        execute(
            conn,
            "INSERT INTO jobs(job_id,workspace_id,state,progress,processed_files,total_files,message,started_at) VALUES(?,?,?,?,?,?,?,?)",
            (job_id, workspace_id, "running", 0.0, 0, 0, message, now),
        )

        def cb(progress: float, processed: int, total: int, msg: str) -> None:
            conn2 = connect(self.db_path)
            execute(
                conn2,
                "UPDATE jobs SET progress=?, processed_files=?, total_files=?, message=? WHERE job_id=?",
                (float(progress), int(processed), int(total), msg, job_id),
            )

        def wrapped() -> dict:
            try:
                result = runner(cb)
                conn3 = connect(self.db_path)
                execute(
                    conn3,
                    "UPDATE jobs SET state=?, progress=?, message=?, finished_at=? WHERE job_id=?",
                    ("finished", 1.0, "done", dt.datetime.utcnow().isoformat(), job_id),
                )
                return result
            except Exception as e:
                conn3 = connect(self.db_path)
                execute(
                    conn3,
                    "UPDATE jobs SET state=?, error=?, message=?, finished_at=? WHERE job_id=?",
                    (
                        "failed",
                        repr(e),
                        "failed",
                        dt.datetime.utcnow().isoformat(),
                        job_id,
                    ),
                )
                raise

        with self._lock:
            self._futures[job_id] = self._executor.submit(wrapped)
        return job_id

    def status(self, job_id: str) -> dict | None:
        conn = connect(self.db_path)
        row = fetch_one(conn, "SELECT * FROM jobs WHERE job_id=?", (job_id,))
        return row

    def result_if_done(self, job_id: str) -> dict | None:
        with self._lock:
            fut = self._futures.get(job_id)
        if fut and fut.done() and not fut.cancelled():
            try:
                return fut.result()
            except Exception:
                return None
        return None
