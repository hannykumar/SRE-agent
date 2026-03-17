from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict

from ops.metrics import ACTIVE_JOBS, QUEUE_DEPTH, QUEUE_WAIT_DURATION


@dataclass
class QueuedJob:
    job_type: str
    run_id: str
    group_key: str
    handler: Callable[[], None]
    enqueued_at: float = field(default_factory=time.time)


class AsyncJobQueue:
    def __init__(self, max_workers: int = 2) -> None:
        self.max_workers = max(1, int(max_workers))
        self._queue: "queue.Queue[QueuedJob]" = queue.Queue()
        self._started = False
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._active_groups: set[str] = set()

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            for index in range(self.max_workers):
                thread = threading.Thread(target=self._worker, name=f"sre-agent-worker-{index}", daemon=True)
                thread.start()
                self._threads.append(thread)
            self._started = True

    def submit(self, job_type: str, run_id: str, group_key: str, handler: Callable[[], None]) -> bool:
        self.start()
        normalized_group = group_key or run_id
        with self._lock:
            if normalized_group in self._active_groups:
                return False
            self._active_groups.add(normalized_group)
        self._queue.put(QueuedJob(job_type=job_type, run_id=run_id, group_key=normalized_group, handler=handler))
        QUEUE_DEPTH.set(self._queue.qsize())
        return True

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            QUEUE_DEPTH.set(self._queue.qsize())
            ACTIVE_JOBS.inc()
            QUEUE_WAIT_DURATION.observe(max(time.time() - job.enqueued_at, 0.0))
            try:
                job.handler()
            finally:
                ACTIVE_JOBS.dec()
                with self._lock:
                    self._active_groups.discard(job.group_key)
                self._queue.task_done()
                QUEUE_DEPTH.set(self._queue.qsize())


_QUEUE: AsyncJobQueue | None = None


def get_async_queue(max_workers: int = 2) -> AsyncJobQueue:
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = AsyncJobQueue(max_workers=max_workers)
    return _QUEUE
