import logging
from pathlib import Path

from esdglider import gcp, imagery, paths

logger = logging.getLogger(__name__)

deployment_name = "calanus-20260403"
dir_chunk_size = 100
output_path_pre = Path(f"/home/user/tmp-meta/{deployment_name}/osi-manifests")

home = Path.home()
mnt_path = home / "mnt-gcs"

# imagery_in_bucket_name = "swfscesd-glider-imagery-data-in"
# imagery_in_path = mnt_path / imagery_in_bucket_name
imagery_meta_bucket_name = "swfscesd-glider-imagery-metadata"
imagery_meta_path = mnt_path / imagery_meta_bucket_name



#------------------------------------------------------------------------------
if __name__ == "__main__":
    # gcp.gcs_mount_bucket(imagery_in_bucket_name, imagery_in_path, ro=True)
    gcp.gcs_mount_bucket(imagery_meta_bucket_name, imagery_meta_path, ro=False)

    logging.basicConfig(
        # filename=logs_path / log_file_name,
        # filemode="w",
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.captureWarnings(True)
    # logger.info("Beginning scheduled processing for %s", file_info)
    # print(f"Writing logs to {logs_path / log_file_name}")

    img_paths = paths.get_path_imagery(
        deployment_name = deployment_name, 
        # imagery_in_path = imagery_in_path, 
        imagery_meta_path = imagery_meta_path, 
        # data_out_path = data_out_path, 
    )
    output_path_pre.mkdir(parents=True, exist_ok=True)

    unique_dirs = imagery.get_unique_directories(img_paths["imgmetapath"])

    # Chunk directories into manageable sizes for processing
    total_dirs = len(unique_dirs)
    for i in range(0, total_dirs, dir_chunk_size):
        chunk = unique_dirs[i : i + dir_chunk_size]
        # print(i)
        # print(chunk)
        chunk_num = (i // dir_chunk_size) + 1        
        output_filepath = output_path_pre / f"{deployment_name}-image-manifest-chunk{chunk_num:02d}.json"
        logger.info(
            "First and last directory in chunk %d: %s, %s", 
            chunk_num, 
            chunk[0], 
            chunk[-1]
        )
        logger.info("Writing chunk to %s", output_filepath)
        logger.debug("Chunk %d directories: %s", chunk_num, chunk)

        # Usage:
        imagery.generate_osi_manifest(
            jsonl_filepath=img_paths["imgmetapath"],
            output_filepath=output_filepath,
            target_dirs=chunk,
            deployment_name=deployment_name, 
            log_interval=10000, 
        )