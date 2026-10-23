"""
Centralized default settings and constants for ESD glider operations.
"""

# -----------------------------------------------------------------------------
# Google Cloud Platform (GCP) Infrastructure
# -----------------------------------------------------------------------------
GCP_PROJECT_ID = "ggn-nmfs-swfscesd-prod-1"

SECRET_ID_SFMC = "sfmc-swoodman"
SECRET_ID_NGDAC = "ioos-ngdac-ftp"

# -----------------------------------------------------------------------------
# Google Cloud Storage (GCS) Buckets
# -----------------------------------------------------------------------------
BUCKET_DATA_IN = "swfscesd-glider-deployments-data-in"
BUCKET_DATA_OUT = "swfscesd-glider-deployments-data-out"
BUCKET_IMAGERY_IN = "swfscesd-glider-imagery-data-in"
BUCKET_IMAGERY_META = "swfscesd-glider-imagery-metadata"
BUCKET_AA_IN = "swfscesd-glider-active-acoustics-data-in"
BUCKET_LOGS = "swfscesd-glider-logs"

# -----------------------------------------------------------------------------
# Remote Servers & Endpoints
# -----------------------------------------------------------------------------
SFMC_HOST = "sfmc.webbresearch.com"
SFMC_USER = "swoodman"
SFMC_REMOTE_BASE_DIR = "/var/opt/sfmc-dockserver/stations/noaa/gliders"

NGDAC_HOST = "gliders.ioos.us"
NGDAC_USER = "aerd"

PROCESSING_REPO_URL = "https://github.com/SWFSC/glider-processing"

DB_SCHEMA = "dbo"
