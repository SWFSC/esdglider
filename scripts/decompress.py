import logging
import os
from pathlib import Path

from dbdreader.decompress import decompress_file, is_compressed
from esdglider.slocum.core import decompress_compex

from esdglider import gcp, paths, utils

logger = logging.getLogger(__name__)

"""
This script is intended to help users quickly generate decompressed
binary files, as a light wrapper around dbdreader functions.


Potentially relevant command line samples:

# Copy binary files to local, for testing
gcloud storage cp \
    gs://swfscesd-glider-deployments-data-in/2026/calanus-20260824/binary/delayed/* \
    tmp-binary/calanus-20260824-delayed/

# Delete any existing decompressed files to start fresh
find tmp-binary/calanus-20260824-delayed \
    -type f -regextype posix-extended \
    -regex ".*.[de][bc]d" \
    -delete


rclone check calanus-20260824-delayed calanus-20260824-delayed-compex
"""

### User-supplied variables
deployment_name = "calanus-20260824"
mode = "delayed"
decompress_tool = "dbdreader" #dbdreader, compex

# Ignored unless `decompress_tool` is "compex"
compex_exe_path = "" # "/home/user/compexp.exe" 

### Consistent variables
home = Path.home()
logs_bucket_name = "swfscesd-glider-logs"
logs_path = home / "mnt-gcs" / logs_bucket_name
# logs_path = home / "tmp-binary"
log_file_name = f"{deployment_name}-{mode}-decompress-{decompress_tool}.log"


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

    glider_paths = paths.get_path_glider(
        deployment_name = deployment_name, 
        mode = mode, 
        home_path = home,
    )
    gcp.gcs_mount_bucket(paths.data_in_bucket_name, glider_paths["data_in_path"], ro=False)


    binary_dir = glider_paths["binarydir"]
    # binary_dir = "/home/user/tmp-binary/calanus-20260824-delayed-compex"
    binary_dir_files = os.listdir(binary_dir)

    utils.count_binary_files(binary_dir, "Start: ")

    logger.info("decompressing all files in %s", binary_dir)
    for fin in binary_dir_files:
        logger.debug("Working on %s", fin)

        if is_compressed(fin):
            fin_path = os.path.join(binary_dir, fin)
            try:
                if decompress_tool == "dbdreader":
                    decompress_file(fin_path)
                elif decompress_tool == "compex":
                    decompress_compex(fin_path, compex_exe_path)
                else:
                    raise NotImplementedError("Only decompression tools 'dbdreader' or 'compex' are currently supported")
            except Exception as e:  # noqa: BLE001
                logger.error("Error decompressing %s: %s", fin, e)
        else:
            logger.debug("skipping %s", fin)

    utils.count_binary_files(binary_dir, "End: ")
    logger.info("Decompression efforts complete for all files in %s", binary_dir)
    