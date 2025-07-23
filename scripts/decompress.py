import logging
import os
import pathlib

from dbdreader.decompress import decompress_file, is_compressed

from esdglider import gcp, glider

"""
This script is intended to help users quickly generate decompressed
binary files, as a light wrapper around dbdreader functions.
"""

deployment_name = "george-20240907"
config_path = "/home/sam_woodman_noaa_gov/glider-lab/deployment-configs"

deployment_info = {
    "deployment_name": deployment_name,
    "deploymentyaml": os.path.join(config_path, f"{deployment_name}.yml"),
    "mode": "delayed",
}
log_file_name = f"{deployment_name}-delayed-decompress.log"

if __name__ == "__main__":
    bucket_name = "amlr-gliders-deployments-dev"
    deployments_path = os.path.join("/home/sam_woodman_noaa_gov", bucket_name)
    gcp.gcs_mount_bucket(bucket_name, deployments_path, ro=False)

    paths = glider.get_path_glider(deployment_info, deployments_path)

    logging.basicConfig(
        filename=os.path.join(paths["logdir"], log_file_name),
        filemode="w",
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    binarydir = paths["binarydir"]
    binarydir_files = os.listdir(binarydir)
    logging.info("There are %s total files in %s", len(binarydir_files), binarydir)

    dcd_files = list(pathlib.Path(binarydir).glob("*.dcd"))
    ecd_files = list(pathlib.Path(binarydir).glob("*.ecd"))
    logging.info("There are %s dcd files", len(dcd_files))
    logging.info("There are %s ecd files", len(ecd_files))

    # FileDecompressor.decompress(dcd1)
    logging.info("decompressing all files in %s", binarydir)
    for fin in binarydir_files:
        logging.debug(fin)
        if is_compressed(fin):
            decompress_file(os.path.join(binarydir, fin))
        else:
            logging.debug("skipping %s", fin)

    binarydir_files = os.listdir(binarydir)
    logging.info("There are now %s files in %s", len(binarydir_files), binarydir)
