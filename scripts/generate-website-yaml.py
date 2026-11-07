import logging
import os

import gspread
import pandas as pd
import sqlalchemy
import yaml

from esdglider import metadata

logger = logging.getLogger(__name__)

"""
Make a yaml file for all glider deployments to display on the website

Scrape info from database, and generate a yaml file with deployment info.
Then loop through the deployments, and look at GCP to see what output
files have been created. Add this info to the yaml.
Write this yaml top the glider-lab-manual repo to be displayed

This script has to be run from a location that can access the database

'db/glider-db-prod.txt' is the database URL, used to create the
sqlalchemy engine. It should not be committed to GitHub.
"""

yaml_filename = "../glider-lab-manual/content/esd-gliders.yml"


def update_yaml_urls(
    yaml_file_path: str, 
    df_urls: pd.DataFrame, 
    as_html: bool = False
):
    """
    Updates report_link and ERDDAP_link in the deployment YAML file
    using a DataFrame containing: deployment_name, report_url, erddap_ioos_url.
    """
    if not os.path.exists(yaml_file_path):
        logger.error("YAML file not found: %s", yaml_file_path)
        return

    # Normalize column names to lowercase/stripped
    df_urls = df_urls.copy()
    df_urls.columns = df_urls.columns.str.strip().str.lower()

    # Build a lookup dictionary keyed by normalized deployment name
    url_lookup = {}
    for _, row in df_urls.iterrows():
        dep_name = str(row.get("deployment_name", "")).strip().lower()
        if not dep_name or dep_name == "nan":
            continue

        rep = str(row.get("report_url", "")).strip() if pd.notna(row.get("report_url")) else ""
        erd = str(row.get("erddap_ioos_url", "")).strip() if pd.notna(row.get("erddap_ioos_url")) else ""

        # Format as HTML anchor tags if matching the website's gcp_link format
        if as_html:
            rep = f"<a href='{rep}'>report</a>" if rep else ""
            erd = f"<a href='{erd}'>IOOS</a>" if erd else ""

        url_lookup[dep_name] = {"report_link": rep, "ERDDAP_link": erd}

    # Load existing YAML
    with open(yaml_file_path, "r", encoding="utf-8") as f:
        deployments = yaml.safe_load(f)

    if not isinstance(deployments, list):
        logger.error("Expected a list of deployments in %s", yaml_file_path)
        return

    # Update matching deployments
    updated_count = 0
    for item in deployments:
        dep_key = str(item.get("Deployment_Name", "")).strip().lower()
        if dep_key in url_lookup:
            links = url_lookup[dep_key]
            if links["report_link"]:
                item["report_link"] = links["report_link"]
            if links["ERDDAP_link"]:
                item["ERDDAP_link"] = links["ERDDAP_link"]
            updated_count += 1

    # Write back to YAML, preserving key order
    with open(yaml_file_path, "w", encoding="utf-8") as f:
        yaml.dump(deployments, f, sort_keys=False, default_flow_style=False)

    logger.info("Updated links for %d deployments in %s", updated_count, yaml_file_path)



if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    logging.basicConfig(
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with open("db/glider-db-prod.txt", "r") as f:
        conn_string = f.read()
        engine = sqlalchemy.create_engine(conn_string)
        with engine.connect() as connection:
            # 1. Generate deployment table from database
            df_depl = metadata.get_deployment_table(connection)

            # REMOVE amlr01-20191206
            df_depl = df_depl[(df_depl['Deployment_Name'] != 'amlr01-20191206')]

            # 2. Make website yaml
            metadata.make_website_yaml(
                df=df_depl,
                outfile=yaml_filename,
                glider_data_out_bucket_name="swfscesd-glider-deployments-data-out",
                aa_in_bucket_name="swfscesd-glider-active-acoustics-data-in",
                imagery_in_bucket_name="swfscesd-glider-imagery-data-in",
            )

    # 3. Read Google Sheet: 'Fleet Status' -> tab 'Deployment Links'
    logger.info("Fetching link data from Google Sheets...")
    
    # Authenticate: use service_account() if headless/automated, or oauth() for local interactive runs
    gc = gspread.oauth()  # Alternative: gc = gspread.service_account(filename="path/to/credentials.json")
    
    spreadsheet = gc.open("Fleet Status")
    worksheet = spreadsheet.worksheet("Deployment Links")
    df_urls = pd.DataFrame(worksheet.get_all_records())

    # 4. Inject URLs into the generated YAML
    update_yaml_urls(yaml_filename, df_urls, as_html=True)