import logging

import gspread
import sqlalchemy
import pandas as pd

import esdglider.config as config

"""
CDOM
"""

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
            x = x[["Deployment_Name", "Project", "Start", "Glider_Deployment_ID"]]
            x = x.sort_values(['Project', 'Deployment_Name'])

            # Get flbbcd serial number, calibration date
            Deployment_Device = pd.read_sql_table("vDeployment_Device", connection, "dbo")
            Deployment_Device_Calibration = pd.read_sql_table(
                "vDeployment_Device_Calibration", 
                connection, 
                "dbo", 
            )
            flbbcd_key = "flbbcd"
            x["serial_number"] = ""
            x["calibration_date"] = ""

            for i, row in x.iterrows():
                depl_id = row['Glider_Deployment_ID']
                depl_name = row['Deployment_Name']
                logging.debug('Glider_Deployment_ID %s', depl_id)
                logging.debug('Deployment Name %s', depl_name)

                db_device_flbbcd = Deployment_Device[
                    (
                        (Deployment_Device["Glider_Deployment_ID"] == depl_id)
                        & (Deployment_Device["Component"] == config.db_components[flbbcd_key])
                    )
                ]
                db_cals = Deployment_Device_Calibration[
                    Deployment_Device_Calibration["Glider_Deployment_ID"] == depl_id
                ]

                if db_device_flbbcd.shape[0] > 0:
                    instr = config._get_instrument_attrs(
                        "flbbcd", 
                        db_device_flbbcd, 
                        db_cals
                    )
                    x.loc[i, "serial_number"] = instr["serial_number"]
                    x.loc[i, "calibration_date"] = instr["calibration_date"]
                else:
                    logging.info('No flbbcd for %s', depl_name)


            # db_devices = Deployment_Device[
            #     Deployment_Device["Glider_Deployment_ID"] == glider_depl_id
            # ]
            # db_cals = Deployment_Device_Calibration[
            #     Deployment_Device_Calibration["Glider_Deployment_ID"] == glider_depl_id
            # ]




            # Write Deployments table to fleet status
            wk_name = "Ecopuck-corrections"
            logging.info("Updating the Fleet Status %s sheet", wk_name)
            x = x.drop(["Glider_Deployment_ID"], axis=1).fillna("")
            gc = gspread.oauth()  # type: ignore
            sh = gc.open("Fleet Status")
            wk = sh.worksheet(wk_name)
            wk.update([x.columns.values.tolist()] + x.values.tolist())

            # # # Update data validation formatting automatically..
            # # from gspread.utils import ValidationConditionType
            # # wk.add_validation(
            # #     f'F2:L{1+x.shape[0]}',
            # #     ValidationConditionType.one_of_list,
            # #     ['TRUE', 'FALSE'],
            # #     showCustomUi=True
            # # )

            # # Make website yaml
            # config.make_website_yaml(df_depl, yaml_path)
