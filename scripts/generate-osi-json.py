import logging
import json
from esdglider import gcp, imagery, paths
from pathlib import Path


logger = logging.getLogger(__name__)

# deployment_name = "amlr08-20220513"  #Dir0000
# target_directories = {"Dir0000", "Dir0001"}

deployment_name = "calanus-20260403"  #dir0000001
target_directories = {f"dir{i:07d}" for i in range(0, 20)}
output_filepath = f"/home/user/{deployment_name}-image-manifest1.json"

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

    
    # Example Usage:
    imagery.generate_osi_manifest(
        jsonl_filepath=img_paths["imgmetapath"],
        output_filepath=output_filepath,
        target_dirs=target_directories,
        deployment_name=deployment_name, 
        log_interval=1000, 
    )