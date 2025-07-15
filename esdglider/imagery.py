import glob
import logging
import os
import statistics
from datetime import datetime

import numpy as np
import pandas as pd

import esdglider.utils as utils

_log = logging.getLogger(__name__)


def solocam_filename_dt(filename, dt_idx_start, format="%Y%m%d-%H%M%S"):
    """
    Parse solocam (imagery) filename to return associated datetime
    Requires index of start of datetime part of string

    Parameters
    ----------
    filename : str
        Full filename
    dt_idx_start : int
        The index of the start of the datetime string.
        The datetime inlcudes this index, plus the next 15 characters
        Sepcifically: filename[dt_idx_start : (dt_idx_start + 15)]
    format : str
        format passed to datetime.strptime

    Returns
    -------
        The datetime extracted from the imagery filename,
        returned as a 'datetime64[s]' object
    """

    solocam_substr = filename[dt_idx_start : (dt_idx_start + 15)]
    _log.debug(f"datetime substring: {solocam_substr}")
    solocam_dt = datetime.strptime(solocam_substr, format)
    solocam_dt64s = np.datetime64(solocam_dt).astype("datetime64[s]")

    return solocam_dt64s


def get_path_imagery(deployment_info: dict, imagery_path):
    """
    Return a dictionary of imagery-related paths
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
    imagery_path : str
        The path to the top-level folder of the imagery data.
        This is intended to be the path to the mounted raw imagery bucket

    Returns
    -------
    dict
        A dictionary with the relevant paths

    """

    # Extract or calculate relevant info
    deploymentyaml = deployment_info["deploymentyaml"]
    # mode = deployment_info["mode"]
    deployment = utils.read_deploymentyaml(deploymentyaml)

    deployment_name = deployment["metadata"]["deployment_name"]
    project = deployment["metadata"]["project"]
    year = utils.year_path(project, deployment_name)

    # Check that relevant deployment path exists
    imagery_deployment_path = os.path.join(
        imagery_path,
        project,
        year,
        deployment_name,
    )
    if not os.path.isdir(imagery_deployment_path):
        raise FileNotFoundError(f"{imagery_deployment_path} does not exist")

    # Return dictionary of file paths
    return {
        "imagedir": os.path.join(imagery_deployment_path, "images"),
        "configdir": os.path.join(imagery_deployment_path, "config"),
        "metadir": os.path.join(imagery_deployment_path, "metadata"),
    }


def imagery_timeseries(ds, paths, ext="jpg", dt_idx_start=None):
    """
    Matches up imagery files with data from pyglider by imagery filename
    Uses interpolated variables (hardcoded in function)
    Returns data frame with imagery+timeseries information

    Parameters
    ----------
    ds : xarray Dataset
        from science timeseries NetCDF
    imagery_dir : str
        path to folder with images, specifically the 'Dir####' folders
    ext : str, optional
        Imagery file extension. Default is 'jpg'.
    dt_idx_start : int | None
        The index of the beginning of the timestamp in the image file name.
        If None, then the index is determined as the index after the
        space in the file name

    Returns
    -------
        DataFrame: pd.DataFrame of imagery timeseries
    """

    deployment = ds.attrs["deployment_name"]
    imagedir = paths["imagedir"]
    metadir = paths["metadir"]
    _log.info(f"Creating imagery metadata file for {deployment}")
    _log.info(f"Using images directory {imagedir}")

    csv_file = os.path.join(metadir, f"{deployment}-imagery-metadata.csv")
    if os.path.isfile(csv_file):
        _log.info(f"Deleting old imagery metadata file: {csv_file}")
        os.remove(csv_file)

    # --------------------------------------------
    # Checks
    if not os.path.isdir(imagedir):
        raise FileNotFoundError(f"{imagedir} does not exist")
    else:
        # NOTE: this should probably be a separate function, and return a tuple
        filepaths = glob.glob(f"{imagedir}/**/*.{ext}", recursive=True)
        _log.debug(f"Found {len(filepaths)} files with the extension {ext}")
        if len(filepaths) == 0:
            _log.error(
                "Zero image files were found. Did you provide "
                + "the right path, and use the right file extension?",
            )
            raise ValueError("No files for which to generate metadata")
        imagery_files = [os.path.basename(path) for path in filepaths]
        imagery_dirs = [os.path.basename(os.path.dirname(path)) for path in filepaths]

    # --------------------------------------------
    # Extract info from imagery file names
    _log.debug("Processing imagery file names")

    # Check that all filenames have the same number of characters
    imagery_files_nchar = [len(i) for i in imagery_files]
    if not len(set(imagery_files_nchar)) == 1:
        _log.warning(
            "The imagery file names are not all the same length, "
            + "and thus shuld be checked carefully",
        )
        nchar_mode = statistics.mode(imagery_files_nchar)
        diff_idx = [i for i, f in enumerate(imagery_files) if len(f) != nchar_mode]
        diff_files = [f"{imagery_dirs[i]}/{imagery_files[i]}" for i in diff_idx]
        _log.warning(
            "The following filenames are of a different length: %s",
            ", ".join(diff_files),
        )

    if dt_idx_start is None:
        _log.info("Calculating the datetime index as the index after the space")
        space_idx = str.index(imagery_files[0], " ")
        if space_idx == -1:
            _log.error(
                "The imagery file name year index could not be found, "
                + "and thus the imagery metadata file cannot be generated",
            )
            raise ValueError("Incompatible file name spaces")
        dt_idx_start = space_idx + 1
    _log.debug("dt_idx_start %s", dt_idx_start)

    imagery_files_dt = np.array(
        [solocam_filename_dt(i, dt_idx_start) for i in imagery_files],
    )

    # TODO: filter for dates after deployment_min_dt?

    df = pd.DataFrame(
        data={
            "img_file": imagery_files,
            "img_dir": imagery_dirs,
            "time": imagery_files_dt,
        },
    ).sort_values(by="img_file", ignore_index=True)

    # --------------------------------------------
    # Create metadata file
    _log.info("Interpolating glider data to image timestamps")
    ds_prof = ds[["profile_index", "profile_direction"]]

    # Must filter df.time for times >= start of ds_prof.time
    img_times = df.time[df.time >= min(ds_prof.time.values)].values
    ds_sel = ds_prof.reindex(time=img_times, method="pad")
    df = df.join(ds_sel.to_pandas(), on="time", how="left")

    # For each variable that exists, extract interpolated values to df
    ds_interp = ds.interp(time=df.time.values)
    # NOTE: ds.interp 'account for' nans, meaning if nans are the previous
    # timestamp they are interpolated through. This is what we want,
    # because the timeseries has had max_gap applied

    vars_toignore = [
        # handled above
        "profile_index",
        "profile_direction",
        # in standard ESD datasets, but not necessary here
        "distance_over_ground",
        "waypoint_latitude",
        "waypoint_longitude",
        "water_velocity_eastward",
        "water_velocity_northward",
    ]
    vars_list = [var for var in list(ds.data_vars) if var not in vars_toignore]

    for var in vars_list:
        _log.debug(f"Interpolating var {var}")
        if var not in list(ds_interp.keys()):
            _log.debug(f"{var} not present in ds - skipping interp")
            continue
        df[var] = ds_interp[var].values

    # --------------------------------------------
    # Export metadata file
    utils.mkdir_pass(metadir)
    _log.info(f"Writing imagery metadata to: {csv_file}")
    df.to_csv(csv_file, index=False)

    return df
