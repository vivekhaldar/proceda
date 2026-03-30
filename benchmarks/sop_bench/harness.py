# ABOUTME: Evaluation harness for running Proceda against SOP-Bench benchmarks.
# ABOUTME: Loads tasks from CSV, runs each through Proceda, scores against ground truth.

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmarks.sop_bench.output_extractor import extract_output
from proceda.agent import Agent
from proceda.config import ProcedaConfig
from proceda.events import CollectorEventSink

BENCHMARKS_DIR = Path(__file__).parent / "domains"
RESULTS_DIR = Path(__file__).parent / "results"

MAX_RETRIES = 2


def load_benchmark_data(domain: str, data_dir: str) -> tuple[list[dict], list[str], list[str]]:
    """Load task data, input columns, and output columns for a domain."""
    domain_path = Path(data_dir) / domain

    # Check for local metadata override in our domain directory first
    local_metadata_path = BENCHMARKS_DIR / domain / "metadata.json"
    metadata_path = domain_path / "metadata.json"
    if local_metadata_path.exists():
        with open(local_metadata_path) as f:
            metadata = json.load(f)
    else:
        with open(metadata_path) as f:
            metadata = json.load(f)
    output_columns = metadata["output_columns"]
    input_columns = metadata.get("input_columns")

    if input_columns is None:
        csv_path = domain_path / "test_set_with_outputs.csv"
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            all_columns = reader.fieldnames or []
        output_set = set(output_columns)
        input_columns = [c for c in all_columns if c not in output_set]

    csv_path = domain_path / "test_set_with_outputs.csv"
    tasks = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tasks.append(dict(row))

    return tasks, input_columns, output_columns


def get_task_id(task: dict, index: int) -> str:
    """Extract task ID from common column names."""
    return (
        task.get("product_id")
        or task.get("patient_id")
        or task.get("content_id")
        or task.get("account_id")
        or task.get("business_id")
        or task.get("video_id")
        or task.get("partner_id")
        or task.get("order_id")
        or task.get("po_number")
        or f"task_{index}"
    )


def compare_decisions(predicted: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Compare predicted vs expected output, case-insensitive with normalization."""
    if not predicted or not expected:
        return False

    for key, expected_val in expected.items():
        pred_val = predicted.get(key)
        if pred_val is None:
            return False
        pred_norm = str(pred_val).lower().strip().replace("-", "_").replace(" ", "_")
        exp_norm = str(expected_val).lower().strip().replace("-", "_").replace(" ", "_")
        if pred_norm == exp_norm:
            continue
        if pred_norm in exp_norm or exp_norm in pred_norm:
            continue
        return False

    return True


def save_trace(
    domain: str,
    task_id: str,
    status: str,
    collector: CollectorEventSink,
    predicted: dict,
    expected: dict,
    run_event_log_path: Path | None,
) -> None:
    """Save full trace for a task to the results/traces directory."""
    traces_dir = RESULTS_DIR / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    trace_path = traces_dir / f"{domain}_{task_id}_{status}.jsonl"
    with open(trace_path, "w") as f:
        f.write(
            json.dumps(
                {
                    "_meta": True,
                    "domain": domain,
                    "task_id": task_id,
                    "status": status,
                    "predicted": predicted,
                    "expected": expected,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            + "\n"
        )
        for event in collector.events:
            f.write(event.to_json() + "\n")

    if run_event_log_path:
        log_path = Path(run_event_log_path)
        if log_path.is_dir():
            events_file = log_path / "events.jsonl"
            if events_file.exists():
                dest = traces_dir / f"{domain}_{task_id}_{status}_proceda.jsonl"
                shutil.copy2(events_file, dest)
        elif log_path.is_file():
            dest = traces_dir / f"{domain}_{task_id}_{status}_proceda.jsonl"
            shutil.copy2(log_path, dest)


def run_single_task(
    task: dict,
    task_index: int,
    total: int,
    domain: str,
    skill_dir: Path,
    config: ProcedaConfig,
    input_columns: list[str],
    output_columns: list[str],
) -> dict[str, Any]:
    """Run a single task with retry logic. Thread-safe."""
    task_id = get_task_id(task, task_index)

    variables = {}
    for col in input_columns:
        val = task.get(col, "")
        variables[col] = str(val) if val is not None else ""

    expected = {}
    for col in output_columns:
        expected[col] = task.get(col, "")

    last_error = None
    for attempt in range(1 + MAX_RETRIES):
        collector = CollectorEventSink()
        start_time = time.time()
        try:
            agent = Agent.from_path(str(skill_dir), config=config)
            result = agent.run(variables=variables, event_sinks=[collector])
            execution_time = time.time() - start_time
            success = True
            error = None
            run_event_log_path = getattr(result, "event_log_path", None)
            break
        except Exception as e:
            execution_time = time.time() - start_time
            last_error = e
            error_str = str(e)
            # Retry on LLM API errors, not on logic errors
            if attempt < MAX_RETRIES and (
                "APIConnectionError" in error_str
                or "RateLimitError" in error_str
                or "APIError" in error_str
                or "list index out of range" in error_str
                or "Timeout" in error_str
            ):
                wait = 2 ** (attempt + 1)
                print(
                    f"\n  [{task_id}] Attempt {attempt + 1} failed: {error_str[:80]}..."
                    f" Retrying in {wait}s",
                    flush=True,
                )
                time.sleep(wait)
                continue
            success = False
            error = error_str
            run_event_log_path = None
            break
    else:
        success = False
        error = str(last_error)
        run_event_log_path = None

    if success:
        predicted = extract_output(collector.events, output_columns)
    else:
        predicted = {}

    is_correct = compare_decisions(predicted, expected) if success else False
    status = "success" if is_correct else ("error" if not success else "fail")

    label = "PASS" if is_correct else "FAIL"
    print(f"[{task_index + 1}/{total}] {task_id}: {label} ({execution_time:.1f}s)", flush=True)
    if not is_correct and predicted:
        for col in output_columns:
            p = predicted.get(col, "<missing>")
            e = expected.get(col, "")
            if str(p).lower().strip() != str(e).lower().strip():
                print(f"  {col}: predicted={p}, expected={e}", flush=True)

    save_trace(domain, task_id, status, collector, predicted, expected, run_event_log_path)

    return {
        "task_id": task_id,
        "success": success,
        "is_correct": is_correct,
        "predicted": predicted,
        "expected": expected,
        "execution_time": execution_time,
        "error": error,
    }


def load_completed_task_ids(domain: str) -> set[str]:
    """Load task IDs from a previous results file for resume support."""
    results_path = RESULTS_DIR / f"{domain}_results.json"
    if not results_path.exists():
        return set()
    try:
        with open(results_path) as f:
            data = json.load(f)
        return {t["task_id"] for t in data.get("tasks", [])}
    except (json.JSONDecodeError, KeyError):
        return set()


def load_previous_results(domain: str) -> list[dict]:
    """Load task results from a previous run for merging."""
    results_path = RESULTS_DIR / f"{domain}_results.json"
    if not results_path.exists():
        return []
    try:
        with open(results_path) as f:
            data = json.load(f)
        return data.get("tasks", [])
    except (json.JSONDecodeError, KeyError):
        return []


def run_evaluation(
    domain: str,
    data_dir: str,
    max_tasks: int | None = None,
    workers: int = 1,
    resume: bool = False,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run Proceda against all tasks in a domain and return metrics."""
    tasks, input_columns, output_columns = load_benchmark_data(domain, data_dir)

    if task_ids is not None:
        allowed = set(task_ids)
        tasks = [t for i, t in enumerate(tasks) if get_task_id(t, i) in allowed]
        if not tasks:
            print("Error: no tasks matched the provided task IDs")
            sys.exit(1)
        print(f"Filtered to {len(tasks)} tasks by task ID")

    if max_tasks is not None:
        tasks = tasks[:max_tasks]

    skill_dir = BENCHMARKS_DIR / domain
    if not (skill_dir / "SKILL.md").exists():
        print(f"Error: {skill_dir / 'SKILL.md'} not found. Run `proceda convert` first.")
        sys.exit(1)

    config_path = skill_dir / "config.yaml"
    if config_path.exists():
        config = ProcedaConfig.load(str(config_path))
    else:
        config = ProcedaConfig.load()

    # Resume: load previously completed tasks and skip them
    previous_results = []
    skip_ids: set[str] = set()
    if resume:
        previous_results = load_previous_results(domain)
        skip_ids = {t["task_id"] for t in previous_results}
        if skip_ids:
            print(f"Resuming: {len(skip_ids)} tasks already completed, skipping them")

    # Build list of tasks to run (with original indices for ID extraction)
    tasks_to_run = []
    for i, task in enumerate(tasks):
        task_id = get_task_id(task, i)
        if task_id not in skip_ids:
            tasks_to_run.append((i, task))

    total = len(tasks)
    print(f"Running {len(tasks_to_run)} tasks ({total} total, {len(skip_ids)} skipped)")
    if workers > 1:
        print(f"Parallelism: {workers} workers")

    results = list(previous_results)  # start with previous results if resuming

    if workers <= 1:
        # Sequential execution
        for i, task in tasks_to_run:
            task_result = run_single_task(
                task, i, total, domain, skill_dir, config, input_columns, output_columns
            )
            results.append(task_result)
    else:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for i, task in tasks_to_run:
                future = executor.submit(
                    run_single_task,
                    task,
                    i,
                    total,
                    domain,
                    skill_dir,
                    config,
                    input_columns,
                    output_columns,
                )
                futures[future] = (i, get_task_id(task, i))

            for future in as_completed(futures):
                try:
                    task_result = future.result()
                    results.append(task_result)
                except Exception as e:
                    idx, tid = futures[future]
                    print(f"[{idx + 1}/{total}] {tid}: ERROR ({e})", flush=True)
                    results.append(
                        {
                            "task_id": tid,
                            "success": False,
                            "is_correct": False,
                            "predicted": {},
                            "expected": {},
                            "execution_time": 0.0,
                            "error": str(e),
                        }
                    )

    # Sort results by task_id for consistent output
    results.sort(key=lambda r: r["task_id"])

    # Calculate metrics
    num_tasks = len(results)
    num_completed = sum(1 for r in results if r["success"])
    num_correct = sum(1 for r in results if r["is_correct"])

    tsr = num_correct / num_tasks if num_tasks > 0 else 0.0
    ecr = num_completed / num_tasks if num_tasks > 0 else 0.0
    c_tsr = num_correct / num_completed if num_completed > 0 else 0.0

    metrics = {
        "domain": domain,
        "num_tasks": num_tasks,
        "num_completed": num_completed,
        "num_correct": num_correct,
        "tsr": tsr,
        "ecr": ecr,
        "c_tsr": c_tsr,
        "timestamp": datetime.now().isoformat(),
    }

    print(f"\n{'=' * 50}")
    print(f"Results for {domain}")
    print(f"{'=' * 50}")
    print(f"Tasks:     {num_tasks}")
    print(f"Completed: {num_completed} (ECR: {ecr:.1%})")
    print(f"Correct:   {num_correct} (TSR: {tsr:.1%})")
    print(f"C-TSR:     {c_tsr:.1%}")
    print(f"{'=' * 50}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / f"{domain}_results.json"
    with open(results_path, "w") as f:
        json.dump({"metrics": metrics, "tasks": results}, f, indent=2)
    print(f"Results saved to {results_path}")

    csv_path = RESULTS_DIR / f"{domain}_detailed.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["task_id", "success", "is_correct", "execution_time", "error"],
        )
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "task_id": r["task_id"],
                    "success": r["success"],
                    "is_correct": r["is_correct"],
                    "execution_time": f"{r['execution_time']:.2f}",
                    "error": r["error"] or "",
                }
            )
    print(f"Detailed CSV saved to {csv_path}")

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="SOP-Bench evaluation harness for Proceda")
    parser.add_argument("--domain", required=True, help="Benchmark domain name")
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / "repos/3p/sop-bench/src/amazon_sop_bench/benchmarks/data"),
        help="Path to SOP-Bench benchmarks/data/",
    )
    parser.add_argument("--max-tasks", type=int, default=None, help="Limit number of tasks")
    parser.add_argument(
        "--workers", type=int, default=1, help="Number of parallel workers (default: 1)"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Skip tasks completed in a previous run"
    )
    parser.add_argument(
        "--task-ids",
        nargs="+",
        default=None,
        help="Run only specific task IDs (e.g. P_13057 PARTNER104)",
    )
    parser.add_argument(
        "--task-ids-file",
        default=None,
        help="File containing task IDs to run (one per line)",
    )
    args = parser.parse_args()

    task_ids = args.task_ids
    if args.task_ids_file:
        with open(args.task_ids_file) as f:
            file_ids = [line.strip() for line in f if line.strip()]
        task_ids = (task_ids or []) + file_ids

    run_evaluation(args.domain, args.data_dir, args.max_tasks, args.workers, args.resume, task_ids)


if __name__ == "__main__":
    main()
