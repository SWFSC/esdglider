import logging
import os
from pathlib import Path

from dbdreader.decompress import decompress_file, is_compressed

from esdglider import gcp, paths

"""
This script is intended to help users quickly generate decompressed
binary files, as a light wrapper around dbdreader functions.
"""

deployment_name = "amlr30-20260114"
mode = "delayed"

home = Path.home()

mnt_path = home / "gcs-mnt"
config_path = home / "glider-lab" / "deployment-configs"
cac_path = home / "standard-glider-files" / "Cache"

logs_bucket_name = "swfscesd-glider-logs"
data_in_bucket_name = "swfscesd-glider-deployments-data-in"

logs_path = mnt_path / logs_bucket_name
data_in_path = mnt_path / data_in_bucket_name

# deployment_info = {
#     "deployment_name": deployment_name,
#     "deploymentyaml": os.path.join(config_path, f"{deployment_name}.yml"),
#     "mode": "delayed",
# }
log_file_name = f"{deployment_name}-{mode}-decompress.log"

if __name__ == "__main__":
    # bucket_name = "amlr-gliders-deployments-dev"
    # deployments_path = os.path.join("/home/sam_woodman_noaa_gov", bucket_name)
    gcp.gcs_mount_bucket(logs_bucket_name, logs_path, ro=False)
    gcp.gcs_mount_bucket(data_in_bucket_name, data_in_path, ro=False)

    logging.basicConfig(
        filename=logs_path / log_file_name,
        filemode="w",
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    glider_paths = paths.get_path_glider(
        deployment_name = deployment_name, 
        mode = mode, 
        config_path = config_path, 
        data_in_path = data_in_path, 
        cac_path = cac_path, 
    )

    binarydir = glider_paths["binarydir"]
    # binarydir = "/home/user/amlr30-20260114-compressed/delayed"
    binarydir_files = os.listdir(binarydir)
    logging.info("There are %s total files in %s", len(binarydir_files), binarydir)

    dcd_files = list(Path(binarydir).glob("*.dcd"))
    ecd_files = list(Path(binarydir).glob("*.ecd"))
    logging.info("There are %s dcd files", len(dcd_files))
    logging.info("There are %s ecd files", len(ecd_files))

    # FileDecompressor.decompress(dcd1)
    logging.info("decompressing all files in %s", binarydir)
    for fin in binarydir_files:
        logging.debug("Working on %s", fin)
        if is_compressed(fin):
            try:
                decompress_file(os.path.join(binarydir, fin))
            except Exception as e:
                logging.error("Error decompressing %s: %s", fin, e)
        else:
            logging.debug("skipping %s", fin)

    binarydir_files = os.listdir(binarydir)
    logging.info("There are now %s files in %s", len(binarydir_files), binarydir)
