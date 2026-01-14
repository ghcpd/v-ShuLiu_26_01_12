import atexit
import json
import os
import queue
import threading
import time
import random
from typing import Any, Dict

# Reuse the deterministic compute from baseline to ensure identical outputs.
from baseline_worker import _slow_compute

# Per-logpath singleton writer instances
_writers = {}
_writers_lock = threading.Lock()


class _LogWriter:
    def __init__(self, log_path: str):
        self.log_path = log_path
        self.q = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False
        self._start_lock = threading.Lock()

        # Tunable parameters: small batch size and short flush interval
        # to reduce syscall overhead while keeping tail latency low.
        self.batch_size = 128
        self.max_interval = 0.02  # seconds
        self.fsync_every_n = 8

    def start(self):
        # Make start() safe under concurrent callers to avoid "threads can only be started once".
        with self._start_lock:
            if self._started:
                return
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            # Open once and reuse file descriptor for all writes.
            self._f = open(self.log_path, "a", encoding="utf-8")
            self._thread.start()
            atexit.register(self.stop)
            self._started = True

    def enqueue(self, record: Dict[str, Any]):
        self.start()
        # Non-blocking enqueue; Queue is unbounded so this will not block.
        self.q.put(record)

    def _run(self):
        pending = []
        last_flush = time.time()
        fsync_counter = 0
        while not self._stop.is_set() or not self.q.empty():
            try:
                # Wait up to max_interval for items to enable batching.
                rec = self.q.get(timeout=self.max_interval)
                pending.append(rec)
            except queue.Empty:
                pass

            now = time.time()
            if pending and (
                len(pending) >= self.batch_size
                or now - last_flush >= self.max_interval
                or (self._stop.is_set() and pending)
            ):
                # Write batch with a single write call.
                lines = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in pending)
                self._f.write(lines)
                self._f.flush()
                fsync_counter += 1
                if fsync_counter >= self.fsync_every_n:
                    try:
                        os.fsync(self._f.fileno())
                    except OSError:
                        # If fsync fails, continue; we don't want to lose data
                        # but this should be rare in normal environments.
                        pass
                    fsync_counter = 0
                pending = []
                last_flush = now

        # Final flush on shutdown
        if pending:
            self._f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in pending))
        try:
            self._f.flush()
            os.fsync(self._f.fileno())
        except Exception:
            pass
        try:
            self._f.close()
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        if self._started:
            # Wait for thread to finish flushing remaining records.
            self._thread.join(timeout=2.0)


def _get_writer(log_path: str) -> _LogWriter:
    with _writers_lock:
        w = _writers.get(log_path)
        if w is None:
            w = _LogWriter(log_path)
            _writers[log_path] = w
        return w


def handle_request(job_id: int, payload: str, log_path: str) -> Dict[str, Any]:
    """Optimized request handler.

    Preserves the same return value as baseline but defers and batches log I/O
    to a background thread in order to drastically reduce per‑request syscalls
    and lock contention under concurrency.
    """
    start = time.time()

    result = _slow_compute(payload)
    processed_at = time.time()

    record = {
        "job_id": job_id,
        "payload_hash": hash(payload),
        "length": result["length"],
        "vowels": result["vowels"],
        "start_ts": start,
        "processed_ts": processed_at,
    }

    # Enqueue the record to the background writer and return immediately.
    # This removes the global lock and per‑request open/flush/fsync overhead.
    writer = _get_writer(log_path)
    writer.enqueue(record)

    # Preserve the small randomized sleep present in baseline to keep per‑job
    # timing behavior similar (the baseline adds this after logging).
    time.sleep(0.0005 + random.random() * 0.0005)

    return record
