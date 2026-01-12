import argparse
import importlib
import json
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

# Fixed workload parameters. These are part of the contract and must not be changed
# by optimized implementations.
TOTAL_REQUESTS = 2000
WARMUP_REQUESTS = 200
CONCURRENCY = 32
LOG_DIR = "logs"
BASELINE_LOG_FILE = os.path.join(LOG_DIR, "baseline.log")
OPTIMIZED_LOG_FILE = os.path.join(LOG_DIR, "optimized.log")


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    k = (len(values) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


def _run_phase(
    worker_module: Any,
    num_requests: int,
    log_file: str,
) -> Tuple[List[float], float]:
    """Run a batch of requests and return per‑request latencies and total time.

    Each request is executed by calling worker_module.handle_request(job_id, payload, log_file).
    """
    from baseline_worker import generate_payload

    latencies: List[float] = []
    start_wall = time.time()

    def _one_request(i: int) -> None:
        payload = generate_payload(i)
        t0 = time.perf_counter()
        worker_module.handle_request(i, payload, log_file)
        t1 = time.perf_counter()
        latencies.append(t1 - t0)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [executor.submit(_one_request, i) for i in range(num_requests)]
        for f in as_completed(futures):
            # Propagate any exception.
            f.result()

    total_wall = time.time() - start_wall
    return latencies, total_wall


def run_benchmark(worker_module_name: str, log_file: str) -> Dict[str, Any]:
    module = importlib.import_module(worker_module_name)

    # Warm‑up phase (not measured in reported metrics).
    _run_phase(module, WARMUP_REQUESTS, log_file)

    # Measurement phase.
    latencies, wall = _run_phase(module, TOTAL_REQUESTS, log_file)
    latencies.sort()

    if latencies:
        p50 = _percentile(latencies, 50)
        p90 = _percentile(latencies, 90)
        p95 = _percentile(latencies, 95)
        p99 = _percentile(latencies, 99)
        avg = statistics.fmean(latencies)
    else:
        p50 = p90 = p95 = p99 = avg = 0.0

    throughput = TOTAL_REQUESTS / wall if wall > 0 else 0.0

    return {
        "worker_module": worker_module_name,
        "requests": TOTAL_REQUESTS,
        "concurrency": CONCURRENCY,
        "p50": p50,
        "p90": p90,
        "p95": p95,
        "p99": p99,
        "avg": avg,
        "throughput_rps": throughput,
        "wall_time_s": wall,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run baseline or optimized worker benchmark.")
    parser.add_argument(
        "--worker",
        default="baseline_worker",
        help="Python module name that exposes handle_request(job_id, payload, log_path)",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional path to write JSON metrics for this single run.",
    )
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)

    if args.worker == "baseline_worker":
        log_file = BASELINE_LOG_FILE
    else:
        log_file = OPTIMIZED_LOG_FILE

    metrics = run_benchmark(args.worker, log_file)

    print("Worker:", metrics["worker_module"])
    print("Requests:", metrics["requests"], "Concurrency:", metrics["concurrency"])
    print(
        "Latency p50/p90/p95/p99/avg (s):",
        f"{metrics['p50']:.6f}",
        f"{metrics['p90']:.6f}",
        f"{metrics['p95']:.6f}",
        f"{metrics['p99']:.6f}",
        f"{metrics['avg']:.6f}",
    )
    print("Throughput (req/s):", f"{metrics['throughput_rps']:.2f}")
    print("Wall time (s):", f"{metrics['wall_time_s']:.3f}")

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)


if __name__ == "__main__":
    main()
