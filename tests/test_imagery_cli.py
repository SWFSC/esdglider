"""Test imagery configuration, file outputs, and safe DAG submission."""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from esdglider import imagery_cli

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str) -> types.ModuleType:
    """Load a script without executing its command-line entry point.

    Args:
        name: Hyphenated script basename.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / "scripts" / f"{name}.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


METADATA = load_script("extract-image-metadata")
MANIFESTS = load_script("generate-osi-json")
DAG = load_script("trigger-osi-dag")
PIPELINE = load_script("run-imagery-pipeline")


class ConfigTests(unittest.TestCase):
    """Check defaults, shared YAML, overrides, and early validation."""

    def test_explicit_model_required_only_for_dag_stages(self):
        """Missing models block DAG stages, including path-only previews."""
        for stage in ("dag", "pipeline"):
            parser = imagery_cli.create_parser(stage, "test")
            for mode in ([], ["--dry-run"], ["--submit"]):
                args = parser.parse_args([
                    "--deployment-name", "calanus-20261007", *mode,
                ])
                with self.assertRaisesRegex(ValueError, "Choose a model"):
                    imagery_cli.load_config(args)
            for model in ("", " ", "invalid/model"):
                args = parser.parse_args([
                    "--deployment-name", "calanus-20261007",
                    "--model-type", model,
                ])
                with self.assertRaises(ValueError):
                    imagery_cli.load_config(args)
        for stage in ("metadata", "manifests"):
            config = self.parse(
                stage, "--deployment-name", "calanus-20261007",
            )
            self.assertEqual(config.model_type, "")

    def test_missing_model_starts_no_pipeline_stages(self):
        """A missing model fails before metadata can be written."""
        with patch.object(PIPELINE.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "Choose a model"):
                PIPELINE.main([
                    "--deployment-name", "calanus-20261007", "--submit",
                ])
            run.assert_not_called()

    def parse(self, stage="dag", *options):
        """Return effective settings for a stage and CLI options."""
        parser = imagery_cli.create_parser(stage, "test")
        return imagery_cli.load_config(parser.parse_args(list(options)))

    def test_example_for_every_stage(self):
        """The example config is accepted unchanged by all three stages."""
        for stage in ("metadata", "manifests", "dag", "pipeline"):
            config = self.parse(
                stage, "--config",
                str(ROOT / "resources" / "imagery-deployment.example.yaml"),
            )
            self.assertEqual(config.deployment_name, "calanus-20261007")
            self.assertEqual(config.directory_chunk_size, 50)

    def test_overrides_and_defaults(self):
        """CLI settings override YAML settings and retain remaining defaults."""
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "deployment.yaml"
            config_path.write_text(
                "deployment_name: calanus-20261007\n"
                "directory_chunk_size: 20\n"
                "dag:\n  model_type: configured-model\n",
            )
            config = self.parse(
                "dag", "--config", str(config_path),
                "--deployment-name", "chovy-20261005",
                "--directory-chunk-size", "7", "--model-type", "cli-model",
            )
        self.assertEqual(config.deployment_name, "chovy-20261005")
        self.assertEqual(config.directory_chunk_size, 7)
        self.assertEqual(config.model_type, "cli-model")
        self.assertEqual(config.output_bucket, "ggn-nmfs-osi-dev-1-data")
        self.assertEqual(
            config.manifest_prefix,
            "2026/chovy-20261005/osi-manifests-007dirs/",
        )
        self.assertEqual(
            config.output_prefix, "SWFSC/glider/chovy-20261005/007dirs",
        )

    def test_invalid_yaml_settings(self):
        """Invalid structure, values, and unknown keys fail before cloud use."""
        cases = [
            "", "[]", "dag: []",
            "deployment_name: ../escape-20261007",
            "deployment_name: calanus-20260230",
            "deployment_name: glider-name-20261007",
            "deployment_name: calanus-20261007\nunknown: 1",
            (
                "deployment_name: calanus-20261007\n"
                "deployment_name: chovy-20261005"
            ),
            (
                "deployment_name: calanus-20261007\n"
                "dag:\n  model_type: one\n  model_type: two"
            ),
            "deployment_name: calanus-20261007\n1: value",
            "deployment_name: calanus-20261007\ndirectory_chunk_size: true",
            "deployment_name: calanus-20261007\nnum_cores: 0",
            "deployment_name: calanus-20261007\ndirectory_filter: []",
            "deployment_name: calanus-20261007\ndag:\n  dry_run: false",
            "deployment_name: calanus-20261007\ndag:\n  model_type: null",
            (
                "deployment_name: calanus-20261007\n"
                "dag:\n  yaml_config_path: file.yaml"
            ),
            (
                "deployment_name: calanus-20261007\n"
                "dag:\n  composer_url: http://host"
            ),
            "deployment_name: calanus-20261007\nimagery_bucket: gs://bucket",
            "deployment_name: [broken",
        ]
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "bad.yaml"
            for content in cases:
                with self.subTest(content=content):
                    config_path.write_text(content)
                    with self.assertRaises(ValueError):
                        self.parse("dag", "--config", str(config_path))

    def test_invalid_cli_and_missing_deployment(self):
        """Missing deployment and nonpositive CLI integers fail."""
        with self.assertRaises(ValueError):
            self.parse("dag")
        for key in ("--test-limit", "--directory-chunk-size"):
            with self.assertRaises(ValueError):
                self.parse(
                    "dag", "--deployment-name", "calanus-20261007", key, "0",
                )

    def test_help_and_safe_mode(self):
        """Each command exposes help and DAG live mode is explicit."""
        for stage in ("metadata", "manifests", "dag", "pipeline"):
            parser = imagery_cli.create_parser(stage, "description")
            with (
                redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit) as exc,
            ):
                parser.parse_args(["--help"])
            self.assertEqual(exc.exception.code, 0)
        parser = imagery_cli.create_parser("dag", "test")
        self.assertFalse(parser.parse_args([]).submit)
        self.assertTrue(parser.parse_args(["--submit"]).submit)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--submit", "--dry-run"])

    def test_package_path_derivation(self):
        """Local metadata paths match the established deployment layout."""
        config = self.parse(
            "metadata", "--deployment-name", "calanus-20261007",
            "--mount-root", "/tmp/test-imagery-mounts",
        )
        paths = config.image_paths()
        self.assertEqual(
            paths["imgmetapath"],
            "/tmp/test-imagery-mounts/swfscesd-glider-imagery-metadata/"
            "2026/calanus-20261007/calanus-20261007-image-metadata.jsonl",
        )

    def test_invalid_config_prevents_stage_operations(self):
        """Every entry point validates before mounting or listing objects."""
        for module in (METADATA, MANIFESTS, DAG, PIPELINE):
            with patch.object(imagery_cli, "mount_bucket") as mount:
                with self.assertRaises(ValueError):
                    module.main(["--deployment-name", "invalid"])
                mount.assert_not_called()

    def test_file_stage_previews_do_not_mount(self):
        """Metadata and manifest previews only print derived paths."""
        for module in (METADATA, MANIFESTS):
            with patch.object(imagery_cli, "mount_bucket") as mount:
                with redirect_stdout(io.StringIO()) as output:
                    module.main([
                        "--deployment-name", "calanus-20261007", "--dry-run",
                    ])
                mount.assert_not_called()
                self.assertIn("calanus-20261007", output.getvalue())

    def test_command_exit_codes_and_streams(self):
        """Real CLI processes fail nonzero and keep preview stdout usable."""
        for name in (
            "extract-image-metadata", "generate-osi-json", "trigger-osi-dag",
            "run-imagery-pipeline",
        ):
            result = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / f"{name}.py"),
                    "--deployment-name", "invalid",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("deployment_name", result.stderr)
            self.assertEqual(result.stdout, "")
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "extract-image-metadata.py"),
                "--deployment-name", "calanus-20261007", "--dry-run",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("calanus-20261007", json.loads(result.stdout)["imagedir"])
        self.assertIn("INFO:", result.stderr)


class PipelineTests(unittest.TestCase):
    """Verify orchestration reuses stage CLIs and preserves safety controls."""

    def test_stage_order_and_effective_options(self):
        """One YAML plus CLI overrides supplies all stages consistently."""
        example = ROOT / "resources" / "imagery-deployment.example.yaml"
        with patch.object(PIPELINE.subprocess, "run") as run:
            PIPELINE.main([
                "--config", str(example),
                "--deployment-name", "chovy-20261005",
                "--directory-chunk-size", "7",
                "--num-cores", "2",
                "--directory-filter", "dir1", "dir2",
                "--mount-root", "/tmp/pipeline-test",
                "--imagery-bucket", "test-images",
                "--metadata-bucket", "test-metadata",
                "--model-type", "another-model",
                "--output-bucket", "test-results",
                "--overwrite", "--submit", "--test-limit", "1",
            ])
        calls = run.call_args_list
        self.assertEqual(len(calls), 3)
        stages = ("metadata", "manifests", "dag")
        for call, stage in zip(calls, stages):
            command = call.args[0]
            self.assertEqual(command[0], sys.executable)
            self.assertTrue(call.kwargs["check"])
            parser = imagery_cli.create_parser(stage, "test")
            args = parser.parse_args(command[2:])
            config = imagery_cli.load_config(args)
            self.assertEqual(config.deployment_name, "chovy-20261005")
            self.assertEqual(config.directory_chunk_size, 7)
            self.assertEqual(config.mount_root, Path("/tmp/pipeline-test"))
            self.assertEqual(config.imagery_bucket, "test-images")
            self.assertEqual(config.metadata_bucket, "test-metadata")
        self.assertEqual(
            [Path(call.args[0][1]).name for call in calls],
            [
                "extract-image-metadata.py", "generate-osi-json.py",
                "trigger-osi-dag.py",
            ],
        )
        self.assertIn("--num-cores", calls[0].args[0])
        self.assertIn("--directory-filter", calls[1].args[0])
        for call in calls[:2]:
            self.assertIn("--overwrite", call.args[0])
            self.assertNotIn("--test-limit", call.args[0])
        dag_args = imagery_cli.create_parser("dag", "test").parse_args(
            calls[2].args[0][2:],
        )
        self.assertTrue(dag_args.submit)
        self.assertEqual(dag_args.test_limit, 1)
        self.assertEqual(dag_args.model_type, "another-model")
        self.assertEqual(dag_args.output_bucket, "test-results")

    def test_default_prepares_files_but_previews_submission(self):
        """Default runs the writing stages but explicitly previews the DAG."""
        with patch.object(PIPELINE.subprocess, "run") as run:
            PIPELINE.main([
                "--deployment-name", "calanus-20261007",
                "--model-type", "test-model",
            ])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 3)
        for command in commands[:2]:
            self.assertNotIn("--dry-run", command)
            self.assertNotIn("--overwrite", command)
        self.assertIn("--dry-run", commands[2])
        self.assertNotIn("--submit", commands[2])

    def test_dry_run_never_executes_stages(self):
        """Path-only preview works without generated manifests or cloud access."""
        with patch.object(PIPELINE.subprocess, "run") as run:
            with patch.object(imagery_cli, "mount_bucket") as mount:
                with redirect_stdout(io.StringIO()) as output:
                    PIPELINE.main([
                        "--deployment-name", "calanus-20261007", "--dry-run",
                        "--model-type", "test-model",
                    ])
        run.assert_not_called()
        mount.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertEqual(
            plan["manifests"],
            "gs://swfscesd-glider-imagery-metadata/2026/"
            "calanus-20261007/osi-manifests-050dirs/",
        )

    def test_stops_on_each_stage_failure(self):
        """No subsequent stage runs after a failed subprocess."""
        for failure_index in range(3):
            failure = subprocess.CalledProcessError(9, ["stage"])
            with patch.object(
                PIPELINE.subprocess, "run",
                side_effect=[None] * failure_index + [failure],
            ) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    PIPELINE.main([
                        "--deployment-name", "calanus-20261007", "--submit",
                        "--model-type", "test-model",
                    ])
                self.assertEqual(run.call_count, failure_index + 1)

    def test_invalid_settings_start_no_processes(self):
        """Invalid options fail before extraction starts."""
        for options in (
            ["--deployment-name", "invalid"],
            ["--deployment-name", "calanus-20261007", "--test-limit", "0"],
        ):
            with patch.object(PIPELINE.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    PIPELINE.main(options)
                run.assert_not_called()

    def test_dry_run_cli_process(self):
        """The real runner dry run succeeds without preexisting stage outputs."""
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "run-imagery-pipeline.py"),
                "--config",
                str(ROOT / "resources" / "imagery-deployment.example.yaml"),
                "--dry-run",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["dag_id"], "optics-pipeline")
        self.assertIn("no cloud access", result.stderr)

    def test_entry_point_preserves_failed_stage_exit_code(self):
        """The runner exits nonzero with the failing stage's exit status."""
        script = str(ROOT / "scripts" / "run-imagery-pipeline.py")
        with (
            patch.object(
                sys, "argv",
                [
                    script, "--deployment-name", "calanus-20261007",
                    "--model-type", "test-model",
                ],
            ),
            patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(7, ["stage"]),
            ) as run,
            self.assertLogs(level="ERROR") as logs,
            self.assertRaises(SystemExit) as exc,
        ):
            runpy.run_path(script, run_name="__main__")
        self.assertEqual(exc.exception.code, 7)
        self.assertEqual(run.call_count, 1)
        self.assertIn("Earlier outputs may exist", logs.output[0])


class FileTests(unittest.TestCase):
    """Check metadata formats and explicit existing/partial-output behavior."""

    def test_publish_refuses_existing_file(self):
        """Exclusive output creation preserves existing content."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            destination = Path(directory) / "destination"
            source.write_text("new")
            destination.write_text("old")
            with self.assertRaises(FileExistsError):
                imagery_cli.publish_file(source, destination, overwrite=False)
            self.assertEqual(destination.read_text(), "old")
            imagery_cli.publish_file(source, destination, overwrite=True)
            self.assertEqual(destination.read_text(), "new")

    def test_metadata_formats_and_failure_staging(self):
        """Extraction preserves JSON shapes and does not publish on failure."""
        with tempfile.TemporaryDirectory() as directory:
            deployment = Path(directory) / "deployment.json"
            images = Path(directory) / "images.jsonl"
            fake_imagery = types.ModuleType("esdglider.imagery")
            fake_imagery.extract_deployment_metadata = MagicMock(
                return_value={"deployment": "calanus-20261007"},
            )
            fake_imagery.extract_image_metadata = MagicMock()
            executor = MagicMock()
            executor.__enter__.return_value.map.return_value = iter([
                {"n": "one.jpg", "p": "dir1"}, {"error": "invalid image"},
            ])
            with patch.dict(sys.modules, {"esdglider.imagery": fake_imagery}):
                with patch("esdglider.imagery", fake_imagery, create=True):
                    with patch.object(
                        METADATA, "ProcessPoolExecutor", return_value=executor,
                    ):
                        METADATA.run_pipeline(
                            [Path("one.jpg"), Path("two.jpg")],
                            "calanus-20261007", deployment, images,
                        )
                        self.assertEqual(
                            json.loads(deployment.read_text()),
                            {"deployment": "calanus-20261007"},
                        )
                        self.assertEqual(
                            [
                                json.loads(line)
                                for line in images.read_text().splitlines()
                            ],
                            [
                                {"n": "one.jpg", "p": "dir1"},
                                {"error": "invalid image"},
                            ],
                        )
                        with self.assertRaises(FileExistsError):
                            METADATA.run_pipeline(
                                [Path("one.jpg")], "calanus-20261007",
                                deployment, images,
                            )
                        executor.__enter__.return_value.map.side_effect = (
                            ValueError("broken")
                        )
                        with self.assertRaises(ValueError):
                            METADATA.run_pipeline(
                                [Path("one.jpg")], "calanus-20261007",
                                deployment, images, overwrite=True,
                            )
                        self.assertEqual(
                            json.loads(deployment.read_text()),
                            {"deployment": "calanus-20261007"},
                        )

    def test_generate_manifest_contents(self):
        """The command preserves the existing manifest format and image URIs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = imagery_cli.ImageryConfig(
                deployment_name="calanus-20261007", mount_root=root,
                directory_chunk_size=1,
            )
            metadata = Path(config.image_paths()["imgmetapath"])
            metadata.parent.mkdir(parents=True)
            metadata.write_text(
                '{"n": "one.jpg", "p": "dir1"}\n'
                '{"n": "two.png", "p": "dir2"}\n',
            )
            output = root / "manifests"
            options = [
                "--deployment-name", config.deployment_name,
                "--mount-root", str(root), "--directory-chunk-size", "1",
                "--output-directory", str(output),
            ]
            with patch.object(imagery_cli, "mount_bucket"):
                MANIFESTS.main(options)
                files = sorted(output.glob("*.json"))
                self.assertEqual(len(files), 2)
                self.assertEqual(json.loads(files[0].read_text()), {
                    "instances": [{"input_images": [
                        "gs://swfscesd-glider-imagery-data-in/"
                        "2026/calanus-20261007/images/dir1/one.jpg",
                    ]}],
                })
                with self.assertRaises(FileExistsError):
                    MANIFESTS.main(options)
                stale = output / "unexpected.json"
                stale.write_text("{}")
                with self.assertRaisesRegex(FileExistsError, "stale"):
                    MANIFESTS.main(options + ["--overwrite"])
                self.assertEqual(stale.read_text(), "{}")


class DagTests(unittest.TestCase):
    """Verify stable mapping, safe previews, and live-submission guards."""

    def setUp(self):
        """Create a config and two nonconsecutive manifest plans."""
        self.config = imagery_cli.ImageryConfig(
            "calanus-20261007", model_type="test-model",
        )
        self.names = [
            self.config.manifest_prefix
            + f"calanus-20261007-image-manifest-chunk{chunk:02d}.json"
            for chunk in (10, 2)
        ]
        self.plans = DAG.build_plans(self.names, self.config)

    def test_stable_chunk_mapping(self):
        """Filtering and sorting cannot change a manifest's output or run ID."""
        self.assertEqual(
            self.plans[0].conf["output_folder"].split("/")[-1], "02",
        )
        self.assertEqual(
            self.plans[1].conf["output_folder"].split("/")[-1], "10",
        )
        self.assertEqual(
            DAG.build_plans(self.names[:1], self.config)[0], self.plans[1],
        )

    def test_bad_manifest_names(self):
        """Missing, unexpected, or duplicate chunks fail before submission."""
        for names in (
            [], [self.config.manifest_prefix + "unexpected.json"],
            [self.names[0], self.names[0].replace("chunk10", "chunk010")],
        ):
            with self.assertRaises(ValueError):
                DAG.build_plans(names, self.config)

    def test_preview_never_submits(self):
        """Default and explicit dry runs list objects but never call Airflow."""
        for options in ([], ["--dry-run"]):
            client = MagicMock()
            client.bucket.return_value.list_blobs.side_effect = [
                [types.SimpleNamespace(name=name) for name in self.names],
                [], [],
            ]
            with patch("google.cloud.storage.Client", return_value=client):
                with patch.object(DAG, "submit_plans") as submit:
                    with redirect_stdout(io.StringIO()) as output:
                        DAG.main([
                            "--deployment-name", self.config.deployment_name,
                            "--model-type", self.config.model_type,
                            *options,
                        ])
                    submit.assert_not_called()
                    payloads = [
                        json.loads(line)
                        for line in output.getvalue().splitlines()
                    ]
                    self.assertEqual(len(payloads), 2)
                    self.assertFalse(payloads[0]["existing_output_objects"])

    def test_live_payload_and_timeout(self):
        """Live submission preserves model parameters and stable run IDs."""
        session = MagicMock()
        session.get.return_value.status_code = 404
        session.post.return_value.status_code = 201
        DAG.submit_plans(self.plans, set(), self.config, session)
        self.assertEqual(session.post.call_count, 2)
        payload = session.post.call_args_list[0].kwargs["json"]
        self.assertEqual(payload["conf"], self.plans[0].conf)
        self.assertEqual(payload["dag_run_id"], self.plans[0].dag_run_id)
        self.assertEqual(session.post.call_args.kwargs["timeout"], 60)

    def test_existing_run_states(self):
        """Successful runs skip; failed/active runs block, never duplicate."""
        for state in ("success", "failed", "running", "queued", None):
            session = MagicMock()
            session.get.return_value.status_code = 200
            session.get.return_value.json.return_value = {"state": state}
            if state == "success":
                DAG.submit_plans(self.plans, set(), self.config, session)
            else:
                with self.assertRaises(RuntimeError):
                    DAG.submit_plans(self.plans, set(), self.config, session)
            session.post.assert_not_called()

    def test_all_preflight_before_any_submission(self):
        """An ambiguous later output prevents all selected new submissions."""
        session = MagicMock()
        session.get.return_value.status_code = 404
        with self.assertRaises(FileExistsError):
            DAG.submit_plans(
                self.plans, {self.plans[1].conf["output_folder"]},
                self.config, session,
            )
        session.post.assert_not_called()

    def test_submission_error_propagates(self):
        """A rejected HTTP submission cannot produce a success exit."""
        import requests

        session = MagicMock()
        session.get.return_value.status_code = 404
        session.post.return_value.raise_for_status.side_effect = (
            requests.HTTPError("rejected")
        )
        with self.assertRaises(requests.HTTPError):
            DAG.submit_plans(self.plans, set(), self.config, session)


if __name__ == "__main__":
    unittest.main()
