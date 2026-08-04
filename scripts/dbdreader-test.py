import os
from pathlib import Path
import numpy as np
import logging
import time
# import pandas as pd

# os.environ["DBDREADER_C_EXTENSION"] = "1"

import dbdreader
# from esdglider import gcp, paths, slocum, utils


# deployment_name = "calanus-20241019"
deployment_name = "amlr05-20211124"
mode = "delayed"

print(f"dbdreader version {dbdreader.__version__}")
print(os.environ.get("DBDREADER_C_EXTENSION"))
# gcsfuse --implicit-dirs -o ro swfscesd-glider-deployments-data-in calanus-20241019-binary/mnt

# binarydir = "/home/user/calanus-20241019-binary/delayed2"
binarydir = "/home/user/calanus-20241019-binary/mnt/2024/calanus-20241019/binary/delayed"
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
        # 'm_lat', 'm_lon', 'sci_water_cond', 'sci_water_temp', 'sci_flbbcd_chlor_units', 
        # 'sci_flbbcd_cdom_units', 'sci_flbbcd_bb_units', 
        # 'sci_oxy4_oxygen', 'sci_oxy4_saturation', 
        "sci_solocam_free_disk_space", "sci_solocam_image_files", 
        # 'm_heading', 'm_pitch', 'm_roll', 'm_final_water_vx', 'm_final_water_vy', 'm_depth', 'm_battery', 'm_battpos', 
        # 'm_coulomb_amphr', 'm_coulomb_amphr_total', 'm_de_oil_vol', 'm_leakdetect_voltage', 'm_leakdetect_voltage_forward', 
        # 'm_leakdetect_voltage_science', 'm_vacuum', 'm_tot_num_inflections', 'm_altitude', 'm_gps_lat', 'm_gps_lon', 
        # 'c_de_oil_vol', 'c_dive_target_depth', 'c_wpt_lat', 'c_wpt_lon', 
        # 'sci_flbbcd_chlor_sig', 'sci_flbbcd_cdom_sig', 'sci_flbbcd_bb_sig'
    ] 
    ## OPTION 1 - all files
    search = "*.[DEde][BCbc][Dd]"
    dbd = dbdreader.MultiDBD(
        pattern=f"{binarydir}/{search}", 
        cacheDir="standard-glider-files/Cache", 
        # skip_initial_line = False, 
    )
    logging.info("Reading data from DBD files")
    data_list = [(t, v) for (t, v) in dbd.get(*sensors, return_nans=True)]
    data_time, data = zip(*data_list)


    print(data_list)
    # print(data_time)
    # print(data)

    # Record the end time
    end_time = time.perf_counter()

    # Calculate and print total execution time
    execution_time = end_time - start_time
    print(f"Function took {execution_time:.6f} seconds to complete.")


    # ### OPTION 2 - specific files
    # dbdfiles_orig = [f.stem for f in Path(binarydir).iterdir() if f.is_file()]
    # dbdfiles = sorted(list(set(dbdfiles_orig)))
    # for i in dbdfiles:
    #     logging.debug(i)
    #     dbd = dbdreader.MultiDBD(
    #         pattern=f"{binarydir}/{i}*", 
    #         cacheDir="standard-glider-files/Cache", 
    #         # skip_initial_line = False, 
    #     )
    #     data_list = [(t, v) for (t, v) in dbd.get(*sensors, return_nans=True)]
    #     data_time, data = zip(*data_list)
    #     # print(data_list)
