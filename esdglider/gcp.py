"""
Functions for interacting with GCP (Fail-Fast Architecture)
"""

import logging
import os
import shutil
import subprocess
from functools import wraps
from pathlib import Path, PurePosixPath

import google.auth
import google_crc32c
from google.cloud import secretmanager, storage

from esdglider import defaults

_log = logging.getLogger(__name__)


def resolve_project_id(project_id: str | None = None) -> str:
    """
    Resolve the GCP project ID following a priority cascade:
    1. Explicit function argument
    2. Environment variables ('GOOGLE_CLOUD_PROJECT', 'GCP_PROJECT_ID')
    3. Google Application Default Credentials (ADC)
    4. Package default constant from defaults.py

    Parameters
    ----------
    project_id : str | None, optional
        Explicitly passed project ID.

    Returns
    -------
    str
        The resolved GCP project ID.
    """
    if project_id:
        return project_id

    # 1. Check standard environment variables
    env_project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
    if env_project:
        return env_project

    # 2. Auto-discover from ADC / metadata server
    try:
        _, adc_project = google.auth.default()
        if adc_project:
            return adc_project
    except Exception:  # noqa: BLE001, S110
        # Pass silently if running offline or in an unauthenticated test environment
        pass

    # 3. Fall back to package constant
    return defaults.GCP_PROJECT_ID


def access_secret_version(
    secret_id: str,
    project_id: str | None = None,
    version_id: str = "latest",
) -> str:
    """
    Access a decrypted payload from Google Cloud Secret Manager.

    Parameters
    ----------
    secret_id : str
        The name or ID of the secret to retrieve.
    project_id : str | None, optional
        The GCP project ID. Defaults to esdglider.defaults.GCP_PROJECT_ID.
    version_id : str, optional
        The secret version number or alias (default is "latest").

    Returns
    -------
    str
        The decrypted secret payload decoded as a UTF-8 string.

    Raises
    ------
    ValueError
        If the payload fails CRC32c integrity verification.
    """
    project_id = resolve_project_id(project_id)

    # Initialized only when called; safe for machines without GCP credentials
    client = secretmanager.SecretManagerServiceClient()
    resource_name = (
        f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    )

    _log.debug("Accessing Secret Manager payload: %s", resource_name)
    response = client.access_secret_version(request={"name": resource_name})

    if response.payload.data_crc32c is not None:
        crc32c = google_crc32c.Checksum()
        crc32c.update(response.payload.data)
        if response.payload.data_crc32c != int(crc32c.hexdigest(), 16):
            msg = f"Data corruption detected while retrieving secret: {secret_id}"
            _log.critical(msg)
            raise ValueError(msg)

    return response.payload.data.decode("UTF-8")


def sync_directory_to_gcs(
    local_dir: Path,
    bucket_name: str,
    gcs_prefix: str,
    delete: bool = True,
):
    """
    Uploads files from a local directory to a GCS bucket using native Python SDK.
    Only uploads files if they do not already exist at the destination prefix.
    
    Parameters
    ----------
    local_dir : Path
        Local directory source to sync from.
    bucket_name : str
        Target GCS bucket name.
    gcs_prefix : str
        Target object prefix inside the GCS bucket.
    delete : bool, optional
        If True, deletes objects under the GCS prefix that do not exist in local_dir.
        Defaults to True.

    Returns
    -------
    None
    """
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    _log.info(
        "Synchronizing local directory '%s' to GCS bucket '%s', "
        + "with prefix '%s', and with delete as '%s'",
        local_dir,
        bucket_name,
        gcs_prefix,
        delete,
    )

    prefix_str = PurePosixPath(gcs_prefix).as_posix() if gcs_prefix else ""
    list_prefix = f"{prefix_str}/" if prefix_str and not prefix_str.endswith("/") else prefix_str

    # Fetch existing blobs once to avoid making an API call per local file
    _log.debug(f"Fetching existing GCS objects under gs://{bucket_name}/{list_prefix}")
    existing_blobs = {blob.name: blob for blob in bucket.list_blobs(prefix=list_prefix)}

    local_blob_names = set()

    # Iterate through local files
    for file_path in local_dir.rglob("*"):
        if file_path.is_file():
            relative_path = file_path.relative_to(local_dir)
            blob_name = (PurePosixPath(prefix_str) / relative_path.as_posix()).as_posix()
            local_blob_names.add(blob_name)

            # Check if file already exists in GCS destination
            if blob_name in existing_blobs:
                _log.debug(f"Skipping {file_path.name}; gs://{bucket_name}/{blob_name} already exists.")
            else:
                blob = bucket.blob(blob_name)
                _log.info(f"Uploading {file_path} to gs://{bucket_name}/{blob_name}")
                blob.upload_from_filename(file_path)

    # Delete unmatched destination objects if delete=True
    if delete:
        for blob_name, blob in existing_blobs.items():
            if blob_name not in local_blob_names:
                _log.info(f"Deleting unmatched GCS blob: gs://{bucket_name}/{blob_name}")
                blob.delete()


def log_and_raise_shell_errors(command_name: str):
    """
    Decorator to log stdout/stderr when a subprocess fails, then let
    the exception bubble up to halt the orchestration script.

    Parameters
    ----------
    command_name : str
        The name of the CLI tool being executed (e.g., 'gcsfuse',
        'fusermount'). Used to format clean and clear log statements.

    Returns
    -------
    callable
        The wrapped function capable of catching and logging subprocess
        failures.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except subprocess.CalledProcessError as e:
                _log.critical(f"{command_name} failed with exit code {e.returncode}")
                if e.stdout:
                    _log.debug(f"{command_name} stdout:\n{e.stdout.strip()}")
                if e.stderr:
                    _log.error(f"{command_name} stderr:\n{e.stderr.strip()}")
                raise
            except Exception as e:
                _log.exception(f"Unexpected system error during {command_name}: {e}")
                raise
        return wrapper
    return decorator


def check_gcsfuse_installed() -> None:
    """
    Ensure gcsfuse is available in the system environment.

    Raises
    ------
    FileNotFoundError
        If the gcsfuse binary dependency is missing from the system path.
    """
    if shutil.which("gcsfuse") is None:
        msg = "gcsfuse dependency missing: Not found in system PATH."
        _log.critical(msg)
        raise FileNotFoundError(msg)


@log_and_raise_shell_errors(command_name="fusermount")
def gcs_unmount_bucket(mountpoint: str) -> None:
    """
    Unmount a GCS bucket mounted at a specific path using fusermount.

    Parameters
    ----------
    mountpoint : str
        The absolute or relative system path where the bucket is
        currently mounted.
        Example: '/home/user/data/amlr-gliders-imagery-proc-dev'

    Returns
    -------
    None

    Raises
    ------
    subprocess.CalledProcessError
        If the underlying fusermount command returns a non-zero exit code.
    """
    mountpoint = str(mountpoint)
    cmd = ["fusermount", "-u", mountpoint]
    _log.debug(f"Executing: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    if result.stdout:
        _log.debug(f"fusermount stdout:\n{result.stdout.strip()}")
    _log.debug(f"Successfully unmounted {mountpoint}")

def gcs_mount_bucket_check(bucket: str, mountpoint: str | Path, ro: bool = False) -> None:
    """
    Light wrapper around the gcs_mount_bucket function.
    Checks for files and directories in mountpoint, 
    and exits quietly if the mountpoint is not empty.
    This is a convenience function for esdglider notebooks, 
    which might be being run while a processing script is also running, 
    and thus remounting is undesirable.
    """ 

    mountpoint = str(mountpoint)
    if os.path.exists(mountpoint) and os.listdir(mountpoint):
        _log.info("Mountpoint '%s' is not empty; skipping mount.", mountpoint)
        return
    
    gcs_mount_bucket(bucket, mountpoint, ro=ro)


def gcs_mount_bucket(bucket: str, mountpoint: str | Path, ro: bool = False) -> None:
    """
    Run the command to mount a bucket at a mountpoint using gcsfuse.
    
    Command is run with the '--implicit-dirs' argument. Automatically
    handles pre-checks, directory validation, and dirty unmounting
    efforts prior to execution.

    Parameters
    ----------
    bucket : str
        Name of the Cloud Storage bucket to mount. 
        Example: 'amlr-gliders-imagery-raw-dev'
    mountpoint : str
        The local system directory path where the bucket should be attached.
        Example: '/mnt/amlr-gliders-imagery-proc-dev'
    ro : bool, optional
        Indicates if the bucket should be mounted as read-only using the
        '-o ro' argument flag. Defaults to False (read-write).

    Returns
    -------
    None

    Raises
    ------
    FileNotFoundError
        If gcsfuse is missing from the environment, or if the target local
        mountpoint directory does not exist.
    RuntimeError
        If the target mountpoint is not empty and cannot be successfully
        unmounted or cleared.
    subprocess.CalledProcessError
        If the underlying gcsfuse command returns a non-zero exit code.
    """
    check_gcsfuse_installed()

    bucket = str(bucket)
    mountpoint = str(mountpoint)
    _log.info(f"Initiating mount sequence for bucket '{bucket}' -> '{mountpoint}'")

    if not os.path.exists(mountpoint):
        msg = f"Mount abort: Mountpoint path does not exist: {mountpoint}"
        _log.critical(msg)
        raise FileNotFoundError(msg)
        
    if os.listdir(mountpoint):
        _log.debug(f"Mountpoint '{mountpoint}' is not empty; attempting pre-unmount clean")
        try:
            gcs_unmount_bucket(mountpoint)
        except subprocess.CalledProcessError:
            _log.warning("Initial unmount attempt reported an error, checking if directory cleared anyway...")

        if os.listdir(mountpoint):
            msg = f"Mount abort: Mountpoint '{mountpoint}' is dirty and could not be cleared."
            _log.critical(msg)
            raise RuntimeError(msg)

    @log_and_raise_shell_errors(command_name="gcsfuse")
    def _run_mount_cmd():
        cmd = ["gcsfuse", "--implicit-dirs", bucket, mountpoint]
        if ro:
            cmd[2:2] = ["-o", "ro"]

        _log.debug(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        if result.stdout:
            _log.debug(f"gcsfuse stdout:\n{result.stdout.strip()}")
        if result.stderr:
            _log.debug(f"gcsfuse operational logs (stderr):\n{result.stderr.strip()}")

    _run_mount_cmd()
    _log.info(f"Successfully mounted bucket '{bucket}'")


def check_gcs_file_exists(bucket, file_path: str) -> bool:
    """
    Checks if a file exists in a GCS bucket.

    Parameters
    ----------
    bucket : google.cloud.storage.bucket.Bucket
        An initialized Google Cloud Storage Bucket object instantiation.
    file_path : str
        The structural key path pointing to the object blob inside the
        bucket. Does not include the bucket prefix string itself.

    Returns
    -------
    bool
        True if the exact object blob exists in the bucket, and False
        otherwise.
    """
    blob = bucket.blob(file_path)
    return blob.exists()


def check_gcs_directory_exists(bucket, directory_path: str) -> bool:
    """
    Checks if a pseudo-directory exists in a GCS bucket.
    
    Verifies existence by evaluating whether any objects exist matching
    the requested path prefix filter.

    Parameters
    ----------
    bucket : google.cloud.storage.bucket.Bucket
        An initialized Google Cloud Storage Bucket object instantiation.
    directory_path : str
        The pseudo-directory path within the target bucket. Does not
        include the bucket name. If the string does not end with a
        trailing forward slash ('/'), it will be automatically appended.

    Returns
    -------
    bool
        True if the prefix yields at least one matching object nested
        within, and False otherwise.
    """
    if not directory_path.endswith("/"):
        directory_path += "/"

    blobs = bucket.list_blobs(prefix=directory_path, max_results=1)
    return next(blobs, None) is not None