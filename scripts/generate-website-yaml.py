import logging

import gspread
import sqlalchemy

import esdglider.config as config

"""
Make a yaml file for all glider deployments to display on the website

Scrape info from database, and generate a yaml file with deployment info.
Then loop through the deployments, and look at GCP to see what output
files have been created. Add this info to the yaml.
Write this yaml top the glider-lab-manual repo to be displayed

This script will normally have to be run
from a local computer to access the database

'db/glider-db-prod.txt' is the database URL, used to create the
sqlalchemy engine. It should not be committed to GitHub.
"""

# yaml_path = "C:/Users/sam.woodman/Downloads"
yaml_path = "../glider-lab-manual/content"

if __name__ == "__main__":
    logging.basicConfig(
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with open("db/glider-db-prod.txt", "r") as f:
        conn_string = f.read()    
        engine = sqlalchemy.create_engine(conn_string)
        with engine.connect() as connection:
            # Generate deployment table
            df_depl = config.get_deployment_table(connection)
            x = df_depl.copy(deep=True)
            x = x.drop(["Dates", "Sensors"], axis=1)

            # Write Deployments table to fleet status
            wk_name = "Deployments-Database"
            logging.info("Updating the Fleet Status %s sheet", wk_name)
            x = x.fillna("").rename({"Glider_Deployment_ID": "Deployment_ID"})
            gc = gspread.oauth()  # type: ignore
            sh = gc.open("Fleet Status")
            wk = sh.worksheet(wk_name)
            wk.update([x.columns.values.tolist()] + x.values.tolist())

            # # Update data validation formatting automatically..
            # from gspread.utils import ValidationConditionType
            # wk.add_validation(
            #     f'F2:L{1+x.shape[0]}',
            #     ValidationConditionType.one_of_list,
            #     ['TRUE', 'FALSE'],
            #     showCustomUi=True
            # )

            # Make website yaml
            config.make_website_yaml(df_depl, yaml_path)
