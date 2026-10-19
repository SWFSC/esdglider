import logging
import os
import pathlib

from dbdreader.decompress import decompress_file

from esdglider import gcp, glider
"""
This script is intended to help users quickly generate decompressed
binary files, as a light wrapper around dbdreader functions.
"""

deployment_name = "calanus-20250617"
config_path = "/home/sam_woodman_noaa_gov/glider-lab/deployment-configs"

deployment_info = {
    "deployment_name": deployment_name,
    "deploymentyaml": os.path.join(config_path, f"{deployment_name}.yml"),
    "mode": "delayed",
}
log_file_name = f"cache-decompress.log"

if __name__ == "__main__":
    bucket_name = "amlr-gliders-deployments-dev"
    deployments_path = os.path.join("/home/sam_woodman_noaa_gov", bucket_name)
    gcp.gcs_mount_bucket(bucket_name, deployments_path, ro=False)

    paths = glider.get_path_glider(deployment_info, deployments_path)

    logging.basicConfig(
        filename=os.path.join(paths["logdir"], log_file_name),
        filemode="a",
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    cacdir = pathlib.Path(paths["cacdir"])
    # cacdir = pathlib.Path("/home/sam_woodman_noaa_gov/cache")
    # ccc_files = cacdir.glob("*.ccc")
    ccc_file_count = sum(1 for item in cacdir.glob("*.ccc") if item.is_file())
    cac_file_count = sum(1 for item in cacdir.glob("*.cac") if item.is_file())
    logging.info("Using cache directory %s", cacdir)
    logging.info("There are %s *.ccc files", ccc_file_count)
    logging.info("There are %s *.cac files", cac_file_count)

    logging.info("decompressing ccc files without cac matches")
    for ccc_file in cacdir.glob("*.ccc"):
        logging.debug(ccc_file)
        cac_file_name = ccc_file.stem + '.cac'
        cac_file_path = ccc_file.with_name(cac_file_name)
        if not cac_file_path.is_file():
            logging.debug("Decompressing file: %s", ccc_file)
            decompress_file(ccc_file)
        else:
            logging.debug(
                "Skipping because it already has a .cac counterpart: %s",
                ccc_file
            )

    ccc_file_count = sum(1 for item in cacdir.glob("*.ccc") if item.is_file())
    cac_file_count = sum(1 for item in cacdir.glob("*.cac") if item.is_file())
    logging.info("There are now %s *.ccc files", ccc_file_count)
    logging.info("There are now %s *.cac files", cac_file_count)
