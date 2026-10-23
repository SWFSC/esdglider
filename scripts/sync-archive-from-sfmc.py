import logging
import tempfile
from pathlib import Path

from esdglider.slocum import pipeline

from esdglider import defaults, gcp, paths, utils

logger = logging.getLogger(__name__)

deployment_name = "chovy-20261005" # Deployment name

if __name__ == "__main__":
    logging.basicConfig(
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.captureWarnings(True)
    logger.info(
        "Starting sync of archive folder from SFMC for deployment: %s", 
        deployment_name
    )

    sfmc_password = gcp.access_secret_version(
        defaults.SECRET_ID_SFMC, 
        defaults.GCP_PROJECT_ID
    )

    year = utils.get_path_year(deployment_name)
    glider = utils.get_glider_name(deployment_name)
    sfmc_archive = Path(defaults.SFMC_REMOTE_BASE_DIR) / glider / "archive"

    with tempfile.TemporaryDirectory() as tempdir:
        temp_path = Path(tempdir)

        # Fethc archive folder from SFMC
        pipeline.rsync_from_sfmc(
            # deployment_name=deployment_name,
            local_dir=temp_path,
            remote_dir=sfmc_archive,
            password=sfmc_password,
        )

        # for in_file in temp_path.iterdir():
        #     print(in_file)

        # Get gcs prefix
        glider_paths = paths.get_path_glider(
            deployment_name = deployment_name, 
            mode="delayed", 
        )
        _, match, after = glider_paths["archivesfmc"].partition(year)

        # Sync archive files to GCS
        gcp.sync_directory_to_gcs(
            local_dir=temp_path,
            bucket_name=defaults.BUCKET_DATA_IN,
            gcs_prefix=(match + after),
            delete=False,
        )
