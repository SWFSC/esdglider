"""Trigger an OSI Airflow DAG for unprocessed image-manifest files.

When run as a script, lists manifest JSON files in the configured input folder
of a Google Cloud Storage bucket and checks the output bucket for folders
corresponding to completed runs. It skips manifests with matching output
folders, optionally limits the remaining files for testing, and submits each
one to the configured Cloud Composer DAG with its model, output, and YAML
configuration parameters. Set ``DRY_RUN`` to ``True`` to print the
configurations without triggering DAG runs.
"""

import os

import google.auth
import google.auth.transport.requests
import requests
from google.cloud import storage

# --- Configuration ---
COMPOSER_URL = (
    "https://14668048b1ac46eba7798ad8ed19d75a-"
    "dot-us-central1.composer.googleusercontent.com"
)
DAG_ID = "optics-pipeline"
JSON_BUCKET_NAME = "swfscesd-glider-imagery-metadata"
OUTPUT_BUCKET_NAME = "ggn-nmfs-osi-dev-1-data"

# GCS Paths
DEPLOYMENT_NAME = "calanus-20261007"
DEPLOYMENT_YEAR = DEPLOYMENT_NAME.rsplit("-", maxsplit=1)[-1][:4]
DIRECTORY_CHUNK_SIZE = 50
CHUNK_DIRECTORY = f"{DIRECTORY_CHUNK_SIZE:03d}dirs"
JSON_FOLDER_PATH = (
    f"{DEPLOYMENT_YEAR}/{DEPLOYMENT_NAME}/osi-manifests-{CHUNK_DIRECTORY}"
)
OUTPUT_FOLDER_PATH = f"SWFSC/glider/{DEPLOYMENT_NAME}/{CHUNK_DIRECTORY}"

# --- DAG Run Parameters ---
MODEL_TYPE = "andy-6d-v1-detector-model"
YAML_CONFIG_PATH = (
    "gs://ggn-nmfs-osi-dev-1-data/"
    "SWFSC/sam/viame-custom-pipeline-viamecsv-uploadno.yaml"
)
# YAML_CONFIG_PATH = (
#     "gs://ggn-nmfs-osi-dev-1-data/"
#     "SWFSC/sam/viame-custom-pipeline-coco-uploadno.yaml"
# )

# --- Testing ---
# Set to a number to limit test runs (e.g., 2), or None to process all files.
TEST_LIMIT = None
DRY_RUN = False


def get_access_token():
    """Fetch an OAuth 2.0 access token for Google Cloud.

    Returns:
        str: The refreshed access token.

    Raises:
        google.auth.exceptions.DefaultCredentialsError: If application
            default credentials cannot be obtained.
        google.auth.exceptions.RefreshError: If the credentials cannot be
            refreshed.
    """
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


def main():
    """Find unprocessed manifests and trigger the configured Airflow DAG.

    The function reads input and output folder information from Google Cloud
    Storage, then submits a DAG run for each eligible manifest unless
    ``DRY_RUN`` is enabled.

    Returns:
        None.

    Raises:
        google.auth.exceptions.DefaultCredentialsError: If application
            default credentials cannot be obtained.
        google.auth.exceptions.RefreshError: If the credentials cannot be
            refreshed.
        google.cloud.exceptions.GoogleCloudError: If a Cloud Storage
            operation fails.
        requests.exceptions.RequestException: If a DAG trigger request fails
            at the transport level.
    """
    print("Obtaining access token...")
    token = get_access_token()

    # Connect to GCS bucket
    storage_client = storage.Client()
    json_bucket = storage_client.bucket(JSON_BUCKET_NAME)
    output_bucket = storage_client.bucket(OUTPUT_BUCKET_NAME)

    # 1. Retrieve existing folder names in the output directory
    print("Fetching existing output folders to exclude...")
    output_blobs = output_bucket.list_blobs(
        prefix=OUTPUT_FOLDER_PATH,
        delimiter="/",
    )

    existing_output_folders = set()
    for page in output_blobs.pages:
        for prefix in page.prefixes:
            folder_name = prefix[len(OUTPUT_FOLDER_PATH) :].rstrip("/")
            if folder_name:
                existing_output_folders.add(folder_name)

    print(f"Found {len(existing_output_folders)} existing output folder(s).")

    # 2. Fetch JSON input files
    blobs = json_bucket.list_blobs(prefix=JSON_FOLDER_PATH)
    json_blobs = [
        blob
        for blob in blobs
        if blob.name.endswith(".json") and not blob.name.endswith("/")
    ]
    print(f"Found {len(json_blobs)} total JSON configuration files in GCS.")

    # 3. Filter out files whose base name matches an existing folder in output
    filtered_json_blobs = []
    skipped_count = 0

    for blob in json_blobs:
        file_name = os.path.basename(blob.name)
        base_name, _ = os.path.splitext(file_name)

        if base_name in existing_output_folders:
            skipped_count += 1
        else:
            filtered_json_blobs.append(blob)

    print(
        f"Filtered out {skipped_count} existing run(s). "
        f"{len(filtered_json_blobs)} remaining to process."
    )

    # --- Apply Test Limit ---
    if TEST_LIMIT is not None:
        filtered_json_blobs = filtered_json_blobs[:TEST_LIMIT]
        print(
            "\n--- TEST MODE ACTIVE: Only processing the first "
            f"{len(filtered_json_blobs)} file(s) ---\n"
        )
    else:
        print("\n")

    # REST API setup
    url = f"{COMPOSER_URL}/api/v1/dags/{DAG_ID}/dagRuns"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Iterate through each remaining JSON file and trigger DAG
    for i, blob in enumerate(filtered_json_blobs, 1):
        file_name = os.path.basename(blob.name)

        # --- Generate the Wrapper Configuration dynamically ---
        input_file_uri = f"gs://{JSON_BUCKET_NAME}/{blob.name}"

        dag_conf = {
            "input_file": input_file_uri,
            "model_type": MODEL_TYPE,
            "output_bucket": OUTPUT_BUCKET_NAME,
            "output_folder": f"{OUTPUT_FOLDER_PATH}/{i:02d}",
            "yaml_config_path": YAML_CONFIG_PATH,
        }

        if not DRY_RUN:
            # Payload structure expected by Airflow REST API
            payload = {"conf": dag_conf}
            res = requests.post(url, json=payload, headers=headers)

            if res.status_code in (200, 201):
                print(
                    f"[{i}/{len(filtered_json_blobs)}] Successfully triggered "
                    f"run for {file_name}"
                )
            else:
                print(
                    f"[{i}/{len(filtered_json_blobs)}] Failed to trigger "
                    f"{file_name}: {res.status_code} - {res.text}"
                )

        else:
            print(
                f"[{i}/{len(filtered_json_blobs)}] DRY RUN: "
                f"Skipping trigger for {file_name}"
            )
            print("Would post the following payload configuration -----")
            for k, v in dag_conf.items():
                print(f"{k}: {v}")
            print("End of payload configuration -----\n")

if __name__ == "__main__":
    main()
