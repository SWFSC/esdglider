# functions for real-time processing in GCP

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from esdglider import gcp, utils

_log = logging.getLogger(__name__)


def scrape_sfmc(
        deployment_name: str, 
        bucket_name: str, 
        sfmc_path: str | Path, 
        cache_path: str | Path, 
        gcpproject_id: str, 
        secret_id: str, 
    ):
    """
    rsync files from sfmc, and send them to correct bucket directories;


    Parameters
    ----------
    deployment_name : str
        The name of the deployment.
    bucket_name : str
        The name of the GCS data in bucket.
    sfmc_path : str | Path
        The local path to store SFMC files.
    gcpproject_id : str
        The GCP project ID.
    secret_id : str
        The secret ID for accessing the SFMC password.
    cache_path : str | Path
        The local path to the cache file directory.

    Returns 0
    """

    _log.info(f"Scraping files from SFMC for deployment {deployment_name}")
    glider = utils.get_glider_name(deployment_name)
    year = utils.get_path_year(deployment_name)

    # --------------------------------------------
    # Create sfmc directory structure, if needed
    _log.info("Making sfmc deployment dirs at %s", sfmc_path)
    
    # Path construction and directory creation via pathlib
    sfmc_local_path = Path(sfmc_path) / f"sfmc-{deployment_name}"
    sfmc_local_path.mkdir(parents=True, exist_ok=True)
   
    # Secret retrieval
    sfmc_password = gcp.access_secret_version(gcpproject_id, secret_id)

    # --------------------------------------------
    # rsync with SFMC
    _log.info(
        "Starting rsync with SFMC dockerver for glider/deployment: %s/%s", 
        glider, 
        deployment_name
    )
    # sfmc_glider = os.path.join(
    #     "/var/opt/sfmc-dockserver/stations/noaa/gliders",
    #     glider,
    #     "from-glider/",
    # )
    # sfmc_server_path = f"swoodman@sfmc.webbresearch.com:{sfmc_glider}"
    
    remote_dir = PurePosixPath("/var/opt/sfmc-dockserver/stations/noaa/gliders") / glider / "from-glider"
    sfmc_server_path = f"swoodman@sfmc.webbresearch.com:{remote_dir.as_posix()}/"

    ssh_cmd = "sshpass -e ssh -o StrictHostKeyChecking=accept-new"

    rsync_args = [
        "rsync",
        "-aP",
        "--delete",
        "-e", ssh_cmd,
        sfmc_server_path,
        f"{sfmc_local_path.as_posix()}/",
    ]

    env = dict(os.environ, SSHPASS=sfmc_password)
    
    _log.info("Starting rsync from SFMC server...")
    retcode = subprocess.run(rsync_args, env=env, capture_output=True, text=True)

    if retcode.returncode != 0:
        _log.error(f"Error rsyncing with SFMC dockserver: {retcode.stderr}")
        raise ValueError("Unsuccessful rsync with SFMC dockserver")

    _log.info("Successfully completed rsync from SFMC.")
    
   
    # rsync_args = [
    #     "sshpass",
    #     "-p",
    #     gcp.access_secret_version(gcpproject_id, secret_id),
    #     "rsync",
    #     "-aP",
    #     "--delete",
    #     sfmc_server_path,
    #     sfmc_local_path,
    # ]
    # NOTE: sshpass via file does not currently work. Unsure why
    # rsync_args = ['sshpass', '-f', sfmc_pwd_file,
    #               'rsync', "-aP", "--delete", sfmc_server_path, sfmc_local_path]
    # os.remove(sfmc_pwd_file) #delete sfmc_pwd_file
    # _log.debug(f'Removed SFMC ssh password file')

    # _log.debug(rsync_args)
    # retcode = subprocess.run(rsync_args, capture_output=True)
    # _log.debug(retcode.args)

    # if retcode.returncode != 0:
    #     _log.error("Error rsyncing with SFMC dockserver")
    #     _log.error(f"Args: {retcode.args}")
    #     _log.error(f"stderr: {retcode.stderr}")
    #     raise ValueError("Unsuccessful rsync with SFMC dockserver")
    # else:
    #     _log.info(f"Successfully completed rsync with SFMC dockerver for {glider}")
    #     _log.debug(f"Args: {retcode.args}")
    #     _log.debug(f"stderr: {retcode.stdout}")

    # Check for unexpected file extensions
    sfmc_file_ext = utils.find_extensions(sfmc_local_path)
    file_ext_expected = {".cac", ".CAC", ".sbd", ".tbd", ".ad2", ".ccc", ".scd", ".tcd", ".cam"}
    file_ext_unk = sfmc_file_ext.difference(file_ext_expected)
    if len(file_ext_unk) > 0:
        _log.warning(
            "File with the following extensions (%s) "
            + "were downloaded from the SFMC, "
            + "but will not be organized or copied to the GCS bucket", 
            ", ".join(file_ext_unk),
        )

    # --------------------------------------------
    # Copy files to subfolders, and rsync with bucket
    _log.info("Starting file management")

    ### cache files ------------------------------
    # cache files to standard-glider-files repo folder
    cache_path = Path(cache_path)
    pattern = re.compile(r"\.[Cc][AaCc][Cc]$")

    # Ensure the cache destination directory exists
    if not cache_path.exists():
        _log.error("Cache path does not exist: %s", cache_path)
        return

    _log.info(
        "Performing real-time file management for cache files "
        "matching regex: %s in sfmc local path", 
        pattern, 
    )

    # Process matching files
    for item in sfmc_local_path.iterdir():
        if item.is_file() and pattern.search(item.name):
            destination = cache_path / item.name

            # Only copy if the file does not already exist in cache_path
            if not destination.exists():
                shutil.copy(str(item), str(destination))
                _log.info("Copied cache file %s to %s", item.name, destination)
            else:
                _log.debug("Skipped cache file (already exists): %s", item.name)

    
    ### sbd/tbd files ---------------------------
    # Do not bother with compressed files, because the SFMC uncompresses them
    bucket_stbd_prefix = (PurePosixPath(year) / deployment_name / "binary/rt").as_posix()
    transfer_files_to_gcs(
        sfmc_ext_all=sfmc_file_ext,
        ext_regex=r"\.[SsTt]bd$",
        subdir_name="stbd",
        local_path=sfmc_local_path,
        bucket_name=bucket_name,
        gcs_prefix=bucket_stbd_prefix,
    )

    

    ### ad2 files -------------------------------
    # # TODO ad2 files
    # name_ad2 = "ad2"
    # bucket_ad2 = (
    #     f"gs://amlr-gliders-acoustics-dev/{project}/{year}/{deployment}/data/rt/"
    # )
    # utils.mkdir_pass(os.path.join(sfmc_local_path, name_ad2))
    # rt_file_mgmt(sfmc_file_ext, ".ad2", name_ad2, sfmc_local_path, bucket_ad2)


    # name_ccc  = 'ccc'
    # putils.mkdir_pass(os.path.join(sfmc_local_path, name_ccc))
    # rt_file_mgmt(sfmc_file_ext, '.ccc', name_ccc, sfmc_local_path,
    #              f'gs://{bucket}/cache-compressed')

    # # scd/tcd files
    # name_stcd = 'stcd'
    # bucket_stcd = os.path.join(bucket_deployment, 'data', 'binary', 'rt-compressed')
    # putils.mkdir_pass(os.path.join(sfmc_local_path, name_stcd))
    # rt_file_mgmt(
    #     sfmc_file_ext, '.[SsTt]cd', name_stcd, sfmc_local_path, bucket_stcd)

    # # cam files TODO
    # name_cam  = 'cam'
    # putils.mkdir_pass(os.path.join(sfmc_local_path, name_cam))
    # rt_files_mgmt(sfmc_file_ext, '.cam', name_cam, sfmc_local_path, bucket_cam)

    # --------------------------------------------
    return 0


def transfer_files_to_gcs(
    sfmc_ext_all: set,
    ext_regex: str,
    subdir_name: str,
    local_path: Path,
    bucket_name: str,
    gcs_prefix: str,
    rsync_delete: bool = True,
):
    """
    Sorts local files matching extension patterns and uploads them to GCS natively.
    Moves files matching the given extension regex from the local path to a subdirectory,
    and then syncs that subdirectory to the specified GCS bucket location.

    Parameters:
    - sfmc_ext_all: Set of all file extensions present in the local SFMC directory.
    - ext_regex: Regular expression pattern to match specific file extensions.
    - subdir_name: Name of the subdirectory to move matching files into.
    - local_path: Path to the local SFMC directory.
    - bucket_name: Name of the GCS bucket to sync files to.
    - gcs_prefix: Prefix path within the GCS bucket.
    - rsync_delete: Whether to delete unmatched files in the GCS destination.

    Returns:
    - 0 if the operation completes successfully.
    """
    pattern = re.compile(ext_regex)
    _log.info(
        "Performing real-time file management for files matching regex: %s in local path: %s", 
        ext_regex, 
        local_path
    )
    
    if any(pattern.search(ext) for ext in sfmc_ext_all):
        subdir_path = local_path / subdir_name
        subdir_path.mkdir(exist_ok=True)

        _log.info(f"Moving {subdir_name} files to {subdir_path}")
        for item in local_path.iterdir():
            if item.is_file() and pattern.search(item.suffix):
                shutil.move(item, subdir_path / item.name)

        _log.info(f"Syncing {subdir_name} files to gs://{bucket_name}/{gcs_prefix}")
        gcp.sync_directory_to_gcs(
            local_dir=subdir_path,
            bucket_name=bucket_name,
            gcs_prefix=gcs_prefix,
            delete=rsync_delete,
        )
    else:
        _log.info(f"No {subdir_name} files found to process.")


# def rt_file_mgmt(
#     sfmc_ext_all,
#     ext_regex,
#     subdir_name,
#     local_path,
#     bucket_path,
#     rsync_delete=True,
# ):
#     """
#     Move real-time files from the local sfmc folder (local_path)
#     to their subdirectory (subdir_path).
#     Then uses gcloud to rsync to their place in the bucket (bucket_path)

#     The rsync_delete flag indicates if the --delete-unmatched-destination-objects
#     flag is used in the command

#     ext_regex_path does include * for copying files (eg is '.[st]bd')
#     """

#     if any(re.search(ext_regex, i) for i in sfmc_ext_all):
#         # Check paths
#         if not os.path.isdir(local_path):
#             _log.error(f"Necessary path ({local_path}) does not exist")
#             raise FileNotFoundError(f"Could not find {local_path}")

#         subdir_path = os.path.join(local_path, subdir_name)
#         if not os.path.isdir(subdir_path):
#             _log.error(f"Necessary path ({subdir_path}) does not exist")
#             raise FileNotFoundError(f"Could not find {subdir_path}")

#         # Move files so as to do rsync later
#         _log.info(f"Moving {subdir_name} files to their local subdirectory")
#         mv_cmd = f"mv {os.path.join(local_path, f'*{ext_regex}')} {subdir_path}"
#         _log.debug(mv_cmd)
#         retcode_tmp = subprocess.call(mv_cmd, shell=True)
#         _log.debug(retcode_tmp)

#         # Do rsync
#         _log.info(f"Rsyncing {subdir_name} subdirectory with bucket directory")
#         rsync_args = ["gcloud", "storage", "rsync", "-r"]
#         if rsync_delete:
#             rsync_args.append("--delete-unmatched-destination-objects")
#         rsync_args.extend([subdir_path, bucket_path])

#         _log.debug(rsync_args)
#         retcode = subprocess.run(rsync_args, capture_output=True)

#         if retcode.returncode != 0:
#             _log.error(f"Error rsyncing {subdir_name} files to bucket")
#             _log.error(f"Args: {retcode.args}")
#             _log.error(f"stderr: {retcode.stderr}")
#             raise ValueError("Unsuccessful rsync to bucket")
#         else:
#             _log.info(f"Rsynced {subdir_name} files to {bucket_path}")
#             _log.debug(f"Args: {retcode.args}")
#             _log.debug(f"stderr: {retcode.stdout}")
#     else:
#         _log.info(f"No {subdir_name} files to copy")

#     return 0
