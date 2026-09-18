"""Dependency-free regression cases: synthetic records, never equipment fixtures.

These unittest cases are also collected by pytest. Synthetic layouts validate
software behavior only; they are not evidence for equipment execution.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import secrets
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pne_scheduler.api.errors import ApiError
from pne_scheduler.api.security import AccessPolicy
from pne_scheduler.io import atomic_output, template_writer
from pne_scheduler.io.sch_binary import read_sch_binary
from pne_scheduler.io.template_writer import SchFieldPatch, SchPatchPlan, apply_sch_patch
from pne_scheduler.resume import splice
from pne_scheduler.resume.checkpoint import StepEndRow, _is_completed, detect_checkpoint, load_raw_csv_checkpoint


class TemporaryCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def csv(self, name, header, rows):
        path = self.root / name
        with path.open("w", encoding="cp949", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    def synthetic_sch(self):
        header = bytearray(1760)
        struct.pack_into("<II", header, 0, 0x000B4D71, 0x00010003)
        data = bytearray(header)
        for number, kind in enumerate((3, 3, 3, 6), 1):
            record = bytearray(612)
            struct.pack_into("<i", record, 0, number)
            struct.pack_into("<i", record, 8, kind)
            data.extend(record)
        source = self.root / "source.sch"
        source.write_bytes(data)
        return source


class CheckpointSafetyTests(TemporaryCase):
    def test_raw_completed_charge_discharge_and_rest_advance(self):
        for kind in ("Charge", "Discharge", "Rest"):
            for flag in ("1", "true", "yes"):
                with self.subTest(kind=kind, flag=flag):
                    path = self.csv("raw.csv", ["StepNo", "StepType", "StepEnd"], [(5, kind, flag)])
                    checkpoint = detect_checkpoint(path)
                    self.assertTrue(checkpoint.step_completed)
                    self.assertEqual(checkpoint.resume_sch_step, 5)  # CTS5 -> SCH4 -> SCH5

    def test_legacy_stepend_completed_charge_and_discharge_advance(self):
        for kind, code in (("Charge", "Current Complete"), ("Discharge", "Voltage Complete")):
            with self.subTest(kind=kind):
                path = self.csv("stepend.csv", ["StepNo", "StepType", "Code"], [(5, kind, code)])
                checkpoint = detect_checkpoint(path)
                self.assertTrue(checkpoint.step_completed)
                self.assertEqual(checkpoint.resume_sch_step, 5)

    def test_unfinished_rest_does_not_advance(self):
        for name, column, code in (("raw.csv", "StepEnd", "0"),
                                   ("stepend.csv", "Code", "InProgress"),
                                   ("stepend.csv", "Code", "Incomplete"),
                                   ("stepend.csv", "Code", "Not Complete")):
            with self.subTest(name=name, code=code):
                path = self.csv(name, ["StepNo", "StepType", column], [(4, "Rest", code)])
                checkpoint = detect_checkpoint(path)
                self.assertFalse(checkpoint.step_completed)
                self.assertEqual(checkpoint.resume_sch_step, 3)

    def test_raw_uses_latest_progress_even_after_completed_row(self):
        for first_flag in ("0", "1"):
            with self.subTest(first_flag=first_flag):
                path = self.csv("raw.csv", ["StepNo", "StepType", "StepEnd", "StepTime_sec"],
                                [(2, "Charge", first_flag, 10), (4, "Rest", "0", 20),
                                 (4, "Rest", "0", 90), ("NaN", "Rest", "1", 100)])
                row = load_raw_csv_checkpoint(path)
                self.assertEqual(row.cts_step_no, 4)
                self.assertEqual(row.step_time_sec, 90)
                self.assertFalse(row.step_completed)
                self.assertEqual(detect_checkpoint(path).resume_sch_step, 3)

    def test_preamble_and_invalid_steps_are_not_schedule_steps(self):
        path = self.csv("raw.csv", ["StepNo", "StepEnd"],
                        [(2, "0"), (1, "1"), (0, "1"), (-1, "1"), ("2.5", "1"), ("inf", "1")])
        self.assertEqual(load_raw_csv_checkpoint(path).sch_step_no, 1)
        self.assertEqual(detect_checkpoint(path).resume_sch_step, 1)

    def test_missing_raw_flag_is_conservative(self):
        path = self.csv("raw.csv", ["StepNo", "StepType", "Code"], [(3, "Charge", "Complete")])
        self.assertFalse(detect_checkpoint(path).step_completed)
        self.assertEqual(detect_checkpoint(path).resume_sch_step, 2)

    def test_finished_semantics_preserved_in_raw_and_stepend(self):
        for name, kind, code in (("raw.csv", "End", ""), ("raw.csv", "Rest", "Experiment End"),
                                 ("stepend.csv", "End", ""), ("stepend.csv", "Rest", "Last Step")):
            with self.subTest(name=name, code=code):
                path = self.csv(name, ["StepNo", "StepType", "Code", "StepEnd"], [(5, kind, code, "0")])
                checkpoint = detect_checkpoint(path)
                self.assertTrue(checkpoint.is_finished)
                self.assertEqual(checkpoint.resume_sch_step, 4)

    def test_legacy_row_constructor_and_explicit_boolean(self):
        row = StepEndRow(3, 2, "Charge", "Current Complete", None, None, None)
        self.assertTrue(_is_completed(row))
        self.assertFalse(_is_completed(replace(row, step_completed=False)))
        self.assertTrue(_is_completed(replace(row, completion_code="StepEnd")))


class WriterSafetyTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.source = self.synthetic_sch()
        self.progress = self.csv("raw.csv", ["StepNo", "StepType", "StepEnd"], [(2, "Rest", "1")])
        self.output = self.root / "out.sch"
        self.manifest = self.root / "out.sch.manifest.json"
        self.plan = SchPatchPlan(hashlib.sha256(self.source.read_bytes()).hexdigest(),
                                 (SchFieldPatch(2, "fEndV", 3123.0),))

    def resume(self, **kwargs):
        return splice.splice_resume_schedule(self.source, self.progress, self.output, **kwargs)

    def patch_output(self, **kwargs):
        return apply_sch_patch(self.source, self.plan, self.output, allow_analysis_output=True, **kwargs)

    def assert_no_temporary_files(self):
        self.assertFalse([p for p in self.root.iterdir() if p.name.startswith(".")])

    def test_resume_rejects_all_path_collisions_before_mutation(self):
        originals = {self.source: self.source.read_bytes(), self.progress: self.progress.read_bytes()}
        paths = [self.source, self.progress, self.output, self.manifest]
        for left in range(4):
            for right in range(left + 1, 4):
                with self.subTest(left=left, right=right):
                    colliding = paths.copy()
                    colliding[right] = colliding[left]
                    with self.assertRaisesRegex(ValueError, "alias"):
                        splice.splice_resume_schedule(*colliding[:3], validation_manifest_path=colliding[3])
                    for path, content in originals.items():
                        self.assertEqual(path.read_bytes(), content)
                    self.assertFalse(self.output.exists())
                    self.assertFalse(self.manifest.exists())
        self.assert_no_temporary_files()

    def test_resolved_alias_is_rejected(self):
        alias = self.root / ".." / self.root.name / self.source.name
        with self.assertRaisesRegex(ValueError, "alias"):
            splice.splice_resume_schedule(self.source, self.progress, alias)

    def test_hardlink_alias_rejected_by_both_writers(self):
        try:
            os.link(self.source, self.output)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "alias"):
            self.resume()
        with self.assertRaisesRegex(ValueError, "alias"):
            self.patch_output()
        self.assertEqual(self.source.read_bytes(), original)

    def test_symlink_alias_is_rejected(self):
        try:
            self.output.symlink_to(self.source)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        with self.assertRaisesRegex(ValueError, "alias"):
            self.resume()
        with self.assertRaisesRegex(ValueError, "alias"):
            self.patch_output()

    def test_resume_manifest_and_document_describe_actual_output(self):
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        result = self.resume()
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["template"]["sha256"], source_hash)
        self.assertEqual(manifest["output"]["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(manifest["output"]["path"], str(self.output))
        self.assertEqual(result.document.path, self.output)
        self.assertTrue(manifest["validation"]["all_passed"])
        self.assertFalse(manifest["equipment_executable"])
        self.assert_no_temporary_files()

    def test_resume_staging_failure_preserves_existing_output_and_manifest(self):
        self.output.write_bytes(b"original output")
        self.manifest.write_bytes(b"original manifest")
        with patch.object(splice, "write_validation_manifest", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.resume()
        self.assertEqual(self.output.read_bytes(), b"original output")
        self.assertEqual(self.manifest.read_bytes(), b"original manifest")
        self.assert_no_temporary_files()

    def test_resume_rejects_source_change_during_staging(self):
        self.output.write_bytes(b"keep")
        real_write = splice.write_sch_binary
        def mutate_source(doc, output):
            real_write(doc, output)
            self.source.write_bytes(self.source.read_bytes() + b"external change")
        with patch.object(splice, "write_sch_binary", side_effect=mutate_source):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                self.resume()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assertFalse(self.manifest.exists())
        self.assert_no_temporary_files()

    def test_invalid_manifest_parent_is_rejected_before_output_mutation(self):
        self.output.write_bytes(b"keep")
        with self.assertRaisesRegex(ValueError, "Invalid output destination"):
            self.resume(validation_manifest_path=self.root / "missing" / "manifest.json")
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assert_no_temporary_files()

    def test_resume_manifest_promotion_failure_rolls_back(self):
        real_replace = os.replace
        def fail_manifest(source, destination):
            if Path(destination) == self.manifest:
                raise OSError("manifest replacement refused")
            real_replace(source, destination)
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.output.write_bytes(b"original output")
                    self.manifest.write_bytes(b"original manifest")
                with patch.object(atomic_output.os, "replace", side_effect=fail_manifest):
                    with self.assertRaises(OSError):
                        self.resume()
                if existing:
                    self.assertEqual(self.output.read_bytes(), b"original output")
                    self.assertEqual(self.manifest.read_bytes(), b"original manifest")
                else:
                    self.assertFalse(self.output.exists())
                    self.assertFalse(self.manifest.exists())
                self.assert_no_temporary_files()

    def test_resume_validation_failure_preserves_existing_output(self):
        self.output.write_bytes(b"keep")
        real_read = read_sch_binary
        def broken_read(path):
            doc = real_read(path)
            return doc if Path(path) == self.source else replace(doc, header=b"bad")
        with patch.object(splice, "read_sch_binary", side_effect=broken_read):
            with self.assertRaisesRegex(ValueError, "validation"):
                self.resume()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assertFalse(self.manifest.exists())
        self.assert_no_temporary_files()

    def test_patch_validates_before_replace_and_hashes_actual_bytes(self):
        old_temporary = self.root / ".out.sch.tmp"
        old_temporary.write_bytes(b"other writer owns this")
        result = self.patch_output()
        self.assertEqual(old_temporary.read_bytes(), b"other writer owns this")
        self.assertEqual(result.report["output"]["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(result.report["output"]["path"], str(self.output))
        self.assertFalse(result.report["equipment_executable"])
        self.assertEqual(result.report["validation"]["equipment_smoke_test"], "not_run")
        old_temporary.unlink()
        self.assert_no_temporary_files()

    def test_patch_failed_structural_validation_preserves_existing_output(self):
        self.output.write_bytes(b"keep")
        real_read = read_sch_binary
        def broken_read(path):
            doc = real_read(path)
            return doc if Path(path) == self.source else replace(doc, step_size=696)
        with patch.object(template_writer, "read_sch_binary", side_effect=broken_read):
            with self.assertRaisesRegex(ValueError, "validation"):
                self.patch_output()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assert_no_temporary_files()

    def test_patch_failed_diff_preserves_existing_output(self):
        self.output.write_bytes(b"keep")
        from pne_scheduler.tools import compare_sch
        with patch.object(compare_sch, "compare_sch_files", side_effect=ValueError("diff failure")):
            with self.assertRaisesRegex(ValueError, "diff failure"):
                self.patch_output()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assert_no_temporary_files()

    def test_patch_promotion_failure_cleans_staging_and_preserves_output(self):
        self.output.write_bytes(b"keep")
        with patch.object(template_writer.os, "replace", side_effect=OSError("replace refused")):
            with self.assertRaisesRegex(OSError, "replace refused"):
                self.patch_output()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assert_no_temporary_files()

    def test_patch_detects_staged_byte_tampering(self):
        self.output.write_bytes(b"keep")
        real_read = read_sch_binary
        def tamper(path):
            doc = real_read(path)
            if Path(path) != self.source:
                body = bytearray(Path(path).read_bytes())
                body[1760 + 100] = 1
                Path(path).write_bytes(body)
            return doc
        with patch.object(template_writer, "read_sch_binary", side_effect=tamper):
            with self.assertRaisesRegex(ValueError, "changed during validation"):
                self.patch_output()
        self.assertEqual(self.output.read_bytes(), b"keep")
        self.assert_no_temporary_files()

    def test_same_destination_fails_fast_and_does_not_remove_other_lock(self):
        self.output.write_bytes(b"keep")
        lock = self.root / ".out.sch.lock"
        with atomic_output.output_lock(self.output):
            for writer in (self.patch_output, self.resume):
                with self.assertRaisesRegex(ValueError, "locked"):
                    writer()
                self.assertTrue(lock.exists())
                self.assertEqual(self.output.read_bytes(), b"keep")
        self.assert_no_temporary_files()


class AccessPolicyTests(unittest.TestCase):
    def local(self, **environment):
        with patch.dict(os.environ, environment, clear=True):
            return AccessPolicy.from_environment()

    def check(self, policy, **overrides):
        values = dict(host="localhost:8000", remote_addr="127.0.0.1", origin=None,
                      fetch_site=None, authorization="")
        values.update(overrides)
        policy.check_request(**values)

    def test_local_accepts_loopback_and_trusted_frontend_origin(self):
        policy = self.local()
        self.assertTrue(policy.local_resources)
        self.check(policy, origin="http://localhost:3000")
        self.check(policy, host="[::1]:8000", remote_addr="::1")

    def test_local_rejects_rebinding_origins_and_remote_peers(self):
        policy = self.local()
        for overrides in ({"host": "attacker.example"}, {"host": "localhost.attacker.example"},
                          {"host": "localhost:bad"}, {"origin": "null"},
                          {"origin": "http://localhost:9999"},
                          {"origin": "https://attacker.example"}, {"remote_addr": "192.0.2.1"},
                          {"fetch_site": "cross-site"}):
            with self.subTest(overrides=overrides), self.assertRaises(ApiError):
                self.check(policy, **overrides)

    def test_local_resources_can_be_disabled(self):
        self.assertFalse(self.local(PNE_LOCAL_RESOURCES="0").local_resources)

    def test_cloud_fails_closed_without_token_or_host_config(self):
        for environment in ({"PNE_SERVER_MODE": "cloud"},
                            {"PNE_SERVER_MODE": "cloud", "PNE_API_TOKEN": secrets.token_urlsafe(32)},
                            {"PNE_SERVER_MODE": "cloud", "PNE_LOCAL_RESOURCES": "1"}):
            with patch.dict(os.environ, environment, clear=True), self.assertRaises(ValueError):
                AccessPolicy.from_environment()

    def test_cloud_auth_and_host_origin_checks(self):
        token = secrets.token_urlsafe(32)
        policy = self.local(PNE_SERVER_MODE="cloud", PNE_API_TOKEN=token,
                            PNE_ALLOWED_HOSTS="scheduler.example", PNE_ALLOWED_ORIGINS="https://scheduler.example")
        self.assertFalse(policy.local_resources)
        self.assertNotIn(token, repr(policy))
        self.check(policy, host="scheduler.example", origin="https://scheduler.example", authorization=f"Bearer {token}")
        for auth in ("", "Bearer incorrect", "Bearer \uac00"):
            with self.assertRaises(ApiError) as error:
                self.check(policy, host="scheduler.example", authorization=auth)
            self.assertEqual(error.exception.status, 401)
        with self.assertRaises(ApiError):
            self.check(policy, host="attacker.example", authorization=f"Bearer {token}")


if __name__ == "__main__":
    unittest.main()