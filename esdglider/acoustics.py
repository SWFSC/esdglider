import datetime
import logging
import math
import os

import pandas as pd
import xarray as xr

import esdglider.utils as utils

_log = logging.getLogger(__name__)


def get_path_acoustics_deployment(
    deployment_path: str, 
    deployment_name: str, 
    mode: str, 
) -> dict:
    """
    Get deployment-specific acoustics paths. 
    Specifically, get all acoutics paths that are within 
    the given deployment folder (deployment_path)

    This function is typically called by get_path_acoustics()
    """

    metadir = os.path.join(deployment_path, "metadata")
    echoviewdir = os.path.join(metadir, "echoview")
    
    regionspath = os.path.join(echoviewdir, f"{deployment_name}-regions.csv")
    pitchpath = os.path.join(echoviewdir, f"{deployment_name}.pitch.csv")
    rollpath = os.path.join(echoviewdir, f"{deployment_name}.roll.csv")
    gpspath = os.path.join(echoviewdir, f"{deployment_name}.gps.csv")
    depthpath = os.path.join(echoviewdir, f"{deployment_name}.depth.evl")
    evrpathprefix = os.path.join(echoviewdir, deployment_name)

    return {
        "rawdatadir": os.path.join(deployment_path, "data", mode),
        "configdir": os.path.join(deployment_path, "config"),
        "metadir": metadir,
        "echoviewdir": echoviewdir, 
        "regionspath": regionspath, 
        "pitchpath": pitchpath, 
        "rollpath": rollpath, 
        "gpspath": gpspath, 
        "depthpath": depthpath, 
        "evrpathprefix": evrpathprefix, 
    }

def get_path_acoustics(deployment_info: dict, acoustic_path: str):
    """
    Return a dictionary of acoustic-related paths
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    Parameters
    ----------
    deployment_info : dict
        A dictionary with the relevant deployment info. Specifically:
        deploymentyaml : str
            The filepath of the glider deployment yaml.
            This file will have relevant info,
            including deployment name (eg, amlr01-20210101) and project
        mode : str
            Mode of the glider data being processed.
            Must be either 'rt', for real-time, or 'delayed
    acoustic_path : str
        The path to the top-level folder of the acoustic data.
        This is intended to be the path to the mounted acoustic bucket

    Returns
    -------
    dict
        A dictionary with the relevant acoustic paths
    """

    # Extract or calculate relevant info
    deploymentyaml = deployment_info["deploymentyaml"]
    mode = deployment_info["mode"]
    deployment = utils.read_deploymentyaml(deploymentyaml)

    deployment_name = deployment["metadata"]["deployment_name"]
    project = deployment["metadata"]["project"]
    year = utils.year_path(project, deployment_name)

    # Check that relevant deployment path exists
    acoustic_deployment_path = os.path.join(
        acoustic_path,
        project,
        year,
        deployment_name,
    )
    if not os.path.isdir(acoustic_deployment_path):
        raise FileNotFoundError(f"{acoustic_deployment_path} does not exist")

    # Return dictionary of file paths
    deployment_paths_out = get_path_acoustics_deployment(
        acoustic_deployment_path, deployment_name, mode
    )    
    return deployment_paths_out


def regions_evr(ds: xr.Dataset, evr_file_prefix: str) -> pd.DataFrame:
    """
    From the science timeseries dataset: 1) calculate dive/climb regions,
    2) format output columns, and 3) write dive and climb regions evr files.
    To get just the regions dataframe, see utils.calc_regions

    Parameters
    ----------
    ds : xarray.Dataset
        Science timeseries dataset
    evr_file_prefix : str
        The file name+path prefix to use for the EVR regions file.
        The output filename will be f"{evr_file_prefix}-...-regions.evr"

    Returns
    -------
    pd.DataFrame
        The regions dataframe, with formatted output columns
    """

    # Process the dataset to create 'regions' dataframe
    _log.debug("Calculating regions")
    regions_df = utils.calc_profile_summary(ds, "depth").assign(
        start_date_str=lambda d: d["start_time"].dt.strftime("%Y%m%d"),
        start_time_str=lambda d: d["start_time"].dt.strftime("%H%M%S0000"),
        end_date_str=lambda d: d["end_time"].dt.strftime("%Y%m%d"),
        end_time_str=lambda d: d["end_time"].dt.strftime("%H%M%S0000"),
    )

    # Set values that are used throughout the EVR files
    start_depth = -1
    end_depth = 1000

    # For each of the dive and climb regions:
    direction_mapping = {1: "Dive", -1: "Climb"}
    for i, r in direction_mapping.items():
        _log.info(f"Processing region {r}")
        # Filter for dives/climbs, and set associated variables
        df = regions_df[regions_df["profile_direction"] == i].reset_index(drop=True)
        region_vec = ["EVRG 7 10.0.298.38422", str(len(df))]

        # Loop through each row and generate the file contents
        for row in df.itertuples():
            idx = row.Index + 1  # type: ignore
            line1 = (
                f"13 4 {idx} 0 3 -1 1 "
                + f"{row.start_date_str} {row.start_time_str} {start_depth} "
                + f"{row.end_date_str} {row.end_time_str} {end_depth}"
            )
            line5 = (
                f"{row.start_date_str} {row.start_time_str} {end_depth} "
                + f"{row.end_date_str} {row.end_time_str} {end_depth} "
                + f"{row.end_date_str} {row.end_time_str} {start_depth} "
                + f"{row.start_date_str} {row.start_time_str} {start_depth} 1"
            )
            # 'Append' this row's contents to the region vector
            region_vec.extend(["", line1, "0", "0", r, line5, f"Region {idx}"])

        # Write the regions EVR file
        with open(f"{evr_file_prefix}-{r.lower()}-regions.evr", "w") as f:
            f.write("\n".join(region_vec) + "\n")

    return regions_df


def echoview_metadata(ds: xr.Dataset, paths: dict):
    """
    Create metadata files for Echoview acoustics data processing

    Parameters
    ----------
    ds : xarray.Dataset
        Science timeseries dataset
    paths : dict
        A dictionary of acoustic file/directory paths
        See get_path_acoustics for the expected key/value pairs

    Returns
    -------
    Nothing
    """

    deployment_name = ds.attrs["deployment_name"]
    _log.info(f"Creating echoview metadata for {deployment_name}")

    # Prep - making paths, variables, etc used throughout
    # path_echoview = os.path.join(paths["metadir"], "echoview")
    path_echoview = paths["echoviewdir"]
    utils.rmtree(path_echoview)
    utils.mkdir_pass(paths["metadir"])
    utils.mkdir_pass(path_echoview)
    # file_echoview_pre = os.path.join(path_echoview, deployment_name)
    _log.info(f"Will write echoview metadata files to {path_echoview}")

    ds_dt = ds.time.values.astype("datetime64[s]").astype(datetime.datetime)
    mdy_str = [i.strftime("%m/%d/%Y") for i in ds_dt]
    hms_str = [i.strftime("%H:%M:%S") for i in ds_dt]

    # Regions
    _log.info("Processing regions files")
    regions_df = regions_evr(ds, paths["evrpathprefix"])
    # regions_df_path = f"{file_echoview_pre}-regions.csv"
    regions_csv = paths["regionspath"]
    _log.info("Writing regions CSV to %s", regions_csv)
    regions_df.to_csv(regions_csv, index=False)

    _log.info("Other echoview metadata files")
    # Pitch
    _log.debug("pitch")
    pitch_df = pd.DataFrame(
        {
            "Pitch_date": mdy_str,
            "Pitch_time": hms_str,
            "Pitch_angle": [math.degrees(x) for x in ds["pitch"].values],
        },
    )
    # pitch_df.to_csv(f"{file_echoview_pre}.pitch.csv", index=False)
    pitch_df.to_csv(paths["pitchpath"], index=False)

    # Roll
    _log.debug("roll")
    roll_df = pd.DataFrame(
        {
            "Roll_date": mdy_str,
            "Roll_time": hms_str,
            "Roll_angle": [math.degrees(x) for x in ds["roll"].values],
        },
    )
    # roll_df.to_csv(f"{file_echoview_pre}.roll.csv", index=False)
    roll_df.to_csv(paths["rollpath"], index=False)

    # GPS
    _log.debug("gps")
    gps_df = pd.DataFrame(
        {
            "GPS_date": [i.strftime("%Y-%m-%d") for i in ds_dt],
            "GPS_time": hms_str,
            "Latitude": ds["latitude"].values,
            "Longitude": ds["longitude"].values,
        },
    )
    gps_df.to_csv(paths["gpspath"], index=False)
    # gps_df.to_csv(f"{file_echoview_pre}.gps.csv", index=False)

    # Depth
    _log.debug("depth")
    depth_df = pd.DataFrame(
        {
            "Depth_date": [i.strftime("%Y%m%d") for i in ds_dt],
            "Depth_time": [f"{i.strftime('%H%M%S')}0000" for i in ds_dt],
            "Depth": ds["depth"].values,
            "repthree": 3,
        },
    )
    depth_file = paths["depthpath"] #f"{file_echoview_pre}.depth.evl"
    depth_df.to_csv(depth_file, index=False, header=False, sep="\t")
    utils.line_prepender(depth_file, str(len(depth_df.index)))
    utils.line_prepender(depth_file, "EVBD 3 8.0.73.30735")

    # Wrap up
    _log.info("Finished writing echoview metadata files")
