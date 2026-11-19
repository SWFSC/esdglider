"""Extract metadata, generate OSI manifests, then preview or submit DAG runs.

Runs the three independent scripts in order using the current Python
interpreter and one validated effective configuration. Stops on the first
failed stage. By default, writes metadata/manifests and previews submissions;
--submit explicitly enables live DAG requests. --dry-run only prints derived
paths, without cloud access or file writes. This command does not wait for
remote inference to complete or roll back earlier stages on failure.
"""

import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile

from esdglider import imagery_cli, imagery_run_records


def run_pipeline(
    config: imagery_cli.ImageryConfig,
    *,
    submit: bool = False,
    overwrite: bool = False,
    test_limit: int | None = None,
    context: dict | None = None,
    resume_run_id: str | None = None,
) -> None:
    """Run existing stage commands with the same effective configuration.

    Args:
        config: Validated shared configuration.
        submit: Whether to submit live DAG runs instead of previewing.
        overwrite: Whether to replace existing metadata and manifests.
        test_limit: Optional limit on DAG submissions, not file generation.
        context: Original user configuration and command options to archive in
            GCS. See prepare_run in trigger-osi-dag.py for a dictionary example.
        resume_run_id: Existing run ID; run only its DAG submission stage.

    Raises:
        subprocess.CalledProcessError: If any stage fails.
        OSError: If a stage process cannot be started.
    """
    if not config.model_type:
        raise ValueError("Choose a model explicitly before running the pipeline.")
    common = []
    for key in (
        "deployment_name", "directory_chunk_size", "mount_root",
        "imagery_bucket", "metadata_bucket",
    ):
        common.extend([
            "--" + key.replace("_", "-"), str(getattr(config, key)),
        ])
    metadata = list(common)
    if config.num_cores is not None:
        metadata.extend(["--num-cores", str(config.num_cores)])
    manifests = list(common)
    if config.directory_filter is not None:
        manifests.extend(["--directory-filter", *config.directory_filter])
    if overwrite:
        metadata.append("--overwrite")
        manifests.append("--overwrite")
    dag = list(common)
    if config.num_cores is not None:
        dag.extend(["--num-cores", str(config.num_cores)])
    if config.directory_filter is not None:
        dag.extend(["--directory-filter", *config.directory_filter])
    for key in (
        "composer_url", "dag_id", "model_type", "yaml_config_path",
        "output_bucket",
    ):
        dag.extend(["--" + key.replace("_", "-"), getattr(config, key)])
    dag.append("--submit" if submit else "--dry-run")
    if test_limit is not None:
        dag.extend(["--test-limit", str(test_limit)])
    if resume_run_id is not None:
        dag.extend(["--resume-run-id", resume_run_id])
    scripts = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="imagery-run-context-") as directory:
        if context is not None and submit and resume_run_id is None:
            context_path = Path(directory) / "context.json"
            context_path.write_text(json.dumps(context), encoding="utf-8")
            dag.extend(["--run-context", str(context_path)])
        stages = [
            ("extract-image-metadata.py", metadata),
            ("generate-osi-json.py", manifests),
            ("trigger-osi-dag.py", dag),
        ]
        if resume_run_id is not None:
            stages = stages[-1:]
        for script, options in stages:
            logging.info("Running stage: %s", script)
            subprocess.run(
                [sys.executable, str(scripts / script), *options], check=True,
            )
    logging.info(
        "Pipeline stages finished; remote inference completion is not checked."
    )


def main(argv: list[str] | None = None) -> None:
    """Validate shared settings and run or preview the complete workflow.

    Args:
        argv: Optional arguments; defaults to the process command line.

    Raises:
        ValueError: If configuration is invalid.
        OSError: If configuration or stage processes cannot be accessed.
        subprocess.CalledProcessError: If a stage fails.
    """
    parser = imagery_cli.create_parser("pipeline", __doc__)
    args = parser.parse_args(argv)
    config = imagery_cli.load_config(args)
    imagery_cli.configure_logging()
    if args.dry_run:
        image_paths = config.image_paths()
        location = imagery_run_records.RunLocation(
            config.output_bucket, config.deployment_name,
            args.resume_run_id or imagery_run_records.new_run_id(),
        )
        print(json.dumps({
            "images": str(image_paths["imagedir"]),
            "deployment_metadata": str(image_paths["deplmetapath"]),
            "image_metadata": str(image_paths["imgmetapath"]),
            "manifests": (
                f"gs://{config.metadata_bucket}/{config.manifest_prefix}"
            ),
            "model_outputs": (
                f"gs://{config.output_bucket}/{location.output_prefix}/"
                "{chunk_number:02d}/"
            ),
            "run_id": location.run_id,
            "run_config": (
                f"gs://{location.bucket_name}/"
                f"{location.config_object('resolved.json')}"
            ),
            "dag_id": config.dag_id,
            "composer_url": config.composer_url,
            "test_limit": args.test_limit,
        }, indent=2))
        logging.info(
            "Paths only; no cloud access, extraction, writes, or submissions."
        )
        return
    context = None
    if args.submit and args.resume_run_id is None:
        context = imagery_run_records.capture_context(config, args)
    run_pipeline(
        config, submit=args.submit, overwrite=args.overwrite,
        test_limit=args.test_limit,
        context=context, resume_run_id=args.resume_run_id,
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        logging.error(
            "Pipeline stopped after stage failure (exit %d). "
            "Earlier outputs may exist; inspect before retrying.",
            exc.returncode,
        )
        sys.exit(exc.returncode if exc.returncode > 0 else 1)
