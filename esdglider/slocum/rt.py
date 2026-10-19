# functions for real-time processing in GCP

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

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

    Returns
    -------
    None
    """

    _log.info("Scraping files from SFMC for deployment %s", deployment_name)
    year = utils.get_path_year(deployment_name)

    # --------------------------------------------
    # Create sfmc directory structure, if needed
    _log.info("Making sfmc deployment dirs at %s", sfmc_path)
    
    # Path construction and directory creation via pathlib
    sfmc_local_path = Path(sfmc_path) / f"sfmc-{deployment_name}"
    sfmc_local_path.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------
    # rsync with SFMC

    # Secret retrieval
    sfmc_password = gcp.access_secret_version(gcpproject_id, secret_id)
    env = dict(os.environ, SSHPASS=sfmc_password)
    
    fetch_from_sfmc(
        deployment_name=deployment_name,
        local_path=sfmc_local_path,
        env=env, 
        # gcpproject_id=gcpproject_id,
        # secret_id=secret_id,
    )
    
    # _log.info(
    #     "Starting rsync with SFMC dockerver for glider/deployment: %s/%s", 
    #     glider, 
    #     deployment_name
    # )
    # # sfmc_glider = os.path.join(
    # #     "/var/opt/sfmc-dockserver/stations/noaa/gliders",
    # #     glider,
    # #     "from-glider/",
    # # )
    # # sfmc_server_path = f"swoodman@sfmc.webbresearch.com:{sfmc_glider}"
    
    # remote_dir = PurePosixPath("/var/opt/sfmc-dockserver/stations/noaa/gliders") / glider / "from-glider"
    # sfmc_server_path = f"swoodman@sfmc.webbresearch.com:{remote_dir.as_posix()}/"

    # ssh_cmd = "sshpass -e ssh -o StrictHostKeyChecking=accept-new"

    # rsync_args = [
    #     "rsync",
    #     "-aP",
    #     "--delete",
    #     "-e", ssh_cmd,
    #     sfmc_server_path,
    #     f"{sfmc_local_path.as_posix()}/",
    # ]
   
    # # Secret retrieval
    # sfmc_password = gcp.access_secret_version(gcpproject_id, secret_id)
    # env = dict(os.environ, SSHPASS=sfmc_password)
    
    # _log.info("Starting rsync from SFMC server...")
    # retcode = subprocess.run(
    #     rsync_args, 
    #     env=env, 
    #     capture_output=True, 
    #     text=True, 
    #     check=False
    # )

    # if retcode.returncode != 0:
    #     _log.error(f"Error rsyncing with SFMC dockserver: {retcode.stderr}")
    #     raise ValueError("Unsuccessful rsync with SFMC dockserver")

    # _log.info("Successfully completed rsync from SFMC.")

    # Check for unexpected file extensions
    sfmc_file_ext = utils.find_extensions(sfmc_local_path)
    file_ext_expected = {".cac", ".CAC", ".sbd", ".tbd", ".ad2", ".ccc", ".scd", ".tcd", ".cam"}
    file_ext_unk = sfmc_file_ext.difference(file_ext_expected)
    if len(file_ext_unk) > 0:
        _log.warning(
            "File with the following extensions (%s) "
            "were downloaded from the SFMC, "
            "but will not be organized or copied to the GCS bucket", 
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
        raise FileNotFoundError(f"Cache path does not exist: {cache_path}")

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
    _stage_and_sync_to_gcs(
        file_extensions=sfmc_file_ext,
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


def fetch_from_sfmc(
    deployment_name: str,
    local_path: Path,
    env: dict[str, str],
) -> None:
    """
    Fetches files from the SFMC server for the specified glider deployment.

    Parameters
    ----------
    deployment_name: str
        Name of the glider deployment.
    local_path: Path
        Local directory to store the fetched files.
    env: dict[str, str]
        Environment variables to use for the subprocess, including the SFMC server password.

    Returns
    -------
    None
    """
    glider = utils.get_glider_name(deployment_name)

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
        f"{local_path.as_posix()}/",
    ]
    
    _log.info("Starting rsync from SFMC server...")
    retcode = subprocess.run(
        rsync_args, 
        env=env, 
        capture_output=True, 
        text=True, 
        check=False
    )

    if retcode.returncode != 0:
        _log.error("Error rsyncing with SFMC dockserver: %s", retcode.stderr)
        raise ValueError("Unsuccessful rsync with SFMC dockserver")

    _log.info("Successfully completed rsync from SFMC.")


def _stage_and_sync_to_gcs(
    file_extensions: set[str],
    ext_regex: str,
    subdir_name: str,
    local_path: Path,
    bucket_name: str,
    gcs_prefix: str,
    rsync_delete: bool = True,
) -> None:
    """
    Sorts local files matching extension patterns and uploads them to GCS natively.
    Moves files matching the given extension regex from the local path to a subdirectory,
    and then syncs that subdirectory to the specified GCS bucket location.

    Parameters
    ----------
    file_extensions: set[str]
        Set of all file extensions present in the local SFMC directory.
    ext_regex: str
        Regular expression pattern to match specific file extensions.
    subdir_name: str
        Name of the subdirectory to move matching files into.
    local_path: Path
        Path to the local SFMC directory.
    bucket_name: str
        Name of the GCS bucket to sync files to.
    gcs_prefix: str
        Prefix path within the GCS bucket.
    rsync_delete: bool
        Whether to delete unmatched files in the GCS destination. 
        Default is True

    Returns
    -------
    None
    """
    pattern = re.compile(ext_regex)
    _log.info(
        "Performing real-time file management for files matching regex: %s in local path: %s", 
        ext_regex, 
        local_path
    )
    
    if any(pattern.search(ext) for ext in file_extensions):
        subdir_path = local_path / subdir_name
        subdir_path.mkdir(exist_ok=True)

        _log.info("Moving %s files to %s", subdir_name, subdir_path)
        for item in local_path.iterdir():
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
    

def sync_directory_to_ftp(
    local_dir: str | Path,
    remote_dir: str | PurePosixPath,
    host: str,
    username: str,
    env: dict[str, str],
    *,
    exclude_patterns: list[str] | str | None = None,
    log_file: str | Path | None = None,
    dry_run: bool = False,
    ssl_force: bool = True,
    ssl_protect_data: bool = True,
    parallel: int = 1,
    timeout: int = 30,
    max_retries: int = 3,
) -> None:
    """
    Mirror a local directory to a remote FTPS server using LFTP.

    Uploads new and modified files, removes remote files missing locally,
    preserves excluded patterns, and passes passwords securely through the
    LFTP_PASSWORD environment variable.

    Parameters
    ----------
    local_dir : str | Path
        The local directory path to synchronize.
    remote_dir : str | PurePosixPath
        The target directory path on the remote FTP server.
    host : str
        The FTP server hostname or IP address.
    username : str
        The FTP account username.
    env : dict[str, str]
        Environment variables to use for the subprocess, including the FTP account password.
    exclude_patterns : list[str] | str | None, optional
        Glob patterns to ignore and protect from remote deletion (default is None).
    log_file : str | Path | None, optional
        Local file path where LFTP will log operations (default is None).
    dry_run : bool, optional
        Whether to simulate the mirror without transferring/deleting files
        (default is False).
    ssl_force : bool, optional
        Whether to enforce FTP over TLS (default is True).
    ssl_protect_data : bool, optional
        Whether to encrypt the data channel (default is True).
    parallel : int, optional
        Number of parallel file transfers (default is 1).
    timeout : int, optional
        Connection and network timeout in seconds (default is 30).
    max_retries : int, optional
        Maximum number of connection retry attempts (default is 3).

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If LFTP is not installed or the local directory does not exist.
    ValueError
        If the LFTP command exits with a non-zero exit code.
    """
    if not shutil.which("lftp"):
        _log.error("The 'lftp' binary was not found in PATH.")
        raise FileNotFoundError("The 'lftp' binary is not installed or not in PATH.")

    local_path = Path(local_dir).expanduser().resolve()
    if not local_path.is_dir():
        _log.error("Local directory does not exist: %s", local_path)
        raise FileNotFoundError(f"Local directory does not exist: {local_path}")

    remote_path = PurePosixPath(remote_dir).as_posix()

    # Normalize exclude patterns into a list
    if exclude_patterns is None:
        patterns: list[str] = []
    elif isinstance(exclude_patterns, str):
        patterns = [exclude_patterns]
    else:
        patterns = list(exclude_patterns)

    mirror_opts = [
        "--reverse",
        "--delete",
        "--only-newer",
        "--verbose",
    ]

    if dry_run:
        mirror_opts.append("--dry-run")
    if parallel > 1:
        mirror_opts.append(f"--parallel={parallel}")
    for pat in patterns:
        mirror_opts.append(f'--exclude-glob "{pat}"')

    if log_file:
        resolved_log = Path(log_file).expanduser().resolve()
        resolved_log.parent.mkdir(parents=True, exist_ok=True)
        mirror_opts.append(f'--log="{resolved_log.as_posix()}"')

    mirror_flags = " ".join(mirror_opts)
    ssl_force_str = "true" if ssl_force else "false"
    ssl_data_str = "true" if ssl_protect_data else "false"

    lftp_script = f"""
    set cmd:fail-exit true
    set net:max-retries {max_retries}
    set net:timeout {timeout}
    set ftp:ssl-force {ssl_force_str}
    set ftp:ssl-protect-data {ssl_data_str}

    open -u "{username}" --env-password "{host}"

    mirror {mirror_flags} "{local_path.as_posix()}" "{remote_path}"

    quit
    """

    _log.info(
        "Starting LFTP mirror from %s to %s:%s (dry_run=%s)",
        local_path,
        host,
        remote_path,
        dry_run,
    )

    retcode = subprocess.run(
        ["lftp"],
        input=lftp_script,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    if retcode.returncode != 0:
        _log.error("Error syncing via LFTP to %s: %s", host, retcode.stderr)
        raise ValueError(f"Unsuccessful LFTP sync with {host}: {retcode.stderr.strip()}")

    _log.info("Successfully completed LFTP mirror to %s.", host)
    if retcode.stdout:
        for line in retcode.stdout.strip().splitlines():
            _log.info("LFTP: %s", line)


def sync_to_ngdac(
    local_dir: str | Path,
    remote_dir: str | PurePosixPath,
    username: str,
    gcpproject_id: str,
    secret_id: str,
    *,
    host: str = "gliders.ioos.us",
    exclude_patterns: list[str] | str | None = None,
    log_file: str | Path | None = None,
    dry_run: bool = False,
) -> None:
    """
    Mirror local glider files to the IOOS NGDAC FTPS server.

    Retrieves the account password from Google Cloud Secret Manager, enforces
    preservation of the remote 'deployment.json' file, and delegates the
    transfer to `sync_directory_to_ftp`.

    Parameters
    ----------
    local_dir : str | Path
        The local directory path to synchronize.
    remote_dir : str | PurePosixPath
        The target directory path on the remote FTP server.
    username : str
        The IOOS NGDAC FTP username.
    gcpproject_id : str
        The GCP project ID containing the Secret Manager secret.
    secret_id : str
        The secret ID in Secret Manager containing the FTP password.
    host : str, optional
        The IOOS NGDAC FTP server hostname (default is 'gliders.ioos.us').
    exclude_patterns : list[str] | str | None, optional
        Additional glob patterns to preserve on the remote server
        (default is None). 'deployment.json' is always preserved.
    log_file : str | Path | None, optional
        Local file path where LFTP will log operations (default is None).
    dry_run : bool, optional
        Whether to simulate the mirror without transferring or deleting files
        (default is False).

    Returns
    -------
    None
    """
    _log.info("Preparing sync to IOOS NGDAC (%s) for user %s", host, username)

    # Normalize patterns and ensure deployment.json is always protected
    combined_excludes = {"deployment.json"}
    if exclude_patterns:
        if isinstance(exclude_patterns, str):
            combined_excludes.add(exclude_patterns)
        else:
            combined_excludes.update(exclude_patterns)

    # Retrieve credentials from GCP Secret Manager
    ftp_password = gcp.access_secret_version(gcpproject_id, secret_id)
    env = dict(os.environ, LFTP_PASSWORD=ftp_password)


    return sync_directory_to_ftp(
        local_dir=local_dir,
        remote_dir=remote_dir,
        host=host,
        username=username,
        env=env,
        exclude_patterns=sorted(combined_excludes),
        log_file=log_file,
        dry_run=dry_run,
        ssl_force=True,
        ssl_protect_data=True,
    )
