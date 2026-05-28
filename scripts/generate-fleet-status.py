import logging

import gspread
import sqlalchemy
import pandas as pd

import esdglider.config as config

"""
Write various database views and summaries to the ESD Fleet Status page

Scrape info from database, and generate a yaml file with deployment info.
Then loop through the deployments, and look at GCP to see what output
files have been created. Add this info to the yaml.
Write this yaml top the glider-lab-manual repo to be displayed

This script has to be run from a location with database access

'db/glider-db-prod.txt' is the database URL, used to create the
sqlalchemy engine. It should not be committed to GitHub.
"""

def write_to_sheet(sh, wk_name, df):
    # wk_name = "Devices-Calibrations"
    logging.info("Updating the %s worksheet on the %s spreadsheet", wk_name, sh.title)
    wk = sh.worksheet(wk_name)
    wk.update([df.columns.values.tolist()] + df.values.tolist())

if __name__ == "__main__":
    logging.basicConfig(
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    gc = gspread.oauth()  # type: ignore
    sh = gc.open("Fleet Status")

    with open("db/glider-db-prod.txt", "r") as f:
        conn_string = f.read()    
        engine = sqlalchemy.create_engine(conn_string)
        with engine.connect() as connection:
            # Generate deployment table, and write to fleet status
            df_depl = config.get_deployment_table(connection)
            x = df_depl.copy(deep=True)
            x = x.drop(["Dates", "Sensors"], axis=1)
            x = x.fillna("").rename({"Glider_Deployment_ID": "Deployment_ID"})
            
            write_to_sheet(sh, "Deployments-Database", x)

            # wk_name = "Deployments-Database"
            # logging.info("Updating the Fleet Status %s sheet", wk_name)
            # wk = sh.worksheet(wk_name)
            # wk.update([x.columns.values.tolist()] + x.values.tolist())

            # Generate device calibrations table, and write to sheet
            cals = pd.read_sql_table("vDevice_Calibration", connection, "dbo")
            cals_columns = [
                'Manufacturer', 'Component', 'Model', 'Serial_Num', 'Calibration_Type',
                'Calibration_Date', 'Coefficient', 'Calibration_Description', 
                # 'Device_ID', 'Device_Calibration_ID', 
                'Calibration_Created_Dt'
            ]
            cals = cals[cals_columns].sort_values(["Component", "Serial_Num", "Calibration_Date"])
            cals["Calibration_Date"] = cals["Calibration_Date"].dt.strftime("%Y-%m-%d")
            cals["Calibration_Created_Dt"] = cals["Calibration_Created_Dt"].dt.strftime("%Y-%m-%d %H:%M:%S")
            cals = cals.fillna("")
            write_to_sheet(sh, "Devices-Calibrations", cals)


            # wk_name = "Devices-Calibrations"
            # logging.info("Updating the Fleet Status %s sheet", wk_name)
            # wk = sh.worksheet(wk_name)
            # wk.update([x.columns.values.tolist()] + x.values.tolist())

            # # Update data validation formatting automatically..
            # from gspread.utils import ValidationConditionType
            # wk.add_validation(
            #     f'F2:L{1+x.shape[0]}',
            #     ValidationConditionType.one_of_list,
            #     ['TRUE', 'FALSE'],
            #     showCustomUi=True
            # )