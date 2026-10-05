"""ABOUTME: Exercise phase-two archiving with temporary evidence and fake credentials only.
ABOUTME: Run standalone; never import runtime inference or alter frozen benchmark tests.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

import archive_phase2 as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phase2-archive-test-")
        # macOS /var is a symlink; use the physical temporary root explicitly.
        self.root = Path(self.temp.name).resolve()
        self.group = "source:probe"
        self.folder = self.root / "phase2/plans/probe"
        key = "a" * 64
        self.cache = self.root / "phase2/cache" / f"{key}.json"
        self.attempt = self.cache.parent / "attempts" / key / "attempt.json"
        self.write(self.cache, {"payload": {"status": "accepted", "finished_at": "now"}})
        self.write(self.attempt, {"payload": {"status": "accepted", "finished_at": "now"}})
        sha = archive.digest(self.cache.read_bytes())
        self.write(
            self.folder / "compilation-result.json",
            {
                "status": "accepted",
                "cache_key": key,
                "artifact_path": str(self.cache),
                "attempt_path": str(self.attempt),
                "artifact_sha256": sha,
            },
        )
        self.write(self.folder / "plan.json", {"compiler_artifact_sha256": sha})
        self.write(
            self.folder / "providers/compiler/request.json",
            {
                "status": "success",
                "finished_at": "now",
                "elapsed_ms": 200,
                "requested_model": "fixture",
                "usage_reported": True,
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def package(self):
        return archive.archive_groups(self.root, "fixture", [self.group], [], ["fake-known-secret"])

    def paths(self):
        folder = self.root / "phase2/archives"
        return folder / "fixture.tar.gz", folder / "fixture.manifest.json"

    def test_roundtrip_preserves_all_bytes_and_cost_metadata(self):
        result = self.package()
        packed, path = self.paths()
        manifest = json.loads(path.read_text())
        restored = self.root / "restored"
        checks = archive.verify_archive(packed, manifest, restored)
        self.assertEqual(result["members_verified"], 5)
        self.assertTrue(checks["fresh_extraction_hashes_verified"])
        self.assertEqual(manifest["groups"][0]["prompt_tokens"], 2)
        self.assertFalse(manifest["groups"][0]["dollar_cost_estimated"])
        self.assertTrue(self.folder.exists())
        for member in manifest["members"]:
            self.assertEqual(
                (restored / member["path"]).read_bytes(), (self.root / member["path"]).read_bytes()
            )

    def test_secret_match_rejects_without_publishing(self):
        (self.folder / "secret.txt").write_text("fake-known-secret")
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse(self.paths()[0].exists())

    def test_empty_credentials_reject(self):
        with self.assertRaises(ValueError):
            archive.archive_groups(self.root, "fixture", [self.group], [], [])

    def test_existing_archive_is_immutable(self):
        self.package()
        before = self.paths()[0].read_bytes()
        with self.assertRaises(FileExistsError):
            self.package()
        self.assertEqual(before, self.paths()[0].read_bytes())

    def test_symlink_file_rejected(self):
        (self.folder / "alias").symlink_to(self.cache)
        with self.assertRaises(ValueError):
            self.package()

    def test_symlink_directory_rejected(self):
        (self.folder / "alias").symlink_to(self.cache.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.package()

    def test_fifo_rejected_without_opening(self):
        os.mkfifo(self.folder / "pipe")
        with self.assertRaises(ValueError):
            self.package()

    def test_missing_source_terminal_result_rejected(self):
        (self.folder / "compilation-result.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.package()

    def test_started_provider_audit_rejected(self):
        self.write(self.folder / "providers/compiler/request.json", {"status": "started"})
        with self.assertRaises(ValueError):
            self.package()

    def test_negative_decision_is_a_finalized_audit(self):
        path = self.root / "phase2/runs/probe/providers/decisions/negative.json"
        self.write(path, {"outcome": "rejected", "reason": "obligation_rejected"})
        audits = archive.complete_audits(self.root, [path])
        self.assertEqual(audits[0]["outcome"], "rejected")

    def test_cache_hash_change_rejected(self):
        self.cache.write_text("{}")
        with self.assertRaises(ValueError):
            self.package()

    def test_active_smoke_rejected_even_with_explicit_abort_option(self):
        folder = self.root / "phase2/runs/live"
        self.write(folder / "schedule.json", {"entries": []})
        for allowed in ((), ("smoke:live",)):
            with self.assertRaises(FileNotFoundError):
                archive.collect(self.root, ["smoke:live"], [], allowed)

    def test_completed_smoke_requires_matching_marker_and_all_terminal_cases(self):
        folder = self.root / "phase2/runs/done"
        entry = {"arm": "baseline", "domain": "email", "task_id": "E1"}
        self.write(folder / "schedule.json", {"entries": [entry]})
        self.write(folder / "summary.json", {"planned_entries": 1, "terminal_entries": 1})
        self.write(
            folder / "completed.json",
            {
                "completed_at": "now",
                "entries": 1,
                "summary_sha256": archive.digest((folder / "summary.json").read_bytes()),
            },
        )
        (folder / "predictions.jsonl").write_text(json.dumps(entry) + "\n")
        case = folder / "baseline/email/E1"
        self.write(case / "terminal.json", {"status": "completed"})
        self.write(case / "phase2-observation.json", {})
        files, metadata = archive.collect(self.root, ["smoke:done"], [])
        self.assertEqual(len(files), 6)
        self.assertEqual(metadata[0]["status"], "completed")
        (case / "terminal.json").unlink()
        with self.assertRaises(FileNotFoundError):
            archive.collect(self.root, ["smoke:done"], [])
        self.write(case / "terminal.json", {"status": "completed"})
        self.write(folder / "summary.json", {"planned_entries": 1, "terminal_entries": 0})
        with self.assertRaises(ValueError):
            archive.collect(self.root, ["smoke:done"], [])

    def test_choice_requires_source_dependencies_and_matching_prediction_ids(self):
        folder = self.root / "phase2/runs/choice"
        self.write(
            folder / "run.json",
            {
                "kind": "finite_choice_development_diagnostic",
                "case_ids": ["E1"],
            },
        )
        self.write(
            folder / "summary.json",
            {
                "completed_at": "now",
                "cases": 1,
                "provider_audit_files": 1,
                "incomplete_provider_attempts": 0,
            },
        )
        self.write(
            folder / "plan.json",
            {
                "compilation_result": str(self.folder / "compilation-result.json"),
                "compiler_artifact_path": str(self.cache),
            },
        )
        prediction = {"case_id": "E1", "status": "accepted"}
        (folder / "predictions.jsonl").write_text(json.dumps(prediction) + "\n")
        self.write(folder / "E1/prediction.json", prediction)
        for filename in ("inputs.json", "expected.json", "score.json"):
            self.write(folder / "E1" / filename, {})
        self.write(folder / "E1/providers/decisions/one.json", {"outcome": "accepted"})
        with self.assertRaises(ValueError):
            archive.collect(self.root, ["choice:choice"], [])
        _, metadata = archive.collect(self.root, [self.group, "choice:choice"], [])
        self.assertEqual(metadata[1]["status"], "completed")
        (folder / "predictions.jsonl").write_text(json.dumps({"case_id": "E2"}) + "\n")
        with self.assertRaises(ValueError):
            archive.collect(self.root, [self.group, "choice:choice"], [])

    def test_aborted_requires_opt_in_and_preserves_partial_audit(self):
        folder = self.root / "phase2/runs/stopped"
        self.write(folder / "schedule.json", {"entries": [{"arm": "a"}]})
        self.write(
            folder / "aborted.json",
            {
                "at": "now",
                "error_type": "CancelledError",
                "planned_entries": 1,
                "finished_entries": 0,
                "unknown_cost_requests": 1,
            },
        )
        self.write(folder / "providers/request.json", {"status": "started"})
        with self.assertRaises(FileNotFoundError):
            archive.collect(self.root, ["smoke:stopped"], [])
        files, metadata = archive.collect(self.root, ["smoke:stopped"], [], ("smoke:stopped",))
        self.assertEqual(len(files), 3)
        self.assertEqual(metadata[0]["status"], "aborted_partial_preserved")
        self.assertTrue(metadata[0]["incomplete_provider_audits_allowed"])

    def test_fresh_extraction_required(self):
        self.package()
        packed, path = self.paths()
        with self.assertRaises(FileExistsError):
            archive.verify_archive(packed, json.loads(path.read_text()), self.root)

    def test_archive_and_member_hash_corruption_rejected(self):
        self.package()
        packed, path = self.paths()
        manifest = json.loads(path.read_text())
        manifest["archive"]["sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            archive.verify_archive(packed, manifest)
        manifest = json.loads(path.read_text())
        manifest["members"][0]["sha256"] = "f" * 64
        with self.assertRaises(ValueError):
            archive.verify_archive(packed, manifest)

    def test_unsafe_paths_duplicates_and_links_rejected(self):
        for name, kind, repeat in (
            ("../escape", "file", False),
            ("phase2/plans/link", "symlink", False),
            ("phase2/plans/repeat", "file", True),
        ):
            with self.subTest(name=name):
                packed = self.root / "malicious.tar.gz"
                with tarfile.open(packed, "w:gz") as stream:
                    for _ in range(2 if repeat else 1):
                        info = tarfile.TarInfo(name)
                        info.size = 0
                        if kind == "symlink":
                            info.type, info.linkname = tarfile.SYMTYPE, "/tmp/escape"
                        stream.addfile(info, io.BytesIO(b""))
                manifest = {
                    "archive": {
                        "sha256": archive.digest(packed.read_bytes()),
                        "size_bytes": packed.stat().st_size,
                    },
                    "members": [{"path": name, "size_bytes": 0, "sha256": archive.digest(b"")}],
                }
                with self.assertRaises(ValueError):
                    archive.verify_archive(packed, manifest)

    def test_inspection_requires_recorded_timestamp(self):
        self.write(self.root / "phase2/analysis/inspection.json", {})
        with self.assertRaises(ValueError):
            archive.collect(self.root, [self.group], ["inspection"])

    def test_groups_must_be_explicit_unique_and_safe(self):
        for groups in ([], [self.group, self.group], ["source:../escape"]):
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                archive.collect(self.root, groups, [])


if __name__ == "__main__":
    unittest.main()
