import logging
import os
import time
from pathlib import Path

# os.environ["DBDREADER_C_EXTENSION"] = "1"
import dbdreader
import numpy as np
import pandas as pd

# from esdglider import gcp, paths, slocum, utils

logger = logging.getLogger(__name__)

# deployment_name = "calanus-20241019"
deployment_name = "amlr05-20211124"
mode = "delayed"

print(f"dbdreader version {dbdreader.__version__}")
print(os.environ.get("DBDREADER_C_EXTENSION"))
# gcsfuse --implicit-dirs -o ro swfscesd-glider-deployments-data-in calanus-20241019-binary/mnt

cachedir = "/home/user/standard-glider-files/Cache"
binarydir = "/home/user/tmp-binary/calanus-20260824-delayed"
# binarydir = "/home/user/calanus-20241019-binary/mnt/2024/calanus-20241019/binary/delayed"
# binarydir


if __name__ == "__main__":
    logging.basicConfig(
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    start_time = time.perf_counter()


    # sensors = [
    #     "m_depth", 
    #     "m_roll", 
    #     # "m_pitch", 
    #     # "sci_water_temp", 
    #     "sci_water_pressure", 
    #     "sci_flbbcd_chlor_units", 
    #     # "x_dbd_mission_number", 
    #     # "x_dbd_segment_number", 
    # ]
    sensors = [
        'sci_water_pressure', 
        'm_lat', 'm_lon', 'sci_water_cond', 'sci_water_temp', 
        'sci_flbbcd_chlor_units', 
        # 'sci_flbbcd_cdom_units', 'sci_flbbcd_bb_units', 
        # 'sci_oxy4_oxygen', 'sci_oxy4_saturation', 
        "sci_solocam_free_disk_space", "sci_solocam_image_files", 
        # 'm_heading', 'm_pitch', 'm_roll', 'm_final_water_vx', 'm_final_water_vy', 'm_depth', 'm_battery', 'm_battpos', 
        # 'm_coulomb_amphr', 'm_coulomb_amphr_total', 'm_de_oil_vol', 'm_leakdetect_voltage', 'm_leakdetect_voltage_forward', 
        # 'm_leakdetect_voltage_science', 'm_vacuum', 'm_tot_num_inflections', 'm_altitude', 'm_gps_lat', 'm_gps_lon', 
        # 'c_de_oil_vol', 'c_dive_target_depth', 'c_wpt_lat', 'c_wpt_lon', 
        # 'sci_flbbcd_chlor_sig', 'sci_flbbcd_cdom_sig', 'sci_flbbcd_bb_sig'
    ] 


    # ## OPTION 1 - all files
    # search = "*.[DEde][Bb][Dd]"
    # dbd = dbdreader.MultiDBD(
    #     pattern=f"{binarydir}/{search}", 
    #     cacheDir=cachedir, 
    #     # skip_initial_line = False, 
    # )
    # logger.info("Reading data from DBD files")
    # data_list = [(t, v) for (t, v) in dbd.get(*sensors, return_nans=True)]
    # data_time, data = zip(*data_list)


    # logger.info("Data list: %s", data_list)
    # for idx, i in enumerate(data):
    #     # print("Data point %d: %s", n, i)
    #     logger.info("Data points for sensor %s: %s", sensors[idx], i)
    #     # print(stats.describe(i))
    #     df = pd.DataFrame(i, columns=[sensors[idx]])
    #     print(df.describe())

    # # logger.info("Data time: %s", data_time)
    # # logger.info("Data: %s", data)

    # # Record the end time
    # end_time = time.perf_counter()

    # # Calculate and print total execution time
    # execution_time = end_time - start_time
    # logger.info("Function took %f seconds to complete.", execution_time)


    ### OPTION 2 - specific files
    dbdfiles_orig = [f.stem for f in Path(binarydir).iterdir() if f.is_file() and f.suffix in [".dbd", ".ebd"]]
    dbdfiles = sorted(set(dbdfiles_orig))
    for i in dbdfiles:
        logger.debug(i)

        # Files: 'corrupted' ecd files from calanus-20260824
        files_list = [
            "01840012", 
            "01940075", 
            "01830016", 
            "01840024", 
            "01900004", 
            "01920012", 
            "01850004", 
            "01940002", 
            "01830000", 
            "01830012", 
            "01940065", 
            "01900023", 
            "01940092", 
            "01840020", 
            "01940019", 
            "01830014", 
            "01940069"
        ]
        if i not in files_list:
            continue

        print(i)
        dbd = dbdreader.MultiDBD(
            pattern=f"{binarydir}/{i}.[de]bd", 
            cacheDir=cachedir, 
            # skip_initial_line = False, 
        )
        data_list = [(t, v) for (t, v) in dbd.get(*sensors, return_nans=True)]
        data_time, data = zip(*data_list)

        # logger.info("Data list: %s", data_list)
        for idx, i in enumerate(data):
            # print("Data point %d: %s", n, i)
            logger.info("Data info for sensor %s", sensors[idx])
            logger.info("Non-NaN data points: %d", np.count_nonzero(~np.isnan(i)))
            # df = pd.DataFrame(i, columns=[sensors[idx]])
            # print(df.describe())