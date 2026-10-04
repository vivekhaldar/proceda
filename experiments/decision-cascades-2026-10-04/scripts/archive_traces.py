"""ABOUTME: Archive finalized experiment run groups without altering any source bytes.
ABOUTME: Scans credentials in memory, verifies immutable archives, and tests offline restoration.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[3]
EXPERIMENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def group_files(experiment: Path, prefix: str, allow_aborted: bool = False) -> tuple[list, str]:
    """Require completed scheduled runs or complete legacy arms; aborts need explicit opt-in."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prefix) or "--" in prefix:
        raise ValueError("Expected a top-level schedule prefix")
    runs = experiment / "runs"
    top = runs / prefix
    if not top.is_dir() or top.is_symlink():
        raise ValueError("Missing run-group directory")
    folders = [top, *sorted(runs.glob(prefix + "--*"))]
    files = []
    for folder in folders:
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError("Run groups must contain ordinary directories")
        for path in sorted(folder.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Symlinks and special files are not archived")
            if path.is_file():
                files.append(path)
    aborted = (top / "aborted.json").is_file()
    if allow_aborted and not aborted:
        raise ValueError("Explicitly aborted groups require an aborted.json marker")
    if aborted:
        read_json(top / "aborted.json")
        if not allow_aborted:
            raise ValueError("Aborted group requires explicit --allow-aborted")
        return files, "aborted_partial_preserved"
    schedule = top / "schedule.json"
    if schedule.exists():
        if not (top / "completed.json").is_file():
            raise ValueError("Scheduled group lacks its completion marker; may still be live")
        read_json(top / "completed.json")
        for block in read_json(schedule)["blocks"]:
            if block["run_id"] != prefix and not block["run_id"].startswith(prefix + "--"):
                raise ValueError("Schedule block escapes the selected group")
            if not (runs / block["run_id"] / block["profile"] / "run.json").is_file():
                raise ValueError("A scheduled arm has not started")
    manifests = [p for p in files if p.name == "run.json"]
    if not manifests:
        raise ValueError("No arm manifests in group")
    for manifest in manifests:
        arm = read_json(manifest)
        summary = manifest.parent / "summary.json"
        if not summary.is_file():
            raise ValueError("Arm lacks its final summary")
        planned = sum(len(ids) for ids in arm["task_ids"].values())
        if read_json(summary)["tasks"] != planned:
            raise ValueError("Arm summary has an incomplete task count")
        for domain, ids in arm["task_ids"].items():
            for task_id in ids:
                terminal = manifest.parent / domain / task_id / "terminal.json"
                if not terminal.is_file() or read_json(terminal)["status"] not in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    raise ValueError("A planned case has not terminated")
    if any(p.name == "started.json" and not (p.parent / "terminal.json").is_file() for p in files):
        raise ValueError("Partial case in an otherwise completed group")
    return files, "completed"


def safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or not name.startswith("runs/"):
        raise ValueError("Unsafe archive member path")


def verify_archive(archive: Path, manifest: dict, extract_to: Path | None = None) -> dict:
    """Verify archive and every ordinary-file member, optionally restoring into a fresh root."""
    if archive.stat().st_size != manifest["archive"]["size_bytes"]:
        raise ValueError("Archive size mismatch")
    if digest(archive.read_bytes()) != manifest["archive"]["sha256"]:
        raise ValueError("Archive hash mismatch")
    expected = {m["path"]: m for m in manifest["members"]}
    if len(expected) != len(manifest["members"]):
        raise ValueError("Duplicate manifest member")
    seen = set()
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            safe_member(member.name)
            if not member.isfile() or member.name in seen or member.name not in expected:
                raise ValueError("Unexpected, repeated, or non-file archive member")
            source = stream.extractfile(member)
            if source is None:
                raise ValueError("Missing member contents")
            data = source.read()
            target = expected[member.name]
            if len(data) != target["size_bytes"] or digest(data) != target["sha256"]:
                raise ValueError("Member hash or size mismatch")
            seen.add(member.name)
            if extract_to is not None:
                destination = extract_to / member.name
                if not destination.resolve().is_relative_to(extract_to.resolve()):
                    raise ValueError("Extraction path escaped destination")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    output.write(data)
    if seen != set(expected):
        raise ValueError("Missing archive members")
    return {"archive_sha256_verified": True, "members_verified": len(seen)}


def archive_group(experiment: Path, prefix: str, credentials: list[str], allow_aborted=False):
    """Scan source bytes, publish one immutable archive/manifest, then verify publication."""
    archive_dir = experiment / "archives"
    archive = archive_dir / f"{prefix}.tar.gz"
    manifest_path = archive_dir / f"{prefix}.manifest.json"
    if archive.exists() or manifest_path.exists():
        raise FileExistsError("Archives and manifests are immutable")
    files, status = group_files(experiment, prefix, allow_aborted)
    members = []
    matches = 0
    secrets = [secret.encode() for secret in credentials if secret]
    if not secrets:
        raise ValueError("Credential scan needs known nonempty credentials")
    for path in files:
        data = path.read_bytes()
        matches += int(any(secret in data for secret in secrets))
        members.append(
            {
                "path": path.relative_to(experiment).as_posix(),
                "size_bytes": len(data),
                "sha256": digest(data),
            }
        )
    print(f"Credential scan {'FAIL' if matches else 'PASS'}: files={len(files)}, matches={matches}")
    if matches:
        raise ValueError("Credential scan failed; nothing published")
    archive_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".archive-", dir=archive_dir) as scratch:
        temporary = Path(scratch) / "archive.tar.gz"
        with tarfile.open(temporary, "w:gz") as stream:
            for path, record in zip(files, members, strict=True):
                data = path.read_bytes()
                if digest(data) != record["sha256"]:
                    raise ValueError("Source changed after scan; refusing archive")
                info = tarfile.TarInfo(record["path"])
                info.size, info.mode, info.mtime = len(data), 0o600, 0
                stream.addfile(info, io.BytesIO(data))
        current_files, current_status = group_files(experiment, prefix, allow_aborted)
        if current_files != files or current_status != status:
            raise ValueError("Run group changed while archiving")
        manifest = {
            "schema_version": 1,
            "group": prefix,
            "group_status": status,
            "created_at": datetime.now(UTC).isoformat(),
            "archiver_git_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
            ).strip(),
            "archiver_script_sha256": digest(Path(__file__).read_bytes()),
            "credential_scan": {
                "passed": True,
                "credentials_checked": len(secrets),
                "files_checked": len(files),
                "values_saved": False,
            },
            "archive": {
                "file": archive.name,
                "sha256": digest(temporary.read_bytes()),
                "size_bytes": temporary.stat().st_size,
            },
            "members": members,
        }
        verify_archive(temporary, manifest)
        encoded = Path(scratch) / "manifest.json"
        encoded.write_text(json.dumps(manifest, indent=2) + "\n")
        os.link(temporary, archive)  # Exclusive publication; never replace existing artifacts.
        os.link(encoded, manifest_path)
    result = verify_archive(archive, manifest)
    print(f"Archive verification PASS: files={result['members_verified']}")
    return manifest


def validate_restoration(experiment: Path, prefix: str) -> dict:
    """Compare selected offline counts/costs on original and temporary restored dev data."""
    from benchmarks.sop_bench.analyze_paired import analyze

    archive_dir = experiment / "archives"
    output = archive_dir / f"{prefix}.restoration-validation.json"
    if output.exists():
        raise FileExistsError("Validation artifacts are immutable")
    manifest = read_json(archive_dir / f"{prefix}.manifest.json")
    if manifest["group_status"] != "completed":
        raise ValueError("Restoration sample must be a completed group")

    def summaries(root: Path) -> dict:
        baseline = sorted((root / "runs").glob(prefix + "--*/baseline-low"))
        candidate = sorted((root / "runs").glob(prefix + "--*/hybrid-local-low"))
        if not baseline or not candidate:
            raise ValueError("Sample needs baseline-low and hybrid-local-low arms")
        report = analyze(baseline, candidate)
        return {
            arm: {
                key: report[arm][key]
                for key in ("planned_cases", "terminal_cases", "correct_cases", "accounting")
            }
            for arm in ("baseline", "candidate")
        }

    original = summaries(experiment)
    with tempfile.TemporaryDirectory(prefix="proceda-trace-restore-") as temp:
        checks = verify_archive(archive_dir / manifest["archive"]["file"], manifest, Path(temp))
        restored = summaries(Path(temp))
    if original != restored:
        raise ValueError("Restored offline counts/costs differ")
    result = {
        "group": prefix,
        "passed": True,
        "temporary_extraction_removed": True,
        "archive_checks": checks,
        "original": original,
        "restored": restored,
        "compared": "Planned/terminal/correct counts and complete accounting dictionaries",
        "inference_performed": False,
    }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print("Restoration analysis PASS: comparisons=2")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="+")
    parser.add_argument("--allow-aborted", action="append", default=[])
    parser.add_argument("--validate-sample")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            for prefix in args.prefix:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
                    raise ValueError("Invalid group name")
                manifest = read_json(EXPERIMENT / "archives" / f"{prefix}.manifest.json")
                result = verify_archive(EXPERIMENT / "archives" / f"{prefix}.tar.gz", manifest)
                print(f"Archive verification PASS: files={result['members_verified']}")
        else:
            from benchmarks.sop_bench.experiment_credentials import load_pass_credentials

            secrets = list(
                load_pass_credentials(
                    {
                        "NEBIUS_API_KEY": "NEBIUS/TOKEN_FACTORY_API_KEY",
                        "TYPESAFE_API_KEY": "API_KEYS/TYPESAFE_AI_JEV_API_KEY",
                    }
                ).values()
            )
            for prefix in args.prefix:
                archive_group(EXPERIMENT, prefix, secrets, prefix in args.allow_aborted)
        if args.validate_sample:
            validate_restoration(EXPERIMENT, args.validate_sample)
        return 0
    except Exception as error:
        # Never print provider credentials, matching content, or raw exception messages.
        print(f"Archive operation FAIL: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
