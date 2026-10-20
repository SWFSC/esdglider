import logging
from pathlib import Path

from esdglider import defaults, gcp, imagery, paths, utils

logger = logging.getLogger(__name__)

deployment_name = "calanus-20260824" # Deployment name
dir_chunk_size = 50                  # Number of directories per json file

# List of directories to include; set to None to include all. 
# Eg: ["dir0000004"]
dir_filter = None 

home = Path.home()
# mnt_path = home / "mnt-gcs"

use_tmp_dir = False

# imagery_in_bucket_name = "swfscesd-glider-imagery-data-in"
# imagery_in_path = mnt_path / imagery_in_bucket_name
# imagery_meta_bucket_name = "swfscesd-glider-imagery-metadata"
# imagery_meta_path = mnt_path / imagery_meta_bucket_name



#------------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.captureWarnings(True)

    img_paths = paths.get_path_imagery(
        deployment_name = deployment_name, 
        home_path=home, 
    )
    gcp.gcs_mount_bucket(defaults.BUCKET_IMAGERY_META, img_paths["metadir"], ro=False)

    if not use_tmp_dir:
        output_path_pre = Path(img_paths["metadir"]) / f"osi-manifests-{dir_chunk_size:03d}dirs"
    else:
        output_path_pre = Path(f"/home/user/tmp-meta/{deployment_name}/osi-manifests-{dir_chunk_size:03d}dirs")
    output_path_pre.mkdir(parents=True, exist_ok=True)

    unique_dirs = imagery.get_unique_directories(img_paths["imgmetapath"])
    if dir_filter is not None:
        unique_dirs = [d for d in unique_dirs if d in dir_filter]

    # Chunk directories into manageable sizes for processing
    total_dirs = len(unique_dirs)
    for i in range(0, total_dirs, dir_chunk_size):
        chunk = unique_dirs[i : i + dir_chunk_size]
        chunk_num = (i // dir_chunk_size) + 1        
        output_filepath = output_path_pre / f"{deployment_name}-image-manifest-chunk{chunk_num:02d}.json"
        logger.info(
            "First / last directory in chunk %d: %s / %s", 
            chunk_num, 
            chunk[0], 
            chunk[-1]
        )
        logger.info("Writing chunk to %s", output_filepath)
        logger.debug("Chunk %d directories: %s", chunk_num, chunk)

        # Usage:
        bucket_name = defaults.BUCKET_IMAGERY_IN
        yr = utils.get_path_year(deployment_name)
        base_uri = f"gs://{bucket_name}/{yr}/{deployment_name}/images"
        imagery.generate_osi_manifest(
            jsonl_filepath=img_paths["imgmetapath"],
            output_filepath=output_filepath,
            target_dirs=chunk,
            base_uri=base_uri, 
            log_interval=100000, 
        )

        if use_tmp_dir:
            print(
                "Command to run to copy from temp directory to bucket, if necessary:\n"
                f"gcloud storage cp -r {output_path_pre} "
                f"gs://{defaults.BUCKET_IMAGERY_META}/"
                f"{utils.get_path_year(deployment_name)}/{deployment_name}/"
            )
        