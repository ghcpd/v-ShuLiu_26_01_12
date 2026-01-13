#!/usr/bin/env python3
"""
Unified benchmark script for baseline and optimized worker implementations.

This script runs both implementations under the same workload, collects metrics,
and generates a performance report.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run_worker_benchmark(worker_module: str) -> dict:
    """Run a single worker benchmark using runner.py."""
    cmd = [
        sys.executable,
        "runner.py",
        "--worker", worker_module,
    ]
    
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    
    result = subprocess.run(cmd, capture_output=False, text=True)
    
    if result.returncode != 0:
        print(f"ERROR: Worker {worker_module} failed with return code {result.returncode}")
        return None
    
    # Parse metrics from runner.py output
    # Since runner.py doesn't output JSON directly, we'll run it again with json output
    # Or we can modify the approach: let runner.py write JSON and read it back
    return None


def main() -> int:
    """Main entry point for the test runner."""
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Clean up previous logs
    log_dir = Path("logs")
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir(exist_ok=True)
    
    print("Performance Optimization Benchmark")
    print("="*60)
    
    # We'll extend runner.py to support JSON output by adding an environment variable
    # For now, let's run baseline and optimized and capture metrics
    
    baseline_metrics = None
    optimized_metrics = None
    
    # Run baseline
    print("\n[1/2] Running BASELINE worker...")
    try:
        result = subprocess.run(
            [sys.executable, "runner.py", "--worker", "baseline_worker"],
            capture_output=True,
            text=True,
            timeout=300
        )
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        if result.returncode != 0:
            print(f"ERROR: Baseline benchmark failed (exit code {result.returncode})")
            return 1
        baseline_metrics = parse_metrics(result.stdout)
    except subprocess.TimeoutExpired:
        print("ERROR: Baseline benchmark timed out")
        return 1
    except Exception as e:
        print(f"ERROR: Failed to run baseline: {e}")
        return 1
    
    # Run optimized
    print("\n[2/2] Running OPTIMIZED worker...")
    try:
        result = subprocess.run(
            [sys.executable, "runner.py", "--worker", "optimized_worker"],
            capture_output=True,
            text=True,
            timeout=300
        )
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        if result.returncode != 0:
            print(f"ERROR: Optimized benchmark failed (exit code {result.returncode})")
            return 1
        optimized_metrics = parse_metrics(result.stdout)
    except subprocess.TimeoutExpired:
        print("ERROR: Optimized benchmark timed out")
        return 1
    except Exception as e:
        print(f"ERROR: Failed to run optimized: {e}")
        return 1
    
    # Verify outputs match
    baseline_log = Path("logs/baseline.log")
    optimized_log = Path("logs/optimized.log")
    
    if not baseline_log.exists():
        print(f"ERROR: Baseline log file not found: {baseline_log}")
        return 1
    
    if not optimized_log.exists():
        print(f"ERROR: Optimized log file not found: {optimized_log}")
        return 1
    
    baseline_lines = baseline_log.read_text().strip().split('\n')
    optimized_lines = optimized_log.read_text().strip().split('\n')
    
    baseline_count = len([l for l in baseline_lines if l])
    optimized_count = len([l for l in optimized_lines if l])
    
    print(f"\nLog file verification:")
    print(f"  Baseline log lines: {baseline_count}")
    print(f"  Optimized log lines: {optimized_count}")
    
    if baseline_count != optimized_count:
        print(f"WARNING: Log line counts differ ({baseline_count} vs {optimized_count})")
        print("This may indicate correctness issues.")
    
    # Generate summary report
    print("\n" + "="*60)
    print("PERFORMANCE SUMMARY")
    print("="*60)
    
    if baseline_metrics and optimized_metrics:
        print_comparison(baseline_metrics, optimized_metrics)
        
        # Write JSON report
        report = {
            "baseline": baseline_metrics,
            "optimized": optimized_metrics,
            "comparison": {
                "throughput_improvement": (optimized_metrics["throughput_rps"] / baseline_metrics["throughput_rps"] - 1) * 100 if baseline_metrics["throughput_rps"] > 0 else 0,
                "p50_improvement": (baseline_metrics["p50"] / optimized_metrics["p50"] - 1) * 100 if optimized_metrics["p50"] > 0 else 0,
                "p95_improvement": (baseline_metrics["p95"] / optimized_metrics["p95"] - 1) * 100 if optimized_metrics["p95"] > 0 else 0,
                "p99_improvement": (baseline_metrics["p99"] / optimized_metrics["p99"] - 1) * 100 if optimized_metrics["p99"] > 0 else 0,
            }
        }
        
        with open("perf_report.json", "w") as f:
            json.dump(report, f, indent=2)
        
        print(f"\nJSON report written to perf_report.json")
    else:
        print("ERROR: Could not parse metrics from benchmark output")
        return 1
    
    return 0


def parse_metrics(output: str) -> dict:
    """Parse metrics from runner.py output."""
    lines = output.strip().split('\n')
    metrics = {}
    
    for line in lines:
        if "Worker:" in line:
            parts = line.split(":")
            if len(parts) > 1:
                metrics["worker_module"] = parts[1].strip()
        elif "Requests:" in line:
            # "Requests: 2000 Concurrency: 32"
            parts = line.split()
            for i, part in enumerate(parts):
                if part == "Requests:":
                    metrics["requests"] = int(parts[i+1])
                elif part == "Concurrency:":
                    metrics["concurrency"] = int(parts[i+1])
        elif "Latency p50/p90/p95/p99/avg (s):" in line:
            parts = line.split()
            idx = line.find("(s):") + 4
            values = line[idx:].strip().split()
            if len(values) >= 5:
                metrics["p50"] = float(values[0])
                metrics["p90"] = float(values[1])
                metrics["p95"] = float(values[2])
                metrics["p99"] = float(values[3])
                metrics["avg"] = float(values[4])
        elif "Throughput (req/s):" in line:
            parts = line.split()
            metrics["throughput_rps"] = float(parts[-1])
        elif "Wall time (s):" in line:
            parts = line.split()
            metrics["wall_time_s"] = float(parts[-1])
    
    return metrics if metrics else None


def print_comparison(baseline: dict, optimized: dict) -> None:
    """Print a formatted comparison of baseline vs optimized metrics."""
    print(f"\nMetric                  Baseline        Optimized       Improvement")
    print("-" * 75)
    
    # Throughput
    baseline_thr = baseline.get("throughput_rps", 0)
    optimized_thr = optimized.get("throughput_rps", 0)
    improvement = (optimized_thr / baseline_thr - 1) * 100 if baseline_thr > 0 else 0
    print(f"Throughput (req/s)      {baseline_thr:>12.2f}    {optimized_thr:>12.2f}    {improvement:>+8.1f}%")
    
    # Wall time
    baseline_wall = baseline.get("wall_time_s", 0)
    optimized_wall = optimized.get("wall_time_s", 0)
    improvement = (baseline_wall / optimized_wall - 1) * 100 if optimized_wall > 0 else 0
    print(f"Wall Time (s)           {baseline_wall:>12.3f}    {optimized_wall:>12.3f}    {improvement:>+8.1f}%")
    
    # Latency percentiles
    for p in ["p50", "p90", "p95", "p99", "avg"]:
        baseline_lat = baseline.get(p, 0) * 1000  # Convert to ms
        optimized_lat = optimized.get(p, 0) * 1000
        improvement = (baseline_lat / optimized_lat - 1) * 100 if optimized_lat > 0 else 0
        print(f"Latency {p:>3} (ms)        {baseline_lat:>12.3f}    {optimized_lat:>12.3f}    {improvement:>+8.1f}%")


if __name__ == "__main__":
    sys.exit(main())
