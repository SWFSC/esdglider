# functions for real-time processing in GCP

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from esdglider import gcp, utils

_log = logging.getLogger(__name__)


def sync_sfmc_deployment(
    deployment_name: str,
    bucket_name: str,
    sfmc_path: str | Path,
    cache_path: str | Path,
    gcpproject_id: str,
    secret_id: str,
) -> None:
    """
    Sync files from SFMC and distribute them to local cache and GCS buckets.

    Parameters
    ----------
    deployment_name : str
        The name of the deployment.
    bucket_name : str
        The name of the GCS data bucket.
    sfmc_path : str | Path
        The local path to store downloaded SFMC files.
    cache_path : str | Path
        The local path to the cache file directory.
    gcpproject_id : str
        The GCP project ID.
    secret_id : str
        The secret ID for accessing the SFMC password.

    Raises
    ------
    FileNotFoundError
        If the cache destination path does not exist.
    """
    _log.info("Starting SFMC deployment sync for %s", deployment_name)
    year = utils.get_path_year(deployment_name)

    # 1. Create sfmc deployment directory
    sfmc_local_path = Path(sfmc_path).expanduser().resolve() / f"sfmc-{deployment_name}"
    sfmc_local_path.mkdir(parents=True, exist_ok=True)

    # 2. Retrieve secret and ingest files from SFMC
    sfmc_password = gcp.access_secret_version(gcpproject_id, secret_id)
    fetch_from_sfmc(
        deployment_name=deployment_name,
        local_path=sfmc_local_path,
        password=sfmc_password,
    )

    # 3. Check for unexpected file extensions
    sfmc_file_ext = utils.find_extensions(sfmc_local_path)
    file_ext_expected = {".cac", ".CAC", ".sbd", ".tbd", ".ad2", ".ccc", ".scd", ".tcd", ".cam"}
    file_ext_unk = sfmc_file_ext.difference(file_ext_expected)
    if file_ext_unk:
        _log.warning(
            "Files with the following unexpected extensions (%s) "
            "were downloaded from SFMC and will not be organized or uploaded.",
            ", ".join(file_ext_unk),
        )

    # 4. Cache file management
    target_cache_path = Path(cache_path).expanduser().resolve()
    if not target_cache_path.is_dir():
        _log.error("Cache path does not exist: %s", target_cache_path)
        raise FileNotFoundError(f"Cache path does not exist: {target_cache_path}")

    pattern = re.compile(r"\.[Cc][AaCc][Cc]$")
    _log.info("Processing cache files matching regex: %s", pattern.pattern)

    for item in sfmc_local_path.iterdir():
        if item.is_file() and pattern.search(item.name):
            destination = target_cache_path / item.name
            if not destination.exists():
                shutil.copy(str(item), str(destination))
                _log.info("Copied cache file %s to %s", item.name, destination)
            else:
                _log.debug("Skipped cache file (already exists): %s", item.name)

    # 5. Stage and sync binary files to GCS
    bucket_stbd_prefix = (Path(year) / deployment_name / "binary/rt").as_posix()
    _stage_and_sync_to_gcs(
        file_extensions=sfmc_file_ext,
        ext_regex=r"\.[SsTt]bd$",
        subdir_name="stbd",
        local_path=sfmc_local_path,
        bucket_name=bucket_name,
        gcs_prefix=bucket_stbd_prefix,
        rsync_delete=True,
    )


def fetch_from_sfmc(
    deployment_name: str,
    local_path: str | Path,
    password: str,
) -> None:
    """
    Fetch files from the SFMC server for the specified glider deployment via rsync.

    Parameters
    ----------
    deployment_name : str
        Name of the glider deployment.
    local_path : str | Path
        Local directory to store the fetched files.
    password : str
        Password for the SFMC dockserver user.

    Raises
    ------
    ValueError
        If the rsync process exits with a non-zero status code.
    """
    glider = utils.get_glider_name(deployment_name)
    target_path = Path(local_path).expanduser().resolve()

    _log.info(
        "Starting rsync with SFMC dockserver for glider/deployment: %s/%s",
        glider,
        deployment_name,
    )

    remote_dir = Path("/var/opt/sfmc-dockserver/stations/noaa/gliders") / glider / "from-glider"
    sfmc_server_path = f"swoodman@sfmc.webbresearch.com:{remote_dir.as_posix()}/"
    ssh_cmd = "sshpass -e ssh -o StrictHostKeyChecking=accept-new"

    rsync_args = [
        "rsync",
        "-aP",
        "--delete",
        "-e", ssh_cmd,
        sfmc_server_path,
        f"{target_path.as_posix()}/",
    ]

    env = dict(os.environ, SSHPASS=password)

    _log.info("Starting rsync from SFMC server...")
    retcode = subprocess.run(
        rsync_args,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    if retcode.returncode != 0:
        _log.error("Error rsyncing with SFMC dockserver: %s", retcode.stderr)
        raise ValueError(f"Unsuccessful rsync with SFMC dockserver: {retcode.stderr.strip()}")

    _log.info("Successfully completed rsync from SFMC.")


def _stage_and_sync_to_gcs(
    file_extensions: set[str],
    ext_regex: str,
    subdir_name: str,
    local_path: str | Path,
    bucket_name: str,
    gcs_prefix: str,
    rsync_delete: bool = True,
) -> None:
    """
    Move matching files into a staging subdirectory and sync that directory to GCS.

    Parameters
    ----------
    file_extensions : set[str]
        Set of all file extensions present in the local directory.
    ext_regex : str
        Regular expression pattern to match specific file extensions.
    subdir_name : str
        Name of the staging subdirectory.
    local_path : str | Path
        Path to the local directory containing source files.
    bucket_name : str
        Name of the GCS bucket.
    gcs_prefix : str
        Prefix path within the GCS bucket.
    rsync_delete : bool, optional
        Whether to delete unmatched files at the GCS destination (default is True).
    """
    base_path = Path(local_path).expanduser().resolve()
    pattern = re.compile(ext_regex)

    _log.info(
        "Performing real-time file management for regex '%s' in %s",
        ext_regex,
        base_path,
    )

    if any(pattern.search(ext) for ext in file_extensions):
        subdir_path = base_path / subdir_name
        subdir_path.mkdir(exist_ok=True)

        _log.info("Moving %s files to %s", subdir_name, subdir_path)
        for item in base_path.iterdir():
            if item.is_file() and pattern.search(item.suffix):
                shutil.move(item, subdir_path / item.name)

        _log.info("Syncing %s files to gs://%s/%s", subdir_name, bucket_name, gcs_prefix)
        gcp.sync_directory_to_gcs(
            local_dir=subdir_path,
            bucket_name=bucket_name,
            gcs_prefix=gcs_prefix,
            delete=rsync_delete,
        )
    else:
        _log.info("No %s files found to process.", subdir_name)
        