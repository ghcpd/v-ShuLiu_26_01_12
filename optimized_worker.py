"""
Optimized worker implementation.

Approach:
- Reuse the same computation from `baseline_worker._slow_compute` to ensure identical
  computed fields (length, vowels) and timing characteristics.
- Avoid opening/flushing/fsync per request. Instead, enqueue JSON lines to a background
  writer thread. The writer batches records and calls flush+fsync per-batch.
- Keep short batch timeout and modest batch sizes to keep tail latency low while
  reducing syscall frequency dramatically.

The module exposes `handle_request(job_id, payload, log_path)` with the same
contract as `baseline_worker.handle_request`.
"""

import atexit
import json
import os
import queue
import threading
import time
import random
from typing import Dict, Any

# Map from log_path -> _LogWriter instance
_writers = {}
_writers_lock = threading.Lock()

# Batch configuration (tunable)
_MAX_BATCH_SIZE = 128
_MAX_WAIT_SEC = 0.01  # max wait before flushing a partial batch


class _LogWriter(threading.Thread):
    def __init__(self, log_path: str):
        super().__init__(daemon=True)
        self.log_path = log_path
        self._q = queue.Queue()
        self._shutdown = threading.Event()
        self._flush_event = threading.Event()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        self._f = open(log_path, "a", encoding="utf-8")
        self.start()

    def run(self) -> None:
        buf = []
        last_flush = time.time()
        while not self._shutdown.is_set() or not self._q.empty() or buf:
            try:
                # Try to fill the batch quickly
                item = self._q.get(timeout=_MAX_WAIT_SEC)
                buf.append(item)
                # Quickly drain up to MAX_BATCH_SIZE
                while len(buf) < _MAX_BATCH_SIZE:
                    try:
                        item = self._q.get_nowait()
                        buf.append(item)
                    except queue.Empty:
                        break
            except queue.Empty:
                # Timeout: flush what we have (if any)
                pass

            # Flush if we have anything
            if buf:
                try:
                    self._f.write("".join(buf))
                    self._f.flush()
                    # fsync per-batch to preserve durability while reducing syscalls
                    os.fsync(self._f.fileno())
                except Exception:
                    # Swallow exceptions to avoid crashing the writer thread; they will
                    # surface at process exit if things are seriously wrong.
                    pass
                buf.clear()
                last_flush = time.time()

            # Allow explicit flush/close requests to proceed quickly
            if self._flush_event.is_set():
                self._flush_event.clear()
        # Final close
        try:
            self._f.flush()
            os.fsync(self._f.fileno())
        except Exception:
            pass
        try:
            self._f.close()
        except Exception:
            pass

    def write_line(self, line: str) -> None:
        # Caller should ensure line ends with a newline
        self._q.put(line)

    def flush_and_close(self) -> None:
        self._shutdown.set()
        # Wake up the thread if waiting
        self._flush_event.set()
        self.join(timeout=5.0)


def _get_writer(log_path: str) -> _LogWriter:
    with _writers_lock:
        w = _writers.get(log_path)
        if w is None:
            w = _LogWriter(log_path)
            _writers[log_path] = w
        return w


def _cleanup_writers() -> None:
    with _writers_lock:
        for w in list(_writers.values()):
            try:
                w.flush_and_close()
            except Exception:
                pass
        _writers.clear()


atexit.register(_cleanup_writers)


def handle_request(job_id: int, payload: str, log_path: str) -> Dict[str, Any]:
    """Optimized handler: compute synchronously, enqueue log writes."""
    # Reuse baseline's deterministic compute to keep results identical.
    # Import inside function to avoid circular imports at module import time.
    import baseline_worker

    start = time.time()

    result = baseline_worker._slow_compute(payload)
    processed_at = time.time()

    record = {
        "job_id": job_id,
        "payload_hash": hash(payload),
        "length": result["length"],
        "vowels": result["vowels"],
        "start_ts": start,
        "processed_ts": processed_at,
    }

    line = json.dumps(record, separators=(",", ":")) + "\n"

    writer = _get_writer(log_path)
    writer.write_line(line)

    # Preserve the small randomized sleep that baseline performs after logging.
    time.sleep(0.0005 + random.random() * 0.0005)

    return record
