"""
Functions for interacting with GCP (Fail-Fast Architecture)
"""

import logging
import os
from pathlib import Path
import subprocess
import shutil
from functools import wraps

import google_crc32c
from google.cloud import secretmanager

_log = logging.getLogger(__name__)


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


def access_secret_version(project_id: str, secret_id: str, version_id: str = "latest") -> str:
    """
    Access the payload for the given secret version if one exists.
    
    Verifies payload data integrity via a CRC32c checksum verification
    step before returning the decoded string.

    Parameters
    ----------
    project_id : str
        The GCP project ID or project number (e.g., 'amlr-gliders-dev').
    secret_id : str
        The name/ID of the secret to retrieve from Secret Manager.
    version_id : str, optional
        The specific version number as a string (e.g., "5") or an alias.
        Defaults to "latest".

    Returns
    -------
    str
        The decrypted secret payload material decoded as a UTF-8 string.

    Raises
    ------
    ValueError
        If data corruption is detected via an invalid CRC32c checksum.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})

    # Verify payload checksum
    crc32c = google_crc32c.Checksum()
    crc32c.update(response.payload.data)
    if response.payload.data_crc32c != int(crc32c.hexdigest(), 16):
        msg = f"Data corruption detected while retrieving secret: {secret_id}"
        _log.critical(msg)
        raise ValueError(msg)

    return response.payload.data.decode("UTF-8")


# ---------------------------------------
# GCS bucket mount management

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
    _log.info(f"Executing: {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    if result.stdout:
        _log.debug(f"fusermount stdout:\n{result.stdout.strip()}")
    _log.info(f"Successfully unmounted {mountpoint}")


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
        _log.info(f"Mountpoint '{mountpoint}' is not empty; attempting pre-unmount clean")
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

        _log.info(f"Executing: {' '.join(cmd)}")
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