"""Generate OSI manifest JSON files from deployment image metadata.

Reads the image-metadata JSONL file in the metadata bucket. Writes manifests
under {year}/{deployment}/osi-manifests-{chunk_size:03d}dirs/, or to an explicit
local --output-directory. --dry-run prints paths without mounting or writing.
Existing manifests require --overwrite. All chunks are staged locally before
publication; a failed publication can leave partial outputs.
"""

import json
import logging
from pathlib import Path
import tempfile

from esdglider import imagery_cli


def main(argv: list[str] | None = None) -> None:
    """Parse shared settings and generate one manifest per directory chunk.

    Args:
        argv: Optional arguments; defaults to the process command line.

    Raises:
        ValueError: If configuration or the directory selection is invalid.
        OSError: If metadata cannot be read or manifests cannot be written.
        FileExistsError: If outputs exist without --overwrite.
    """
    parser = imagery_cli.create_parser("manifests", __doc__)
    args = parser.parse_args(argv)
    config = imagery_cli.load_config(args)
    imagery_cli.configure_logging()
    image_paths = config.image_paths()
    output_directory = (
        args.output_directory.expanduser().resolve()
        if args.output_directory is not None
        else config.mount_root / config.metadata_bucket / config.manifest_prefix
    )
    if args.dry_run:
        print(json.dumps({
            "input": image_paths["imgmetapath"],
            "output_directory": str(output_directory),
            "directory_chunk_size": config.directory_chunk_size,
        }, indent=2))
        return
    imagery_cli.mount_bucket(
        config, config.metadata_bucket,
        read_only=args.output_directory is not None,
    )
    from esdglider import imagery

    directories = imagery.get_unique_directories(image_paths["imgmetapath"])
    if config.directory_filter is not None:
        missing = set(config.directory_filter) - set(directories)
        if missing:
            raise ValueError(f"Unknown image directories: {sorted(missing)}")
        directories = [
            item for item in directories if item in config.directory_filter
        ]
    if not directories:
        raise ValueError("No image directories found in metadata.")
    chunks = [
        directories[start:start + config.directory_chunk_size]
        for start in range(0, len(directories), config.directory_chunk_size)
    ]
    outputs = [
        output_directory
        / f"{config.deployment_name}-image-manifest-chunk{number:02d}.json"
        for number in range(1, len(chunks) + 1)
    ]
    stale_outputs = set(output_directory.glob("*.json")) - set(outputs)
    if stale_outputs:
        raise FileExistsError(
            "Unexpected or stale manifests exist; archive them before "
            f"regenerating: {sorted(map(str, stale_outputs))}"
        )
    imagery_cli.check_outputs(outputs, overwrite=args.overwrite)
    base_uri = (
        f"gs://{config.imagery_bucket}/{config.year}/"
        f"{config.deployment_name}/images"
    )
    with tempfile.TemporaryDirectory(prefix="osi-manifests-") as directory:
        for chunk, output in zip(chunks, outputs):
            imagery.generate_osi_manifest(
                jsonl_filepath=image_paths["imgmetapath"],
                output_filepath=Path(directory) / output.name,
                target_dirs=chunk, base_uri=base_uri, log_interval=100000,
            )
        for output in outputs:
            imagery_cli.publish_file(
                Path(directory) / output.name, output, overwrite=args.overwrite,
            )
    logging.info("Generated %d manifests", len(outputs))


if __name__ == "__main__":
    main()
