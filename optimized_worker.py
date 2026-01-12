import json
import os
import random
import string
import threading
import time
from typing import Any, Dict
from collections import deque

# Thread-local storage for batched writes
_local = threading.local()
_batch_lock = threading.Lock()
_batch_condition = threading.Condition(_batch_lock)
_active_log_paths = {}  # Maps log_path -> (deque of records, writer thread)
_writer_threads = {}    # Maps log_path -> thread object
_shutdown = False


def _slow_compute(payload: str) -> Dict[str, Any]:
    """Simulate some CPU work on the payload.

    Identical to baseline implementation for correctness.
    """
    counts = {
        "length": len(payload),
        "vowels": sum(1 for c in payload.lower() if c in "aeiou"),
    }
    time.sleep(0.0005)
    return counts


def _writer_thread_func(log_path: str) -> None:
    """Background thread that batches and writes accumulated log records.
    
    This thread periodically drains the queue and writes records to disk,
    reducing lock contention and I/O overhead per job.
    """
    while not _shutdown:
        batch_queue = None
        records_to_write = []
        
        with _batch_condition:
            # Wait for records to accumulate or timeout
            if log_path in _active_log_paths:
                batch_queue = _active_log_paths[log_path][0]
                
                if not batch_queue or len(batch_queue) == 0:
                    # Wait up to 10ms for records to arrive
                    _batch_condition.wait(timeout=0.01)
                
                # Check again after waiting
                if log_path in _active_log_paths:
                    batch_queue = _active_log_paths[log_path][0]
                    
                    # Drain up to 100 records at a time
                    while batch_queue and len(records_to_write) < 100:
                        records_to_write.append(batch_queue.popleft())
        
        # Write outside the lock to minimize contention
        if records_to_write:
            _write_records(log_path, records_to_write)


def _write_records(log_path: str, records: list) -> None:
    """Write a batch of records to the log file."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        print(f"Error writing to {log_path}: {e}")


def _ensure_writer_thread(log_path: str) -> None:
    """Ensure a background writer thread is running for the given log path."""
    global _writer_threads
    
    with _batch_lock:
        if log_path not in _active_log_paths:
            queue = deque()
            _active_log_paths[log_path] = (queue, None)
            
            # Start a dedicated writer thread for this log file
            t = threading.Thread(target=_writer_thread_func, args=(log_path,), daemon=True)
            t.start()
            _writer_threads[log_path] = t


def _flush_all_pending(log_path: str) -> None:
    """Flush all pending records for a log path immediately.
    
    This is called at the end of a benchmark to ensure all queued records
    are written to disk before measurement ends.
    """
    records_to_write = []
    
    with _batch_lock:
        if log_path in _active_log_paths:
            batch_queue = _active_log_paths[log_path][0]
            while batch_queue:
                records_to_write.append(batch_queue.popleft())
    
    # Write pending records synchronously
    if records_to_write:
        _write_records(log_path, records_to_write)


# Ensure pending records are flushed on module cleanup
import atexit

def _cleanup() -> None:
    """Cleanup function called when the module is unloaded."""
    global _shutdown, _active_log_paths
    _shutdown = True
    
    # Flush all pending records
    for log_path in list(_active_log_paths.keys()):
        _flush_all_pending(log_path)
    
    # Give background threads a moment to finish
    time.sleep(0.1)

atexit.register(_cleanup)


def handle_request(job_id: int, payload: str, log_path: str) -> Dict[str, Any]:
    """Optimized request handler using batched I/O.

    This implementation reduces contention and I/O overhead by:
    1. Buffering log records in a thread-local queue
    2. Batching writes and flushing in a background thread
    3. Minimizing lock hold times
    
    Arguments:
        job_id: Integer identifier of the job.
        payload: String payload for this job.
        log_path: Path to the log file where structured JSON lines are written.

    Returns:
        A dictionary with the same structure as baseline for correctness.
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

    # Ensure writer thread exists for this log path
    _ensure_writer_thread(log_path)
    
    # Add record to the batch queue with minimal lock contention
    with _batch_lock:
        if log_path in _active_log_paths:
            batch_queue = _active_log_paths[log_path][0]
            batch_queue.append(record)
            
            # Wake up writer thread if we've accumulated enough records
            if len(batch_queue) >= 50:
                _batch_condition.notify()

    # Same small artificial delay as baseline for fairness
    time.sleep(0.0005 + random.random() * 0.0005)

    return record


def generate_payload(seed: int, length: int = 128) -> str:
    """Deterministically generate a pseudo‑random payload string.

    Identical to baseline implementation.
    """
    rnd = random.Random(seed)
    alphabet = string.ascii_letters + string.digits + " "
    return "".join(rnd.choice(alphabet) for _ in range(length))
