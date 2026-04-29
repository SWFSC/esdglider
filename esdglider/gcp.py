"""
Functions for interacting with GCP
"""

import logging
import os
import subprocess

import google_crc32c
from google.cloud import secretmanager

# from google.cloud import storage

_log = logging.getLogger(__name__)


def access_secret_version(project_id, secret_id, version_id="latest"):
    """
    Access the payload for the given secret version if one exists. The version
    can be a version number as a string (e.g. "5") or an alias (e.g. "latest").
    Originally from
    https://github.com/googleapis/python-secret-manager/blob/main/samples/snippets/access_secret_version.py

    TODO: update this to follow current sample code from Google:
    https://github.com/googleapis/google-cloud-python/blob/main/packages/google-cloud-secret-manager/samples/generated_samples/secretmanager_v1_generated_secret_manager_service_access_secret_version_sync.py

    """

    # Create the Secret Manager client.
    client = secretmanager.SecretManagerServiceClient()

    # Build the resource name of the secret version.
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"

    # Access the secret version.
    response = client.access_secret_version(request={"name": name})

    # Verify payload checksum.
    crc32c = google_crc32c.Checksum()
    crc32c.update(response.payload.data)
    if response.payload.data_crc32c != int(crc32c.hexdigest(), 16):
        _log.error("Data corruption detected.")
        return response

    # Print the secret payload.
    #
    # WARNING: Do not print the secret in a production environment - this
    # snippet is showing how to access the secret material.
    # payload = response.payload.data.decode("UTF-8")
    # print("Plaintext: {}".format(payload))

    return response.payload.data.decode("UTF-8")


# ---------------------------------------
# GCS bucket mount management
# NOTE: these functions are from
#  https://github.com/us-amlr/shaip/blob/main/shaip/utils.py
def gcs_unmount_bucket(mountpoint):
    """
    Run the command to unmount a GCS bucket mounted
    at path 'mountpoint' (a string) using gcsfuse
    https://cloud.google.com/storage/docs/gcs-fuse
    """
    subprocess.run(["fusermount", "-u", mountpoint])

    return 0


def gcs_mount_bucket(bucket, mountpoint, ro=False):
    """
    Run the command to mount a bucket 'bucket' at 'mountpoint' using gcsfuse
    Command is run with '--implicit-dirs' argument.
    https://cloud.google.com/storage/docs/gcs-fuse

    Parameters
    ----------
    bucket : str
        Name of bucket ot mount. Eg, 'amlr-gliders-imagery-raw-dev'
    mountpoint : str
        Path of where to mount 'bucket'. Eg, '.../amlr-gliders-imagery-proc-dev'
    ro : boolean
        Indicates if bucket should be mounted as read only
    """
    # Make mountpoint, if necessary
    if not os.path.exists(mountpoint):
        os.makedirs(mountpoint)
    # elif os.listdir(mountpoint) != []:
    #     _log.info("The mountpoint is not empty, will try to unmount")

    # Unmount bucket, just in case
    gcs_unmount_bucket(mountpoint)
    if os.listdir(mountpoint) != []:
        _log.info("The mountpoint not empty after unmounting, exiting") 
        return 0

    # Mount bucket using gcsfuse
    cmd = ["gcsfuse", "--implicit-dirs", bucket, mountpoint]
    if ro:
        cmd[2:2] = ["-o", "ro"]
    subprocess.run(cmd)

    return 0
    # # Run command, capture output (stdout/stderr), and format as strings (text=True)
    # result = subprocess.run(cmd, capture_output=True, text=True)
    # print(result)

    # # Log standard output if it exists
    # if result.stdout:
    #     _log.info(f"gcsfuse stdout:\n{result.stdout.strip()}")
    #     _log.info(f"gcsfuse stderr:\n{result.stderr.strip()}")

    # # Log standard error if it exists
    # if result.stderr:
    #     # CLI tools like gcsfuse often write operational logs to stderr even on success
    #     if result.returncode == 0:
    #         _log.info(f"gcsfuse stderr:\n{result.stderr.strip()}")
    #     else:
    #         _log.error(f"gcsfuse failed with code {result.returncode}:\n{result.stderr.strip()}")

    # return result.returncode


def check_gcs_file_exists(bucket, file_path):
    """
    Checks if a file exists in GCS. Function adapted from Gemini

    Parameters
    ----------
    bucket : Bucket
        An object of class Bucket, created via eg storage_client.bucket(bucket_name)
    file_path : str
        Path to the object/file in the bucket.
        Path does not include the bucket name

    Returns
    -------
    bool
        True if object exists, and False otherwise
    """
    # # This can be initialized once outside the function in your actual script
    # storage_client = storage.Client()
    # bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file_path)
    return blob.exists()


def check_gcs_directory_exists(bucket, directory_path):
    """
    Checks if a 'directory' exists in a GCS bucket by checking for objects
    with that prefix. Function adapted from Gemini

    Parameters
    ----------
    bucket : Bucket
        An object of class Bucket, created via eg storage_client.bucket(bucket_name)
    directory_path : str
        Path to the GCS 'directory' within the bucket.
        Path does not include the bucket name.
        This path must end with a forward slash

    Returns
    -------
    bool
        True if the directory path contains at least object, and False otherwise
    """

    if not directory_path.endswith("/"):
        directory_path += "/"

    # storage_client = storage.Client()
    # bucket = storage_client.bucket(bucket_name)

    # list_blobs returns an iterator.
    # We only need to see if it has at least one item.
    blobs = bucket.list_blobs(prefix=directory_path, max_results=1)

    # next(iterator, default_value) is a memory-efficient way to check
    # if the iterator has any items.
    return next(blobs, None) is not None
