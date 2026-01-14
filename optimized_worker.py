import json
import os
import random
import string
import threading
import time
import atexit
from typing import Any, Dict
from collections import deque
from queue import Queue

# Optimizations implemented:
# 1. Batch writes: accumulate multiple log records in memory before writing
# 2. Background flusher: dedicated thread that periodically flushes batches
# 3. Lock-free queuing: minimize contention by using thread-safe queue
# 4. Keep file open: reduce open/close overhead
# 5. Batch fsync: sync multiple writes at once instead of per-request


class BatchedLogWriter:
    """Thread-safe batched log writer with background flushing."""
    
    def __init__(self, log_path: str, batch_size: int = 100, flush_interval: float = 0.05):
        self.log_path = log_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        
        # Thread-safe queue for log records
        self.queue = Queue()
        
        # Background flusher thread
        self.running = True
        self.flusher_thread = threading.Thread(target=self._flusher_loop, daemon=True)
        self.flusher_thread.start()
        
        # Ensure we flush remaining records on exit
        atexit.register(self.shutdown)
    
    def write_record(self, record: Dict[str, Any]) -> None:
        """Add a record to the write queue."""
        self.queue.put(record)
    
    def _flusher_loop(self) -> None:
        """Background thread that periodically flushes batched records."""
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        
        # Keep the file open for the duration
        f = open(self.log_path, "a", encoding="utf-8")
        
        try:
            batch = []
            last_flush = time.time()
            
            while self.running:
                # Collect records into a batch
                try:
                    # Use timeout to periodically check running flag and flush interval
                    record = self.queue.get(timeout=0.01)
                    batch.append(record)
                except:
                    # Queue empty or timeout
                    pass
                
                # Flush if batch is full or enough time has passed
                now = time.time()
                should_flush = (
                    len(batch) >= self.batch_size or 
                    (batch and (now - last_flush) >= self.flush_interval)
                )
                
                if should_flush and batch:
                    self._flush_batch(f, batch)
                    batch = []
                    last_flush = now
            
            # Final flush of remaining records
            while not self.queue.empty():
                try:
                    batch.append(self.queue.get_nowait())
                except:
                    break
            
            if batch:
                self._flush_batch(f, batch)
        
        finally:
            f.close()
    
    def _flush_batch(self, f, batch: list) -> None:
        """Write a batch of records to disk."""
        if not batch:
            return
        
        # Write all records in the batch
        for record in batch:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        
        # Single flush and fsync for the entire batch
        f.flush()
        os.fsync(f.fileno())
    
    def shutdown(self) -> None:
        """Stop the flusher thread and ensure all records are written."""
        if self.running:
            self.running = False
            if self.flusher_thread.is_alive():
                self.flusher_thread.join(timeout=5.0)


# Global writer instance per log file
_writers: Dict[str, BatchedLogWriter] = {}
_writers_lock = threading.Lock()


def _get_writer(log_path: str) -> BatchedLogWriter:
    """Get or create a batched writer for the given log path."""
    with _writers_lock:
        if log_path not in _writers:
            _writers[log_path] = BatchedLogWriter(log_path)
        return _writers[log_path]


def _slow_compute(payload: str) -> Dict[str, Any]:
    """Simulate some CPU work on the payload.
    
    This is identical to the baseline implementation.
    """
    counts = {
        "length": len(payload),
        "vowels": sum(1 for c in payload.lower() if c in "aeiou"),
    }
    time.sleep(0.0005)
    return counts


def handle_request(job_id: int, payload: str, log_path: str) -> Dict[str, Any]:
    """Optimized request handler.
    
    This function maintains the same interface and behavior as the baseline,
    but uses batched I/O to improve performance under concurrent load.
    
    Arguments:
        job_id: Integer identifier of the job.
        payload: String payload for this job.
        log_path: Path to the log file where structured JSON lines are written.
    
    Returns:
        A small dictionary summarizing the work done, identical to baseline.
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
    
    # Queue the record for batched writing instead of blocking on I/O
    writer = _get_writer(log_path)
    writer.write_record(record)
    
    # Keep the same artificial delay as baseline
    time.sleep(0.0005 + random.random() * 0.0005)
    
    return record


def generate_payload(seed: int, length: int = 128) -> str:
    """Deterministically generate a pseudo‑random payload string.
    
    This is identical to the baseline implementation.
    """
    rnd = random.Random(seed)
    alphabet = string.ascii_letters + string.digits + " "
    return "".join(rnd.choice(alphabet) for _ in range(length))


def shutdown_all_writers() -> None:
    """Ensure all writers are properly shut down and flushed.
    
    Call this at the end of a test run to ensure all logs are written.
    """
    with _writers_lock:
        for writer in _writers.values():
            writer.shutdown()
        _writers.clear()
