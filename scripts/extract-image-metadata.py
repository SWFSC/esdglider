"""Extract metadata for images in a configured glider deployment.

When run as a script, reads JPEG and PNG images from the imagery-input bucket
at ``{year}/{deployment_name}/images/``. Logs questionable image paths and
filename lengths, extracts deployment-level metadata from the first image,
and extracts per-image metadata in parallel.

Writes two files to the imagery-metadata bucket at
``{year}/{deployment_name}/``:

* ``{deployment_name}-deployment-metadata.json``
* ``{deployment_name}-image-metadata.jsonl``

Writes a run log to the logs bucket at
``{deployment_name}-image-metadata-extraction.log``. Metadata extraction is
skipped if either output file already exists. Bucket names are configured
below or in ``esdglider.defaults``.
"""

import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from esdglider import defaults, gcp, imagery, paths, utils

logger = logging.getLogger(__name__)

deployment_name = "calanus-20261007"
run_pipeline_bool = True

home = Path.home()
mnt_path = home / "mnt-gcs"

logs_bucket_name = "swfscesd-glider-logs" #defaults.BUCKET_LOGS
imagery_in_bucket_name = defaults.BUCKET_IMAGERY_IN
imagery_meta_bucket_name = defaults.BUCKET_IMAGERY_META

logs_path = home / "mnt-gcs" / logs_bucket_name
imagery_in_path = mnt_path / imagery_in_bucket_name
imagery_meta_path = mnt_path / imagery_meta_bucket_name

log_file_name = f"{deployment_name}-image-metadata-extraction.log"
# num_cores = os.cpu_count()  # Uses all available cores
# INPUT_DIR = Path("./images")


def run_pipeline(
    files: list[Path],
    deployment_name: str,
    depl_meta_file: Path,
    img_meta_file: Path,
    num_cores: int | None = None,
):
    """Generate deployment-level and image-specific metadata files.

    The deployment-level metadata is extracted from the first image in
    ``files``. Per-image metadata is then extracted in parallel and written
    as newline-delimited JSON.

    Args:
        files: Image file paths. Must contain at least one path.
        deployment_name: Name of the deployment.
        depl_meta_file: Output path for deployment-level metadata.
        img_meta_file: Output path for per-image metadata.
        num_cores: Number of worker processes. If ``None``, use the value
            returned by ``os.cpu_count()``.

    Returns:
        None.

    Raises:
        IndexError: If ``files`` is empty.
        OSError: If an output file or its parent directory cannot be opened
            or created.
    """

    if num_cores is None:
        num_cores = os.cpu_count()

    if depl_meta_file.is_file():
        logger.error(f"depl_meta_file file ({depl_meta_file}) already exists")
        return

    if img_meta_file.is_file():
        logger.error(f"img_meta_file file ({img_meta_file}) already exists")
        return

    # Generate Manifest (from first valid image)
    depl_metadata = imagery.extract_deployment_metadata(files[0], deployment_name)
    logger.info("Writing deployment-level metadata to %s", depl_meta_file)
    depl_meta_file.parent.mkdir(parents=True, exist_ok=True)
    with depl_meta_file.open("w") as f:
        json.dump(depl_metadata, f, indent=4)
    logger.info("Completed deployment-level metadata")

    # Generate index via multiprocessing.
    logger.info("Extracting image-level metadata, and writing to %s", img_meta_file)
    img_meta_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Using %s cores", num_cores)
    with img_meta_file.open("a", encoding="utf-8") as f:  # noqa: SIM117
        with ProcessPoolExecutor(max_workers=num_cores) as executor:
            for result in tqdm(
                executor.map(imagery.extract_image_metadata, files),
                total=len(files),
                unit="images",
            ):
                f.write(json.dumps(result) + "\n")
    logger.info("Completed image-level metadata")


if __name__ == "__main__":
    gcp.gcs_mount_bucket(logs_bucket_name, logs_path, ro=False)
    logging.basicConfig(
        filename=logs_path / log_file_name,
        filemode="w",
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    print(f"Writing logs to {logs_path / log_file_name}")
    logger.info(
        "Starting image metadata extraction for deployment %s, via script %s",
        deployment_name,
        __file__,
    )

    # Mount buckets and get paths.
    gcp.gcs_mount_bucket(imagery_in_bucket_name, imagery_in_path, ro=True)
    gcp.gcs_mount_bucket(imagery_meta_bucket_name, imagery_meta_path, ro=False)

    img_paths = paths.get_path_imagery(
        deployment_name=deployment_name,
        imagery_in_path=imagery_in_path,
        imagery_meta_path=imagery_meta_path,
        data_out_path="",
    )

    # Generate file list.
    # Supports both cases and png extensions
    extensions = {".jpg", ".jpeg", ".png"}

    # Gather all files that match the extension set
    logger.info("Getting all file paths")
    files = [
        p
        for p in Path(img_paths["imagedir"]).rglob("*")
        if p.suffix.lower() in extensions
    ]
    if not files:
        logger.error("No files")
    else:
        logger.info("There are %s files", len(files))

        # Check method 1 - substring
        logger.info("Checking for any questionable paths via substring check")
        sub_check = "checkpoint"
        file_check = [i for i in files if sub_check in str(i)]
        if file_check:
            logger.warning("The substring '%s' is in the following paths:", sub_check)
            for file_path in file_check:
                logger.warning("path: %s", file_path)

        # Check method 2 - length
        logger.info("Checking for any questionable paths via length check")
        utils.check_string_length([str(i.name) for i in files])

        # tmp_folder = home / "tmp-meta" / deployment_name
        # depl_meta_file = (tmp_folder / Path(img_paths["deplmetapath"]).name)
        # img_meta_file = (tmp_folder / Path(img_paths["imgmetapath"]).name)

        depl_meta_file = Path(img_paths["deplmetapath"])
        img_meta_file = Path(img_paths["imgmetapath"])

        if run_pipeline_bool:
            run_pipeline(
                files=files,
                deployment_name=deployment_name,
                depl_meta_file=depl_meta_file,
                img_meta_file=img_meta_file,
                num_cores=os.cpu_count(),
            )
        logger.info("There are %s files", len(files))

        # print(f"gcloud storage mv {str(depl_meta_file)} gs://{img_paths["deplmetapath"]}")
        # print(f"gcloud storage mv {str(img_meta_file)} gs://{img_paths["imgmetapath"]}")
