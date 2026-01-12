import json
import os
import random
import string
import threading
import time
from typing import Any, Dict

# Simple global lock to serialize log writes in the baseline implementation.
# This is intentionally coarse‑grained and inefficient.
_log_lock = threading.Lock()


def _slow_compute(payload: str) -> Dict[str, Any]:
    """Simulate some CPU work on the payload.

    The details are not important for the optimization task; it is only meant
    to be a deterministic transformation so that outputs can be compared.
    """
    # Simple deterministic "work": count characters and vowels with a tiny delay.
    counts = {
        "length": len(payload),
        "vowels": sum(1 for c in payload.lower() if c in "aeiou"),
    }
    # Add a small artificial delay to exaggerate baseline cost.
    time.sleep(0.0005)
    return counts


def _open_log_file(log_path: str):
    # Open in append mode for every call; this is intentionally inefficient.
    # The optimized implementation is expected to find better ways to handle I/O
    # without changing the semantics of what is logged.
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    return open(log_path, "a", encoding="utf-8")


def handle_request(job_id: int, payload: str, log_path: str) -> Dict[str, Any]:
    """Baseline request handler.

    This function is called by runner.py for each job. It performs some
    deterministic computation and writes a structured log record to disk.

    Arguments:
        job_id: Integer identifier of the job.
        payload: String payload for this job.
        log_path: Path to the log file where structured JSON lines are written.

    Returns:
        A small dictionary summarizing the work done. The exact structure is
        not important as long as it is deterministic for a given input.
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

    # Very naive logging: open + write + flush under a global lock
    # for every single request.
    with _log_lock:
        f = _open_log_file(log_path)
        try:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
        finally:
            f.close()

    # Add another tiny sleep to amplify the cost of per‑request I/O.
    time.sleep(0.0005 + random.random() * 0.0005)

    return record


def generate_payload(seed: int, length: int = 128) -> str:
    """Deterministically generate a pseudo‑random payload string.

    The runner may use this helper to produce reproducible job payloads.
    """
    rnd = random.Random(seed)
    alphabet = string.ascii_letters + string.digits + " "
    return "".join(rnd.choice(alphabet) for _ in range(length))
