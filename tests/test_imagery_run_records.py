"""Tests for imagery run locations and create-only configuration records."""

import base64
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from esdglider import imagery_cli, imagery_run_records


class RunRecordTests(unittest.TestCase):
    """Verify run naming and safe object creation without cloud access."""

    def test_new_ids_are_distinct_and_have_valid_locations(self):
        """Generated IDs produce distinct valid prefixes for the same deployment."""
        first_id = imagery_run_records.new_run_id()
        second_id = imagery_run_records.new_run_id()
        self.assertNotEqual(first_id, second_id)
        location = imagery_run_records.RunLocation(
            "test-output-bucket", "calanus-20261007", first_id,
        )
        self.assertEqual(
            location.prefix,
            f"SWFSC/glider/calanus-20261007/runs/{first_id}",
        )
        self.assertEqual(location.output_prefix, f"{location.prefix}/outputs")
        self.assertEqual(
            location.config_object("input.yaml"),
            f"{location.prefix}/config/input.yaml",
        )

    def test_invalid_location_components_are_rejected(self):
        """Malformed names, traversal, and invalid timestamps cannot form paths."""
        valid_id = "20261113T173500Z-" + "a" * 32
        cases = [
            ("gs://test-bucket", "calanus-20261007", valid_id),
            ("test-bucket", "../calanus-20261007", valid_id),
            ("test-bucket", "calanus-20261007", "../other"),
            (
                "test-bucket", "calanus-20261007",
                "20261313T173500Z-" + "a" * 32,
            ),
        ]
        for bucket, deployment, run_id in cases:
            with self.subTest(
                bucket=bucket, deployment=deployment, run_id=run_id,
            ):
                with self.assertRaises(ValueError):
                    imagery_run_records.RunLocation(bucket, deployment, run_id)
        location = imagery_run_records.RunLocation(
            "test-bucket", "calanus-20261007", valid_id,
        )
        with self.assertRaises(ValueError):
            location.config_object("../other")

    def test_create_record_uses_exclusive_gcs_precondition(self):
        """The exact YAML bytes are uploaded only when the object is absent."""
        location = imagery_run_records.RunLocation(
            "test-output-bucket",
            "calanus-20261007",
            "20261113T173500Z-" + "a" * 32,
        )
        bucket = MagicMock()
        bucket.name = location.bucket_name
        contents = b"deployment_name: calanus-20261007\n"
        imagery_run_records.create_record(
            bucket, location, "input.yaml", contents,
            content_type="application/yaml",
        )
        bucket.blob.assert_called_once_with(location.config_object("input.yaml"))
        bucket.blob.return_value.upload_from_string.assert_called_once_with(
            contents, content_type="application/yaml", if_generation_match=0,
        )

    def test_creation_failures_are_not_silenced(self):
        """A precondition or authorization failure is surfaced to the caller."""
        location = imagery_run_records.RunLocation(
            "test-output-bucket",
            "calanus-20261007",
            "20261113T173500Z-" + "a" * 32,
        )
        bucket = MagicMock()
        bucket.name = location.bucket_name
        bucket.blob.return_value.upload_from_string.side_effect = PermissionError(
            "denied",
        )
        with self.assertRaises(PermissionError):
            imagery_run_records.create_record(
                bucket, location, "resolved.json", b"{}",
                content_type="application/json",
            )
        bucket.name = "other-bucket"
        with self.assertRaises(ValueError):
            imagery_run_records.create_record(
                bucket, location, "input.yaml", b"input",
                content_type="application/yaml",
            )

    def test_capture_preserves_exact_yaml_and_cli_overrides(self):
        """Authored bytes are retained while effective values include overrides."""
        contents = (
            b"# Preserve my comment and CRLF\r\n"
            b"deployment_name: calanus-20261007\r\n"
            b"mount_root: ./mounts\r\n"
            b"dag:\r\n  model_type: original\r\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.yaml"
            path.write_bytes(contents)
            args = imagery_cli.create_parser("pipeline", "test").parse_args([
                "--config", str(path), "--model-type", "override",
                "--submit", "--test-limit", "1",
            ])
            config = imagery_cli.load_config(args)
            with patch.object(
                imagery_run_records, "git_details",
                return_value={"available": False, "reason": "test"},
            ):
                context = imagery_run_records.capture_context(config, args)
                path.write_text(
                    "deployment_name: calanus-20261107\n"
                    "dag:\n  model_type: original\n",
                )
                with self.assertRaisesRegex(ValueError, "changed"):
                    imagery_run_records.capture_context(config, args)
        self.assertEqual(
            base64.b64decode(context["input_yaml_base64"]), contents,
        )
        self.assertEqual(
            context["source"]["sha256"], hashlib.sha256(contents).hexdigest(),
        )
        self.assertEqual(context["settings"]["model_type"], "override")
        self.assertTrue(Path(context["settings"]["mount_root"]).is_absolute())
        self.assertEqual(context["operations"]["test_limit"], 1)
        self.assertNotIn("require_model", context["cli_options"])

    def test_git_provenance_reports_revision_dirty_state_and_unavailable(self):
        """Dirty checkouts and unavailable Git are explicitly represented."""
        with patch.object(
            imagery_run_records.subprocess, "run",
            side_effect=[
                subprocess.CompletedProcess([], 0, "/tmp\n", ""),
                subprocess.CompletedProcess([], 0, "abcdef\n", ""),
                subprocess.CompletedProcess([], 0, " M input.yaml\n", ""),
            ],
        ):
            result = imagery_run_records.git_details(Path("/tmp/input.yaml"))
        self.assertEqual(result["commit"], "abcdef")
        self.assertTrue(result["dirty"])
        self.assertEqual(result["path"], "input.yaml")
        with patch.object(
            imagery_run_records.subprocess, "run",
            side_effect=FileNotFoundError("git"),
        ):
            self.assertFalse(
                imagery_run_records.git_details(Path("/tmp/input.yaml"))[
                    "available"
                ],
            )

    def test_snapshots_are_created_exclusively_and_reads_pin_generation(self):
        """Record the exact object version read and guard snapshot creation."""
        client = MagicMock()
        bucket = client.bucket.return_value
        bucket.name = "test-bucket"
        blob = bucket.blob.return_value
        blob.generation = "42"
        contents = b"manifest"
        blob.download_as_bytes.return_value = contents
        _, reference = imagery_run_records.read_snapshot(
            client, "gs://test-bucket/run/manifest.json",
        )
        blob.download_as_bytes.assert_called_once_with(if_generation_match=42)
        self.assertEqual(reference["generation"], 42)
        self.assertEqual(
            reference["sha256"], hashlib.sha256(contents).hexdigest(),
        )
        archived = imagery_run_records.archive_object(
            bucket, "run/manifest.json", contents,
        )
        self.assertEqual(archived, reference)
        blob.upload_from_string.assert_called_once_with(
            contents, if_generation_match=0,
        )

    def test_snapshot_verification_rejects_changed_generation_or_content(self):
        """Replacing even identical bytes invalidates recorded generations."""
        reference = {
            "uri": "gs://test-bucket/run/input", "generation": 42,
            "sha256": "original",
        }
        for change in ({"generation": 43}, {"sha256": "changed"}):
            with (
                patch.object(
                    imagery_run_records, "read_snapshot",
                    return_value=(b"input", {**reference, **change}),
                ),
                self.assertRaisesRegex(ValueError, "Archived input changed"),
            ):
                imagery_run_records.verify_snapshots(MagicMock(), [reference])


if __name__ == "__main__":
    unittest.main()
