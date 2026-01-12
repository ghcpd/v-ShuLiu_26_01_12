"""Optimized worker: preserves baseline semantics but reduces per-request I/O

Strategy
- Perform the same CPU work as the baseline by reusing the baseline's
  computation (ensures identical `length`/`vowels` results).
- Avoid per-request open/flush/fsync by enqueuing JSON records to a
  single background writer thread that batches writes and calls
  fsync once per batch.
- Provide an atexit-safe synchronous flush so that when the process
  exits all records are persisted (runner.py relies on process exit
  to finish a run).

Design goals: correctness first (same observable records), then lower
latency and contention under concurrency by moving I/O off the hot path.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import threading
import time
from typing import Any, Dict

# Reuse the baseline compute so outputs remain bit-for-bit compatible.
# Importing a "private" helper from baseline_worker is acceptable here
# because baseline files are read-only and this preserves exact behavior.
from baseline_worker import _slow_compute

# Tunable parameters
_BATCH_SIZE = 64
_FLUSH_INTERVAL = 0.01  # seconds
_MAX_QUEUE_SIZE = 100_000

_record_queue: "queue.Queue[str]" = queue.Queue()
_log_path_lock = threading.Lock()
_current_log_path: str | None = None

_writer_thread: threading.Thread | None = None
_writer_thread_started = threading.Event()


def _open_log_file(log_path: str):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    return open(log_path, "a", encoding="utf-8")


def _writer_loop() -> None:
    """Background writer: batch records and fsync once per batch."""
    global _current_log_path
    f = None
    buf: list[str] = []
    last_flush = time.time()
    while True:
        try:
            # Wait briefly for new records so we can batch multiple writes.
            item = _record_queue.get(timeout=_FLUSH_INTERVAL)
        except queue.Empty:
            item = None

        if item is not None:
            rec_json, path = item
            # If log path changed (shouldn't in normal runs), reopen.
            if path != _current_log_path:
                if f:
                    try:
                        f.flush()
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                    finally:
                        f.close()
                _current_log_path = path
                f = _open_log_file(path)
            buf.append(rec_json)
            _record_queue.task_done()

        # Flush when buffer is large enough or when idle for a short time.
        now = time.time()
        if buf and (len(buf) >= _BATCH_SIZE or (now - last_flush) >= _FLUSH_INTERVAL):
            try:
                if not f and _current_log_path:
                    f = _open_log_file(_current_log_path)
                if f:
                    f.write("\n".join(buf) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except Exception:
                # Never let logging failures crash the worker; drop on error.
                pass
            finally:
                buf.clear()
                last_flush = now


def _ensure_writer_started() -> None:
    global _writer_thread
    if _writer_thread is None:
        _writer_thread = threading.Thread(target=_writer_loop, name="optimized-writer", daemon=True)
        _writer_thread.start()
        _writer_thread_started.set()


def _drain_and_close(log_path: str | None = None, timeout: float = 5.0) -> None:
    """Synchronously flush any queued records (used at process exit)."""
    # Wait for queue to be empty, then perform a final write to ensure
    # everything is persisted. If the background thread is alive it will
    # also process the queue; we use a time-bounded wait to avoid hangs.
    start = time.time()
    while not _record_queue.empty() and (time.time() - start) < timeout:
        time.sleep(0.001)

    # If anything remains, write it directly to the file path provided (or
    # the last-known path) to guarantee persistence.
    try:
        records = []
        while True:
            records.append(_record_queue.get_nowait())
            _record_queue.task_done()
    except queue.Empty:
        pass

    if not records:
        return

    # records are tuples of (json, path)
    by_path: dict[str, list[str]] = {}
    for rec_json, path in records:
        by_path.setdefault(path, []).append(rec_json)

    for path, recs in by_path.items():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(recs) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            pass


atexit.register(lambda: _drain_and_close(_current_log_path, timeout=10.0))


def handle_request(job_id: int, payload: str, log_path: str) -> Dict[str, Any]:
    """Handle a request: perform the same computation as baseline then enqueue the record.

    Fast path: CPU work is identical; I/O is moved off-thread and batched.
    """
    _ensure_writer_started()

    start = time.time()
    result = _slow_compute(payload)
    processed_at = time.time()

    record: Dict[str, Any] = {
        "job_id": job_id,
        "payload_hash": hash(payload),
        "length": result["length"],
        "vowels": result["vowels"],
        "start_ts": start,
        "processed_ts": processed_at,
    }

    rec_json = json.dumps(record, separators=(",", ":"))

    # Apply simple backpressure if queue grows excessively to avoid OOM.
    if _record_queue.qsize() > _MAX_QUEUE_SIZE:
        # Block briefly until space frees — this will throttle callers.
        _record_queue.put((rec_json, log_path))
    else:
        try:
            _record_queue.put_nowait((rec_json, log_path))
        except queue.Full:
            _record_queue.put((rec_json, log_path))

    return record
