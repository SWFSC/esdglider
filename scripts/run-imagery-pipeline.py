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

from esdglider import imagery_cli


def run_pipeline(
    config: imagery_cli.ImageryConfig,
    *,
    submit: bool = False,
    overwrite: bool = False,
    test_limit: int | None = None,
) -> None:
    """Run existing stage commands with the same effective configuration.

    Args:
        config: Validated shared configuration.
        submit: Whether to submit live DAG runs instead of previewing.
        overwrite: Whether to replace existing metadata and manifests.
        test_limit: Optional limit on DAG submissions, not file generation.

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
    for key in (
        "composer_url", "dag_id", "model_type", "yaml_config_path",
        "output_bucket",
    ):
        dag.extend(["--" + key.replace("_", "-"), getattr(config, key)])
    dag.append("--submit" if submit else "--dry-run")
    if test_limit is not None:
        dag.extend(["--test-limit", str(test_limit)])
    scripts = Path(__file__).resolve().parent
    for script, options in (
        ("extract-image-metadata.py", metadata),
        ("generate-osi-json.py", manifests),
        ("trigger-osi-dag.py", dag),
    ):
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
        print(json.dumps({
            "images": str(image_paths["imagedir"]),
            "deployment_metadata": str(image_paths["deplmetapath"]),
            "image_metadata": str(image_paths["imgmetapath"]),
            "manifests": (
                f"gs://{config.metadata_bucket}/{config.manifest_prefix}"
            ),
            "model_outputs": (
                f"gs://{config.output_bucket}/{config.output_prefix}/"
                "{chunk_number:02d}/"
            ),
            "dag_id": config.dag_id,
            "composer_url": config.composer_url,
            "test_limit": args.test_limit,
        }, indent=2))
        logging.info(
            "Paths only; no cloud access, extraction, writes, or submissions."
        )
        return
    run_pipeline(
        config, submit=args.submit, overwrite=args.overwrite,
        test_limit=args.test_limit,
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
