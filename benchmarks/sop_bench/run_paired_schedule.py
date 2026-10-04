"""ABOUTME: Run sequential, balanced SOP-Bench arm/domain blocks with one secret load.
ABOUTME: Saves the schedule before inference and tees output to a redacted driver log.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TextIO

from benchmarks.sop_bench.experiment_credentials import load_pass_credentials
from benchmarks.sop_bench.paired_experiment import (
    DEFAULT_EXPERIMENT,
    DOMAINS,
    scrub,
    write_json_exclusive,
)
from benchmarks.sop_bench.paired_experiment import (
    main_async as run_experiment,
)

PROFILES = ("baseline", "hybrid-jev", "hybrid-local-jev")
PROFILE_CHOICES = (
    *PROFILES,
    "hybrid-local",
    "hybrid-local-12b",
    "baseline-low",
    "hybrid-local-low",
    "hybrid-local-12b-low",
)
PASS_ENTRIES = {
    "NEBIUS_API_KEY": "NEBIUS/TOKEN_FACTORY_API_KEY",
    "TYPESAFE_API_KEY": "API_KEYS/TYPESAFE_AI_JEV_API_KEY",
}


class Tee:
    """Forward output to its original stream and one shared, redacted log."""

    def __init__(self, original: TextIO, log: TextIO, secrets: list[str], lock: threading.Lock):
        self.original, self.log, self.secrets, self.lock = original, log, secrets, lock

    def write(self, text: str) -> int:
        clean = scrub(text, self.secrets)
        with self.lock:
            self.original.write(clean)
            self.log.write(clean)
            self.original.flush()
            self.log.flush()
        return len(text)

    def flush(self) -> None:
        with self.lock:
            self.original.flush()
            self.log.flush()

    def isatty(self) -> bool:
        return False


def build_schedule(
    run_id: str,
    domains: Sequence[str],
    profiles: Sequence[str],
    split: str,
    limit: int | None,
    jobs: int = 1,
) -> dict:
    """Rotate arm order across domains; blocks are strictly sequential."""
    blocks = []
    for index, domain in enumerate(domains):
        offset = index % len(profiles)
        ordered = [*profiles[offset:], *profiles[:offset]]
        block_id = f"{run_id}--{domain}"
        for profile in ordered:
            blocks.append({"domain": domain, "profile": profile, "run_id": block_id})
    return {
        "schema_version": 1,
        "run_id": run_id,
        "split": split,
        "limit_per_domain": limit,
        "jobs": jobs,
        "concurrency_between_arms": 0,
        "concurrency_model": "single_event_loop_semaphore",
        "profiles": list(profiles),
        "domains": list(domains),
        "blocks": blocks,
        "created_at": datetime.now(UTC).isoformat(),
    }


async def main_async(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument("--limit", type=int, help="Cases per domain; defaults to 2 for dev only")
    parser.add_argument("--jobs", type=int, choices=(1, 2, 4), default=1)
    parser.add_argument("--profiles", nargs="+", choices=PROFILE_CHOICES, default=list(PROFILES))
    parser.add_argument("--domains", nargs="+", choices=DOMAINS, default=list(DOMAINS))
    args = parser.parse_args(argv)
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.run_id):
        parser.error("run-id must contain only letters, numbers, _ or -")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    if len(set(args.domains)) != len(args.domains) or len(set(args.profiles)) != len(args.profiles):
        parser.error("domains and profiles must not contain duplicates")
    limit = args.limit if args.limit is not None else (2 if args.split == "dev" else None)
    schedule = build_schedule(
        args.run_id, args.domains, args.profiles, args.split, limit, args.jobs
    )
    driver_dir = DEFAULT_EXPERIMENT / "runs" / args.run_id
    driver_dir.mkdir(parents=True, exist_ok=False)
    write_json_exclusive(driver_dir / "schedule.json", schedule)
    secrets: list[str] = []
    lock = threading.Lock()
    with (
        (driver_dir / "driver.log").open("x") as log,
        contextlib.redirect_stdout(Tee(sys.stdout, log, secrets, lock)),
        contextlib.redirect_stderr(Tee(sys.stderr, log, secrets, lock)),
    ):
        try:
            credentials = load_pass_credentials(PASS_ENTRIES)
            secrets.extend(credentials.values())
            for block in schedule["blocks"]:
                print(f"Starting {block['run_id']} / {block['profile']}", flush=True)
                command = [
                    "--config",
                    str(DEFAULT_EXPERIMENT / "configs" / f"{block['profile']}.yaml"),
                    "--arm",
                    block["profile"],
                    "--split",
                    args.split,
                    "--domains",
                    block["domain"],
                    "--run-id",
                    block["run_id"],
                    "--jobs",
                    str(args.jobs),
                ]
                if limit is not None:
                    command += ["--limit", str(limit)]
                status = await run_experiment(command, credentials=credentials)
                if status:
                    raise RuntimeError(f"Experiment block exited with status {status}")
            write_json_exclusive(
                driver_dir / "completed.json",
                {
                    "completed_at": datetime.now(UTC).isoformat(),
                    "blocks": len(schedule["blocks"]),
                },
            )
        except Exception as exc:
            message = scrub(f"{type(exc).__name__}: {exc}", secrets)
            print(f"Driver failed: {message}", file=sys.stderr)
            write_json_exclusive(driver_dir / "failed.json", {"error": message})
            return 1
    print(json.dumps({"schedule": str(driver_dir / "schedule.json"), "status": "completed"}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Keep all sequential blocks and concurrent cases on the same event loop."""
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
