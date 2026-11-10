"""Extract deployment JSON and image JSONL metadata using shared YAML settings.

Reads images in the imagery bucket at {year}/{deployment}/images/. Writes
metadata beneath {year}/{deployment}/ in the metadata bucket. Logs go to
stderr, not the logs bucket. --dry-run prints paths without mounting buckets.
Existing outputs require --overwrite. Extraction is staged locally before
publication; failed publication may leave partial outputs requiring inspection.
"""

from concurrent.futures import ProcessPoolExecutor
import json
import logging
from pathlib import Path
import tempfile

from esdglider import imagery_cli

logger = logging.getLogger(__name__)


def run_pipeline(
    files: list[Path],
    deployment_name: str,
    depl_meta_file: Path,
    img_meta_file: Path,
    num_cores: int | None = None,
    *,
    overwrite: bool = False,
) -> None:
    """Extract metadata locally, then publish the two completed files.

    Args:
        files: Nonempty ordered image paths.
        deployment_name: Deployment identifier.
        depl_meta_file: Deployment JSON destination.
        img_meta_file: Image JSONL destination.
        num_cores: Positive worker count, or None for the executor default.
        overwrite: Whether existing outputs may be replaced.

    Raises:
        ValueError: If images or the worker count are invalid.
        OSError: If reading, staging, or publication fails.
        FileExistsError: If an output exists without overwrite permission.
    """
    if not files:
        raise ValueError("No image files found.")
    if num_cores is not None and (type(num_cores) is not int or num_cores < 1):
        raise ValueError("num_cores must be a positive integer.")
    imagery_cli.check_outputs(
        [depl_meta_file, img_meta_file], overwrite=overwrite,
    )
    from esdglider import imagery

    with tempfile.TemporaryDirectory(prefix="image-metadata-") as directory:
        deployment_output = Path(directory) / "deployment.json"
        image_output = Path(directory) / "images.jsonl"
        metadata = imagery.extract_deployment_metadata(
            files[0], deployment_name,
        )
        with deployment_output.open("w") as stream:
            json.dump(metadata, stream, indent=4)
        with image_output.open("w", encoding="utf-8") as stream:
            with ProcessPoolExecutor(max_workers=num_cores) as executor:
                for result in executor.map(
                    imagery.extract_image_metadata, files,
                ):
                    stream.write(json.dumps(result) + "\n")
        imagery_cli.publish_file(
            deployment_output, depl_meta_file, overwrite=overwrite,
        )
        imagery_cli.publish_file(
            image_output, img_meta_file, overwrite=overwrite,
        )


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and run metadata extraction.

    Args:
        argv: Optional arguments; defaults to the process command line.

    Raises:
        ValueError: If configuration or discovered input files are invalid.
        OSError: If configuration, mounts, or metadata cannot be accessed.
    """
    parser = imagery_cli.create_parser("metadata", __doc__)
    args = parser.parse_args(argv)
    config = imagery_cli.load_config(args)
    imagery_cli.configure_logging()
    image_paths = config.image_paths()
    if args.dry_run:
        print(json.dumps({
            key: str(image_paths[key])
            for key in ("imagedir", "deplmetapath", "imgmetapath")
        }, indent=2))
        return
    imagery_cli.mount_bucket(config, config.imagery_bucket, read_only=True)
    imagery_cli.mount_bucket(config, config.metadata_bucket, read_only=False)
    from esdglider import utils

    files = sorted(
        path for path in Path(image_paths["imagedir"]).rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not files:
        raise ValueError(f"No images found in {image_paths['imagedir']}.")
    for path in files:
        if "checkpoint" in str(path):
            logger.warning(
                "Questionable image path containing checkpoint: %s", path,
            )
    utils.check_string_length([path.name for path in files])
    logger.info("Extracting metadata for %d images", len(files))
    run_pipeline(
        files, config.deployment_name,
        Path(image_paths["deplmetapath"]), Path(image_paths["imgmetapath"]),
        config.num_cores, overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
