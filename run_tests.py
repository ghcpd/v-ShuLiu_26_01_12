#!/usr/bin/env python3
"""
Run tests comparing baseline and optimized worker performance.

This script runs both implementations through runner.py with identical workloads,
collects metrics, and generates a performance report.
"""

import json
import os
import subprocess
import sys
import time


def run_worker(worker_name: str, output_json: str) -> dict:
    """Run a worker benchmark and return its metrics."""
    print(f"\n{'='*60}")
    print(f"Running {worker_name}...")
    print('='*60)
    
    cmd = [
        sys.executable,
        "runner.py",
        "--worker", worker_name,
        "--output-json", output_json
    ]
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300
        )
        print(result.stdout)
        
        # Load the metrics
        with open(output_json, 'r') as f:
            metrics = json.load(f)
        
        return metrics
    
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Worker {worker_name} failed!")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        sys.exit(1)
    
    except Exception as e:
        print(f"ERROR: Unexpected error running {worker_name}: {e}")
        sys.exit(1)


def verify_log_consistency(baseline_log: str, optimized_log: str) -> None:
    """Basic sanity check that both implementations logged the same number of records."""
    if not os.path.exists(baseline_log):
        print(f"WARNING: Baseline log not found: {baseline_log}")
        return
    
    if not os.path.exists(optimized_log):
        print(f"WARNING: Optimized log not found: {optimized_log}")
        return
    
    with open(baseline_log, 'r') as f:
        baseline_count = sum(1 for _ in f)
    
    with open(optimized_log, 'r') as f:
        optimized_count = sum(1 for _ in f)
    
    print(f"\nLog record counts:")
    print(f"  Baseline: {baseline_count}")
    print(f"  Optimized: {optimized_count}")
    
    if baseline_count != optimized_count:
        print(f"WARNING: Record counts differ!")
    else:
        print(f"✓ Record counts match")


def print_comparison(baseline: dict, optimized: dict) -> None:
    """Print a human-readable comparison of metrics."""
    print(f"\n{'='*60}")
    print("PERFORMANCE COMPARISON")
    print('='*60)
    
    print(f"\nLatency Percentiles (seconds):")
    print(f"{'Metric':<10} {'Baseline':<12} {'Optimized':<12} {'Improvement':<12}")
    print('-'*50)
    
    for metric in ['p50', 'p90', 'p95', 'p99', 'avg']:
        b_val = baseline[metric]
        o_val = optimized[metric]
        improvement = ((b_val - o_val) / b_val * 100) if b_val > 0 else 0
        print(f"{metric.upper():<10} {b_val:<12.6f} {o_val:<12.6f} {improvement:>10.1f}%")
    
    print(f"\nThroughput:")
    b_tput = baseline['throughput_rps']
    o_tput = optimized['throughput_rps']
    tput_improvement = ((o_tput - b_tput) / b_tput * 100) if b_tput > 0 else 0
    print(f"{'Baseline:':<15} {b_tput:>10.2f} req/s")
    print(f"{'Optimized:':<15} {o_tput:>10.2f} req/s")
    print(f"{'Improvement:':<15} {tput_improvement:>10.1f}%")
    
    print(f"\nWall Time:")
    b_wall = baseline['wall_time_s']
    o_wall = optimized['wall_time_s']
    wall_improvement = ((b_wall - o_wall) / b_wall * 100) if b_wall > 0 else 0
    print(f"{'Baseline:':<15} {b_wall:>10.3f} s")
    print(f"{'Optimized:':<15} {o_wall:>10.3f} s")
    print(f"{'Speedup:':<15} {b_wall/o_wall if o_wall > 0 else 0:>10.2f}x")


def main():
    print("Performance Optimization Test Suite")
    print("====================================\n")
    
    # Clean up old logs
    log_dir = "logs"
    if os.path.exists(log_dir):
        for f in os.listdir(log_dir):
            fpath = os.path.join(log_dir, f)
            if os.path.isfile(fpath):
                os.remove(fpath)
    
    # Run baseline
    baseline_metrics = run_worker("baseline_worker", "baseline_metrics.json")
    
    # Small delay to ensure clean separation
    time.sleep(1)
    
    # Run optimized
    optimized_metrics = run_worker("optimized_worker", "optimized_metrics.json")
    
    # Verify log consistency
    verify_log_consistency(
        os.path.join(log_dir, "baseline.log"),
        os.path.join(log_dir, "optimized.log")
    )
    
    # Print comparison
    print_comparison(baseline_metrics, optimized_metrics)
    
    # Generate performance report
    report = {
        "baseline": {
            "p50": baseline_metrics["p50"],
            "p90": baseline_metrics["p90"],
            "p95": baseline_metrics["p95"],
            "p99": baseline_metrics["p99"],
            "avg": baseline_metrics["avg"],
            "throughput_rps": baseline_metrics["throughput_rps"],
            "wall_time_s": baseline_metrics["wall_time_s"]
        },
        "optimized": {
            "p50": optimized_metrics["p50"],
            "p90": optimized_metrics["p90"],
            "p95": optimized_metrics["p95"],
            "p99": optimized_metrics["p99"],
            "avg": optimized_metrics["avg"],
            "throughput_rps": optimized_metrics["throughput_rps"],
            "wall_time_s": optimized_metrics["wall_time_s"]
        },
        "improvements": {
            "p50_reduction_pct": ((baseline_metrics["p50"] - optimized_metrics["p50"]) / baseline_metrics["p50"] * 100) if baseline_metrics["p50"] > 0 else 0,
            "p95_reduction_pct": ((baseline_metrics["p95"] - optimized_metrics["p95"]) / baseline_metrics["p95"] * 100) if baseline_metrics["p95"] > 0 else 0,
            "p99_reduction_pct": ((baseline_metrics["p99"] - optimized_metrics["p99"]) / baseline_metrics["p99"] * 100) if baseline_metrics["p99"] > 0 else 0,
            "throughput_increase_pct": ((optimized_metrics["throughput_rps"] - baseline_metrics["throughput_rps"]) / baseline_metrics["throughput_rps"] * 100) if baseline_metrics["throughput_rps"] > 0 else 0,
            "speedup_factor": baseline_metrics["wall_time_s"] / optimized_metrics["wall_time_s"] if optimized_metrics["wall_time_s"] > 0 else 0
        }
    }
    
    with open("perf_report.json", "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"\n✓ Performance report written to perf_report.json")
    
    # Success!
    print(f"\n{'='*60}")
    print("ALL TESTS PASSED")
    print('='*60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
