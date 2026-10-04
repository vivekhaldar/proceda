"""ABOUTME: Preserve explicitly selected, completed phase-two evidence without deleting raw files.
ABOUTME: Scan known credentials in memory and verify immutable archives by fresh extraction.

Groups are source:NAME, choice:NAME, or smoke:NAME. Source groups include their
recorded cache/attempt dependencies. Choice groups require those source groups
to be explicitly selected too. Inspections are explicitly named immutable JSON
snapshots, never a whole analysis directory. No inference is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
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


def checked_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Unsafe group or artifact name")
    return value


def ordinary_path(root: Path, path: Path) -> None:
    """Reject symlinks/special files, including intermediate path components."""
    relative = path.absolute().relative_to(root.absolute())
    if ".." in relative.parts:
        raise ValueError("Path escapes evidence root")
    if any(parent.is_symlink() for parent in root.absolute().parents):
        raise ValueError("Symlink ancestor is forbidden")
    current = root.absolute()
    for part in (None, *relative.parts):
        if part is not None:
            current /= part
        mode = current.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("Symlinks and special files are forbidden")


def read_bytes(root: Path, path: Path) -> bytes:
    ordinary_path(root, path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Expected an ordinary file")
        data = stream.read()
        after = os.fstat(stream.fileno())

    def identity(value):
        return value.st_ino, value.st_size, value.st_mtime_ns

    if identity(before) != identity(after) or identity(after) != identity(path.lstat()):
        raise ValueError("Evidence changed while reading")
    return data


def read_json(root: Path, path: Path) -> dict:
    value = json.loads(read_bytes(root, path))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def tree_files(root: Path, folder: Path) -> list[Path]:
    ordinary_path(root, folder)
    if not folder.is_dir():
        raise ValueError("Expected an ordinary directory")
    result = []
    for path in sorted(folder.iterdir()):
        ordinary_path(root, path)
        result.extend(tree_files(root, path) if path.is_dir() else [path])
    return result


def rows(root: Path, path: Path) -> list[dict]:
    return [json.loads(line) for line in read_bytes(root, path).splitlines() if line.strip()]


def complete_audits(root: Path, files: list[Path]) -> list[dict]:
    audits = []
    for path in files:
        if "providers" not in path.parts or path.suffix != ".json":
            continue
        item = read_json(root, path)
        if "outcome" in item:
            if item["outcome"] not in {"accepted", "uncertain", "error", "skipped"}:
                raise ValueError("Incomplete decision audit")
        elif item.get("status") not in {"success", "error"} or not item.get("finished_at"):
            raise ValueError("Incomplete LLM audit")
        audits.append({"path": path.relative_to(root).as_posix(), **item})
    return audits


def source_group(root: Path, name: str) -> tuple[list[Path], dict]:
    folder = root / "phase2/plans" / name
    result = read_json(root, folder / "compilation-result.json")
    bundle = read_json(root, folder / "plan.json")
    if result.get("status") not in {"accepted", "invalid", "rejected", "error"}:
        raise ValueError("Source compilation has no terminal result")
    key = result["cache_key"]
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Invalid cache key")
    cache = root / "phase2/cache" / f"{key}.json"
    attempt = Path(result["attempt_path"])
    if Path(result["artifact_path"]) != cache or attempt.parent != cache.parent / "attempts" / key:
        raise ValueError("Compilation dependency escapes selected cache identity")
    files = [*tree_files(root, folder), cache, *tree_files(root, attempt.parent)]
    if attempt not in files or digest(read_bytes(root, cache)) != result["artifact_sha256"]:
        raise ValueError("Missing attempt or altered cache artifact")
    for path in [cache, *tree_files(root, attempt.parent)]:
        payload = read_json(root, path)["payload"]
        if payload.get("status") != result["status"] or not payload.get("finished_at"):
            raise ValueError("Incomplete or inconsistent compilation cache")
    if bundle.get("compiler_artifact_sha256") != result["artifact_sha256"]:
        raise ValueError("Plan bundle refers to different compilation bytes")
    audits = complete_audits(root, files)
    return files, {
        "group": f"source:{name}",
        "status": result["status"],
        "completion_marker": (folder / "compilation-result.json").relative_to(root).as_posix(),
        "cache_key": key,
        "provider_requests": len(audits),
        "prompt_tokens": sum((a.get("usage") or {}).get("prompt_tokens", 0) for a in audits),
        "completion_tokens": sum(
            (a.get("usage") or {}).get("completion_tokens", 0) for a in audits
        ),
        "provider_elapsed_seconds": sum(a.get("elapsed_ms", 0) / 1000 for a in audits),
        "provider_errors": sum(a.get("status") == "error" for a in audits),
        "unknown_usage_requests": sum(not a.get("usage_reported", False) for a in audits),
        "models": sorted({a.get("response_model") or a["requested_model"] for a in audits}),
        "dollar_cost_estimated": False,
        "cost_note": "Raw preparation usage preserved; rejected attempts are included, not free.",
    }


def choice_group(root: Path, name: str) -> tuple[list[Path], dict]:
    folder = root / "phase2/runs" / name
    summary = read_json(root, folder / "summary.json")
    run = read_json(root, folder / "run.json")
    if not summary.get("completed_at") or run.get("kind") != "finite_choice_development_diagnostic":
        raise ValueError("Choice probe has no completion marker")
    ids = run["case_ids"]
    if len(set(ids)) != len(ids) or summary["cases"] != len(ids):
        raise ValueError("Choice case count mismatch")
    recorded = rows(root, folder / "predictions.jsonl")
    if sorted(row["case_id"] for row in recorded) != sorted(ids):
        raise ValueError("Choice predictions do not match planned cases")
    for case_id in ids:
        case = folder / checked_name(case_id)
        prediction = read_json(root, case / "prediction.json")
        if prediction.get("status") not in {"accepted", "abstained", "error"}:
            raise ValueError("Choice case has not terminated")
        for filename in ("inputs.json", "expected.json", "score.json"):
            read_json(root, case / filename)
    files = tree_files(root, folder)
    audits = complete_audits(root, files)
    if summary["provider_audit_files"] != len(audits) or summary["incomplete_provider_attempts"]:
        raise ValueError("Choice audit count is incomplete")
    return files, {
        "group": f"choice:{name}",
        "status": "completed",
        "completion_marker": (folder / "summary.json").relative_to(root).as_posix(),
        "summary": summary,
    }


def smoke_group(root: Path, name: str, allow_aborted: bool = False) -> tuple[list[Path], dict]:
    folder = root / "phase2/runs" / name
    if allow_aborted:
        aborted = read_json(root, folder / "aborted.json")
        if not aborted.get("at") or not aborted.get("error_type"):
            raise ValueError("An explicit recorded cancellation is required")
        if (folder / "completed.json").exists():
            raise ValueError("Conflicting terminal schedule markers")
        schedule = read_json(root, folder / "schedule.json")
        files = tree_files(root, folder)
        terminal_count = sum(path.name == "terminal.json" for path in files)
        if (
            aborted["planned_entries"] != len(schedule["entries"])
            or aborted["finished_entries"] != terminal_count
        ):
            raise ValueError("Aborted schedule counts disagree with preserved cases")
        return files, {
            "group": f"smoke:{name}",
            "status": "aborted_partial_preserved",
            "completion_marker": (folder / "aborted.json").relative_to(root).as_posix(),
            "aborted": aborted,
            "incomplete_provider_audits_allowed": True,
            "cost_note": (
                "Cancellation may leave pending requests with unknown usage or charges; "
                "partial evidence is not a completed comparison."
            ),
        }
    # Check the final marker first: do not traverse or package an active schedule.
    marker = read_json(root, folder / "completed.json")
    if not marker.get("completed_at") or any(
        (folder / filename).exists() for filename in ("aborted.json", "failed.json")
    ):
        raise ValueError("Only fully completed smoke schedules are eligible")
    summary_path = folder / "summary.json"
    if digest(read_bytes(root, summary_path)) != marker["summary_sha256"]:
        raise ValueError("Completion marker summary hash mismatch")
    summary = read_json(root, summary_path)
    schedule = read_json(root, folder / "schedule.json")
    entries = schedule["entries"]
    if not entries or any(
        count != len(entries)
        for count in (marker["entries"], summary["planned_entries"], summary["terminal_entries"])
    ):
        raise ValueError("Smoke schedule is incomplete")
    keys = [(e["arm"], e["domain"], e["task_id"]) for e in entries]
    if len(set(keys)) != len(keys):
        raise ValueError("Repeated scheduled case")
    predictions = rows(root, folder / "predictions.jsonl")
    if len(predictions) != len(entries):
        raise ValueError("Smoke prediction count mismatch")
    for arm, domain, case_id in keys:
        case = folder / checked_name(arm) / checked_name(domain) / checked_name(case_id)
        terminal = read_json(root, case / "terminal.json")
        if terminal.get("status") not in {"completed", "failed", "cancelled"}:
            raise ValueError("Scheduled case has not terminated")
        read_json(root, case / "phase2-observation.json")
    files = tree_files(root, folder)
    complete_audits(root, files)
    return files, {
        "group": f"smoke:{name}",
        "status": "completed",
        "completion_marker": (folder / "completed.json").relative_to(root).as_posix(),
        "summary": summary,
    }


def collect(
    root: Path, groups: list[str], inspections: list[str], allow_aborted: tuple[str, ...] = ()
) -> tuple[list[Path], list[dict]]:
    if not groups or len(set(groups)) != len(groups) or len(set(inspections)) != len(inspections):
        raise ValueError("Select explicit, unique completed groups")
    if any(group not in groups or not group.startswith("smoke:") for group in allow_aborted):
        raise ValueError("Aborted opt-in must identify a selected smoke group")
    files, metadata = set(), []
    handlers = {"source": source_group, "choice": choice_group, "smoke": smoke_group}
    for selected in groups:
        kind, name = selected.split(":", 1)
        if selected in allow_aborted:
            found, details = smoke_group(root, checked_name(name), allow_aborted=True)
        else:
            found, details = handlers[kind](root, checked_name(name))
        files.update(found)
        metadata.append(details)
    for selected in groups:
        if selected.startswith("choice:"):
            bundle = read_json(root, root / "phase2/runs" / selected.split(":", 1)[1] / "plan.json")
            for key in ("compilation_result", "compiler_artifact_path"):
                if Path(bundle[key]) not in files:
                    raise ValueError("Explicitly select the choice probe's source compilation too")
    for name in inspections:
        path = root / "phase2/analysis" / f"{checked_name(name)}.json"
        if not read_json(root, path).get("recorded_at"):
            raise ValueError("Inspection lacks its recorded snapshot timestamp")
        files.add(path)
    return sorted(files), metadata


def safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or str(path) != name
        or len(path.parts) < 3
        or path.parts[0] != "phase2"
        or path.parts[1] not in {"plans", "runs", "cache", "analysis"}
    ):
        raise ValueError("Unsafe archive member")


def verify_archive(archive: Path, manifest: dict, extract_to: Path | None = None) -> dict:
    """Restore only regular verified files into an exclusively created fresh directory."""
    data = read_bytes(archive.parent, archive)
    if (
        len(data) != manifest["archive"]["size_bytes"]
        or digest(data) != manifest["archive"]["sha256"]
    ):
        raise ValueError("Archive hash or size mismatch")
    expected = {member["path"]: member for member in manifest["members"]}
    if len(expected) != len(manifest["members"]):
        raise ValueError("Duplicate manifest member")
    for name in expected:
        safe_member(name)
    if extract_to is not None:
        ordinary_path(extract_to.parent, extract_to.parent)
        extract_to.mkdir(exist_ok=False)
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as stream:
        for member in stream:
            safe_member(member.name)
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError("Unexpected, duplicate, or non-file member")
            source = stream.extractfile(member)
            if source is None:
                raise ValueError("Missing member contents")
            raw = source.read()
            spec = expected[member.name]
            if len(raw) != spec["size_bytes"] or digest(raw) != spec["sha256"]:
                raise ValueError("Member size or hash mismatch")
            seen.add(member.name)
            if extract_to is not None:
                output = extract_to / member.name
                output.parent.mkdir(parents=True, exist_ok=True)
                ordinary_path(extract_to, output.parent)
                with output.open("xb") as destination:
                    destination.write(raw)
                if digest(read_bytes(extract_to, output)) != spec["sha256"]:
                    raise ValueError("Restored file hash mismatch")
    if seen != set(expected):
        raise ValueError("Archive is missing manifest members")
    return {
        "archive_sha256_verified": True,
        "members_verified": len(seen),
        "fresh_extraction_hashes_verified": extract_to is not None,
    }


def archive_groups(
    root: Path,
    name: str,
    groups: list[str],
    inspections: list[str],
    credentials: list[str],
    allow_aborted: tuple[str, ...] = (),
) -> dict:
    root = root.absolute()
    name = checked_name(name)
    output = root / "phase2/archives"
    destinations = [
        output / f"{name}.{suffix}" for suffix in ("tar.gz", "manifest.json", "verification.json")
    ]
    if any(path.exists() or path.is_symlink() for path in destinations):
        raise FileExistsError("Published archive artifacts are immutable")
    secrets = [value.encode() for value in credentials if value]
    if not secrets:
        raise ValueError("A known, nonempty credential scan is required")
    files, metadata = collect(root, groups, inspections, allow_aborted)
    members = []
    for path in files:
        raw = read_bytes(root, path)
        if any(secret in raw for secret in secrets):
            raise ValueError("Credential scan failed; no archive published")
        members.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": len(raw),
                "sha256": digest(raw),
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    ordinary_path(root, output)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=output) as temporary:
        scratch = Path(temporary)
        archive = scratch / "archive.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for path, member in zip(files, members, strict=True):
                raw = read_bytes(root, path)
                if digest(raw) != member["sha256"]:
                    raise ValueError("Evidence changed after credential scan")
                info = tarfile.TarInfo(member["path"])
                info.size, info.mode, info.mtime = len(raw), 0o600, 0
                stream.addfile(info, io.BytesIO(raw))
        current_files, current_metadata = collect(root, groups, inspections, allow_aborted)
        if current_files != files or current_metadata != metadata:
            raise ValueError("Selected groups changed while archiving")
        if any(
            digest(read_bytes(root, p)) != m["sha256"] for p, m in zip(files, members, strict=True)
        ):
            raise ValueError("Evidence changed while archiving")
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "name": name,
            "groups": metadata,
            "inspections": inspections,
            "inspection_status": (
                "Explicitly selected recorded snapshots; no live analysis directories"
            ),
            "archive": {
                "file": destinations[0].name,
                "sha256": digest(archive.read_bytes()),
                "size_bytes": archive.stat().st_size,
            },
            "raw_size_bytes": sum(m["size_bytes"] for m in members),
            "members": members,
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
            "raw_files_removed": False,
            "inference_performed": False,
            "accounting_note": (
                "Source preparation is reported separately from choice and smoke inference; "
                "do not add duplicate cache copies or amortize preparation twice. "
                "Configured provider cost excludes local compute. "
                "No compiler dollar price is inferred by this archiver."
            ),
        }
        checks = verify_archive(archive, manifest, scratch / "fresh-restoration")
        manifest_path = scratch / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        verification = {
            "verified_at": datetime.now(UTC).isoformat(),
            "archive_sha256": manifest["archive"]["sha256"],
            "manifest_sha256": digest(manifest_path.read_bytes()),
            **checks,
            "raw_size_bytes": manifest["raw_size_bytes"],
            "temporary_extraction_removed": True,
            "raw_files_retained": True,
            "inference_performed": False,
        }
        proof = scratch / "verification.json"
        proof.write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n")
        for source, target in zip((archive, manifest_path, proof), destinations, strict=True):
            source.chmod(0o444)
            os.link(source, target)  # Exclusive publication: never replace existing evidence.
    verify_archive(destinations[0], manifest)
    return verification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--group", action="append", required=True)
    parser.add_argument("--inspection", action="append", default=[])
    parser.add_argument("--allow-aborted", action="append", default=[], metavar="smoke:NAME")
    args = parser.parse_args()
    from benchmarks.sop_bench.experiment_credentials import load_pass_credentials

    credentials = load_pass_credentials(
        {
            "NEBIUS_API_KEY": "NEBIUS/TOKEN_FACTORY_API_KEY",
            "TYPESAFE_API_KEY": "API_KEYS/TYPESAFE_AI_JEV_API_KEY",
        }
    )
    result = archive_groups(
        EXPERIMENT,
        args.name,
        args.group,
        args.inspection,
        list(credentials.values()),
        tuple(args.allow_aborted),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # Credential subprocess exceptions and archive payloads must never be echoed.
        print(f"Archive rejected: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
