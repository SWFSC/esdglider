import logging
import os
import time

# import pandas as pd
import xarray as xr

from esdglider import gcp, glider, plots


def main():
    logging.basicConfig(
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    deployment_name = "amlr04-20231128"
    # deployment_name = "amlr08-20220513"
    mode = "delayed"

    # Standard
    bucket_name = "amlr-gliders-deployments-dev"
    deployments_path = f"/home/sam_woodman_noaa_gov/{bucket_name}"
    config_path = "/home/sam_woodman_noaa_gov/glider-lab/deployment-configs"

    gcp.gcs_mount_bucket("amlr-gliders-deployments-dev", deployments_path, ro=False)
    deployment_info = {
        "deploymentyaml": os.path.join(config_path, f"{deployment_name}.yml"),
        "mode": mode,
    }
    paths = glider.get_path_glider(deployment_info, deployments_path)

    # ds_sci = xr.load_dataset(paths["tsscipath"])
    # ds_eng = xr.load_dataset(paths["tsengpath"])
    ds_g5 = xr.load_dataset(paths["gr5path"])

    # 2. Call the parallel function
    start_time = time.time()

    plots.esd_all_plots(
        {
            "outname_tseng": paths["tsengpath"],
            "outname_tssci": paths["tsscipath"],
            "outname_gr5m": paths["gr5path"],
            "outname_tsraw": paths["tsrawpath"],
        },
        crs=None,
        base_path="/home/sam_woodman_noaa_gov/plots-test",
        max_workers=3,
    )
    # plots.esd_all_plots(outname_dict, crs=None, base_path=paths["plotdir"])
    # g5sci = xr.load_dataset(outname_dict["outname_gr5m"])
    plots.sci_surface_map_loop(
        ds_g5,
        crs="Mercator",
        base_path="/home/sam_woodman_noaa_gov/plots-test",
        figsize_x=11,
        figsize_y=8.5,
    )

    # plots.sci_timeseries_loop(
    #     # ds_g5,
    #     ds_sci,
    #     # ds_eng,
    #     base_path="/home/sam_woodman_noaa_gov/plots-test",
    #     max_workers=None
    # )

    end_time = time.time()
    logging.info(f"execution took {end_time - start_time:.2f} seconds.")


if __name__ == "__main__":
    main()
