import collections
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import gsw
import numpy as np
import pandas as pd
import pytz
import xarray as xr
import yaml

_log = logging.getLogger(__name__)


"""
Utilities, mostly specific to ESD needs and ways of processing
"""


# For IOOS-compliant encoding when writing to NetCDF
def to_netcdf_esd(ds: xr.Dataset, outname: str):
    _log.info(f"Writing dataset with ESD encoding to: {outname}")
    ds.to_netcdf(
        outname,
        "w",
        encoding={
            "time": {
                "units": "seconds since 1970-01-01T00:00:00Z",
                "_FillValue": np.nan,
                "calendar": "gregorian",
                "dtype": "float64",
            },
        },
    )


"""
default optionsList for findProfiles.
Pulled outside so it can also be used by get_fill_profiles. Values from:
https://github.com/socib/glider_toolbox/blob/master/m/processing_tools/processGliderData.m#L113
"""
profileOptionsList = {
    "length": 10,
    "period": 0,
    "inversion": 3,
    "interrupt": 180,
    "stall": 3,
    "shake": 20,
}


def findProfiles(stamp: np.ndarray, depth: np.ndarray, **kwargs):
    """
    -----
    Function copied from:
    https://github.com/OceanGNS/PGPT/blob/main/scripts/gliderfuncs.py#L196

    The only edits are a) pre-commit formatting and
    b) Updating the default kwargs optional argument values.
    These have been updated to match SOCIB:
    https://github.com/socib/glider_toolbox/blob/master/m/processing_tools/processGliderData.m#L113
    -----

    Identify individual profiles and compute vertical direction from depth sequence.

    Args:
            stamp (np.ndarray): A 1D array of timestamps.
            depth (np.ndarray): A 1D array of depths.
            **kwargs (optional): Optional arguments including:
                    - length (int): Minimum length of a profile (default=10).
                    - period (float): Minimum duration of a profile (default=0).
                    - inversion (float): Maximum depth inversion between cast segments of a profile (default=3).
                    - interrupt (float): Maximum time separation between cast segments of a profile (default=180).
                    - stall (float): Maximum range of a stalled segment (default=3).
                    - shake (float): Maximum duration of a shake segment (default=20).

    Returns:
            profile_index (np.ndarray): A 1D array of profile indices.
            profile_direction (np.ndarray): A 1D array of vertical directions.
    """
    if not (isinstance(stamp, np.ndarray) and isinstance(depth, np.ndarray)):
        stamp = stamp.to_numpy()
        depth = depth.to_numpy()

    # Flatten input arrays
    depth, stamp = depth.flatten(), stamp.flatten()

    # Check if the stamp is a datetime object and convert to elapsed seconds if necessary
    if np.issubdtype(stamp.dtype, np.datetime64):
        stamp = (stamp - stamp[0]).astype("timedelta64[s]").astype(float)

    # Set default parameter values (did not set type np.timedelta64(0, 'ns') )
    optionsList = {
        "length": 10,
        "period": 0,
        "inversion": 3,
        "interrupt": 180,
        "stall": 3,
        "shake": 20,
    }
    # Filter for relevant kwards, in case any others got passed in
    # Added because optionsList is now returned 
    kwargs = {key: value for key, value in kwargs.items() if key in optionsList.keys()}
    optionsList.update(kwargs)
    _log.info(
        "Running findProfiles with the following kwargs: %s",
        ", ".join([f"{k}: {v}" for k, v in optionsList.items()]),
    )

    validIndex = np.argwhere(
        np.logical_not(np.isnan(depth)) & np.logical_not(np.isnan(stamp)),
    ).flatten()
    validIndex = validIndex.astype(int)

    sdy = np.sign(np.diff(depth[validIndex], n=1, axis=0))
    depthPeak = np.ones(np.size(validIndex), dtype=bool)
    depthPeak[1 : len(depthPeak) - 1,] = np.diff(sdy, n=1, axis=0) != 0
    depthPeakIndex = validIndex[depthPeak]
    sgmtFrst = stamp[depthPeakIndex[0 : len(depthPeakIndex) - 1,]]
    sgmtLast = stamp[depthPeakIndex[1:,]]
    sgmtStrt = depth[depthPeakIndex[0 : len(depthPeakIndex) - 1,]]
    sgmtFnsh = depth[depthPeakIndex[1:,]]
    sgmtSinc = sgmtLast - sgmtFrst
    sgmtVinc = sgmtFnsh - sgmtStrt
    sgmtVdir = np.sign(sgmtVinc)

    castSgmtValid = np.logical_not(
        np.logical_or(
            np.abs(sgmtVinc) <= optionsList["stall"],
            sgmtSinc <= optionsList["shake"],
        ),
    )
    castSgmtIndex = np.argwhere(castSgmtValid).flatten()
    castSgmtLapse = (
        sgmtFrst[castSgmtIndex[1:]]
        - sgmtLast[castSgmtIndex[0 : len(castSgmtIndex) - 1]]
    )
    castSgmtSpace = -np.abs(
        sgmtVdir[castSgmtIndex[0 : len(castSgmtIndex) - 1]]
        * (
            sgmtStrt[castSgmtIndex[1:]]
            - sgmtFnsh[castSgmtIndex[0 : len(castSgmtIndex) - 1]]
        ),
    )
    castSgmtDirch = np.diff(sgmtVdir[castSgmtIndex], n=1, axis=0)
    castSgmtBound = np.logical_not(
        (castSgmtDirch[:,] == 0)
        & (castSgmtLapse[:,] <= optionsList["interrupt"])
        & (castSgmtSpace <= optionsList["inversion"]),
    )
    castSgmtHeadValid = np.ones(np.size(castSgmtIndex), dtype=bool)
    castSgmtTailValid = np.ones(np.size(castSgmtIndex), dtype=bool)
    castSgmtHeadValid[1:,] = castSgmtBound
    castSgmtTailValid[0 : len(castSgmtTailValid) - 1,] = castSgmtBound

    castHeadIndex = depthPeakIndex[castSgmtIndex[castSgmtHeadValid]]
    castTailIndex = depthPeakIndex[castSgmtIndex[castSgmtTailValid] + 1]
    castLength = np.abs(depth[castTailIndex] - depth[castHeadIndex])
    castPeriod = stamp[castTailIndex] - stamp[castHeadIndex]
    castValid = np.logical_not(
        np.logical_or(
            castLength <= optionsList["length"],
            castPeriod <= optionsList["period"],
        ),
    )
    castHead = np.zeros(np.size(depth))
    castTail = np.zeros(np.size(depth))
    castHead[castHeadIndex[castValid] + 1] = 0.5
    castTail[castTailIndex[castValid]] = 0.5

    profileIndex = 0.5 + np.cumsum(castHead + castTail)
    profileDirection = np.empty((len(depth)))
    profileDirection[:] = np.nan

    for i in range(len(validIndex) - 1):
        iStart = validIndex[i]
        iEnd = validIndex[i + 1]
        profileDirection[iStart:iEnd] = sdy[i]

    return profileIndex, profileDirection, optionsList


def get_fill_profiles(ds, time_var, depth_var, **kwargs) -> xr.Dataset:
    """
    Calculate profile index and direction values,
    and fill both the values and attributes into ds

    ds : `xarray.Dataset`
    time_var, depth_var: Variable names of time and depth in ds
        Values from these variables passed directly to utils.findProfiles

    returns Dataset
    """
    # if np.any(np.isnan(ds.depth.values)):
    #     num_nan = sum(np.isnan(ds.depth.values))
    #     _log.warning(f"There are {num_nan} nan depth values")

    time_vals = ds[time_var].values
    depth_vals = ds[depth_var].values
    prof_idx, prof_dir, prof_opt = findProfiles(time_vals, depth_vals, **kwargs)

    idx_comment = (
        "N = inside profile N, N + 0.5 = between profiles N and N + 1. "
        + "Parameters listed as attributes"
    )
    attrs = collections.OrderedDict(
        [
            ("long_name", "profile index"),
            ("units", "1"),
            ("comment", idx_comment),
            ("sources", f"{time_var} {depth_var}"),
            ("method", "esdglider.utils.findProfiles"),
        ]
        + [(key, val) for key, val in prof_opt.items()],
    )
    ds["profile_index"] = (time_var, prof_idx, attrs)

    attrs = collections.OrderedDict(
        [
            ("long_name", "glider vertical speed direction"),
            ("units", "1"),
            ("comment", "-1 = ascending, 0 = inflecting or stalled, 1 = descending"),
            ("sources", f"{time_var} {depth_var}"),
            ("method", "esdglider.utils.findProfiles"),
        ],
    )
    ds["profile_direction"] = (time_var, prof_dir, attrs)

    _log.debug(f"There are {np.max(ds.profile_index.values)} profiles")

    return ds


def join_profiles(ds, df, **kwargs):
    """
    'Join' profile indexes to a dataset by time,
    using a summary dataframe with profile start and end times

    Parameters
    ----------
    ds : xarray.Dataset
        Timeseries dataset, onto which to join the profile indices
    df : pandas.DataFrame
        Profile summary dataframe; output of calc_profile_summary()
        Contains the true profile indices
    **kwargs:
        findProfile arguments, included here for metadata

    Returns
    -------
    xarray.dataset
        Dataset ds, with new profile_index column
    """

    time_values = ds["time"].values
    idx_values = np.full(time_values.shape, np.nan, dtype=np.float64)
    for _, row in df.iterrows():
        # time start/ends are mutually exclusive, so use >= and <=
        mask = (time_values >= row["start_time"]) & (time_values <= row["end_time"])
        idx_values[mask] = row["profile_index"]

    # Sanity checks, if relevant
    if "profile_index" in ds.keys():
        abs_idx_diff = abs(ds.profile_index.values - idx_values).max()
        if abs_idx_diff > 1:
            _log.info(
                "The absolute value of the old minus new index values is %s",
                abs_idx_diff,
            )

    if any(np.isnan(idx_values)):
        _log.warning(
            "There are %s nan profile index values",
            np.count_nonzero(np.isnan(idx_values)),
        )

    # Attributes and add to dataset
    idx_comment = (
        "N = inside profile N, N + 0.5 = between profiles N and N + 1. "
        + "Parameters listed as attributes"
    )
    attrs = collections.OrderedDict(
        [
            ("long_name", "profile index"),
            ("units", "1"),
            ("comment", idx_comment),
            ("sources", "time depth"),
            ("method", "esdglider.utils.findProfiles (run on the raw dataset)"),
        ]
        + [(key, val) for key, val in kwargs.items()],
    )
    ds["profile_index"] = ("time", idx_values, attrs)

    return ds


def drop_bogus_times(
    ds: xr.Dataset,
    min_dt: str = "1970-01-01",
    max_drop: bool = False,
) -> xr.Dataset:
    """
    Drop bogus times.
    This function is separate to allow users to drop only bogus times.
    See the function 'drop_bogus' for a description of arguments
    """

    # For out of range or nan time/lat/lon, drop rows
    num_orig = len(ds.time)
    num_orig_nan = np.count_nonzero(np.isnan(ds.time.values))
    ds = ds.where(ds.time >= np.datetime64(min_dt), drop=True)
    if (num_orig - len(ds.time)) > 0:
        _log.info(
            "Dropped %s times that were either nan (n=%s) or before '%s'",
            num_orig - len(ds.time), 
            num_orig_nan, 
            min_dt, 

        )

    if max_drop:
        num_orig = len(ds.time)
        max_dt = np.datetime64(datetime_now_utc("%Y-%m-%dT%H:%M:%S"))
        ds = ds.where(ds.time <= np.datetime64(max_dt), drop=True)
        if (num_orig - len(ds.time)) > 0:
            _log.warning(
                "Dropped %s times that were after the current UTC time %s",
                num_orig - len(ds.time), 
                max_dt
            )

    return ds


def drop_bogus(
    ds: xr.Dataset,
    min_dt: str = "1970-01-01",
    max_drop: bool = False,
) -> xr.Dataset:
    """
    Remove and/or drop bogus times and values.
    Rows with bogus time or lat/lons are dropped.
    For other bogus values, out of range values are changed to np.nan

    ds: `xarray.Dataset`
        processed glider data
    min_dt: str; default="1970-01-01"
        String represting the minimum datetime to keep.
        Passed to np.datetime64 to be used to filter.
        For instance, '2017-01-01', or '2020-03-06 12:00:00'.

    Returns
    -------
    xarray Dataset
        Dataset with bogus rows rows dropped, and bodus values changed to nan
    """

    # if not (ds_type in ['sci', 'eng']):
    #     raise ValueError('ds_type must be either sci or eng')

    # # For out of range or nan time/lat/lon, drop rows
    # num_orig = len(ds.time)
    # ds = ds.where(ds.time >= np.datetime64(min_dt), drop=True)
    # if (num_orig - len(ds.time)) > 0:
    #     _log.info(
    #         f"Dropped {num_orig - len(ds.time)} times "
    #         + f"that were either nan or before {min_dt}",
    #     )

    # Drop bogus times, as specified
    ds = drop_bogus_times(ds, min_dt, max_drop=max_drop)

    # Drop bogus lat/lons
    num_orig = len(ds.time)
    ll_good = (
        (ds.longitude >= -180)
        & (ds.longitude <= 180)
        & (ds.latitude >= -90)
        & (ds.latitude <= 90)
    )
    ds = ds.where(ll_good, drop=True)
    if (num_orig - len(ds.time)) > 0:
        _log.info(
            "Dropped %s nan or out of range lat/lons",
            num_orig - len(ds.time)
        )

    # For science variables, change out of range values to nan
    drop_values = {
        "conductivity": [0, 60],
        "temperature": [-5, 100],
        "pressure": [-2, 1500],
        "chlorophyll": [0, 30],
        "cdom": [0, 30],
        "backscatter_700": [0, 5],
        "oxygen_concentration": [-100, 500],
        "salinity": [0, 50],
        "potential_density": [900, 1050],
        "density": [1000, 1050],
        "potential_temperature": [-5, 100],
    }

    for var, value in drop_values.items():
        if var not in list(ds.keys()):
            _log.debug(
                "%s not present in ds - skipping drop_values check", 
                num_orig - len(ds.time)
            )
            continue
        num_orig = len(ds[var])
        good = (ds[var] >= value[0]) & (ds[var] <= value[1])
        ds[var] = ds[var].where(good, drop=False)
        if num_orig - len(ds[var]) > 0:
            _log.info(
                "Changed %s %s values outside range [%s, %s] to nan",
                num_orig - len(ds[var]), 
                var, 
                value[0], 
                value[1], 
            )

    return ds


def get_file_id_esd(ds) -> str:
    """
    ESD's version of pyglider.utils.get_file_id
    This version does not require the glider_serial
    Make a file id for a Dataset: Id = *glider_name* + "YYYYMMDDTHHMM"
    """

    _log.debug(ds.time)
    if not ds.time.dtype == "datetime64[ns]":
        dt = ds.time.values[0].astype("timedelta64[s]") + np.datetime64("1970-01-01")
    else:
        dt = ds.time.values[0].astype("datetime64[s]")
    _log.debug("dt %s", dt)
    id = (
        ds.attrs["glider_name"]
        # + ds.attrs['glider_serial']
        + "-"
        + dt.item().strftime("%Y%m%dT%H%M")
    )
    return id


def read_deploymentyaml(deploymentyaml: str):
    """
    Read the yaml file located at deploymentyaml, and return as a dictionary
    """
    if not os.path.isfile(deploymentyaml):
        raise FileNotFoundError(f"Could not find {deploymentyaml}")
    with open(deploymentyaml) as fin:
        deployment_ = yaml.safe_load(fin)

    return deployment_


def data_var_reorder(ds, new_start):
    """
    Reorder the data variables of a dataset

    new_start is a list of the data variable names from ds that
    will be moved to 'first' in the dataset

    Returns ds, with reordered data variables
    """

    ds_vars_orig = list(ds.data_vars)
    if not all([i in ds_vars_orig for i in new_start]):
        _log.error("new_start %s", new_start)
        _log.error("ds.data_vars %s", ds_vars_orig)
        raise ValueError("All values of new_start must be in ds.data_vars")

    new_order = new_start + [i for i in ds.data_vars if i not in new_start]
    ds = ds[new_order]

    # Double check that all values are present in new ds
    if not (
        all(
            [j in ds_vars_orig for j in new_order]
            + [j in new_order for j in ds_vars_orig],
        )
    ):
        raise ValueError("Error reordering data variables")

    return ds


def datetime_now_utc(format="%Y-%m-%dT%H:%M:%SZ"):
    """
    format : str
        format string; passed to strftime function
        https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior

    Returns a string with the current date/time, in UTC,
        controlled by 'format' input
    """
    return datetime.now(timezone.utc).strftime(format)


def encode_times(ds):
    """
    Straight from:
    https://github.com/voto-ocean-knowledge/votoutils/blob/main/votoutils/utilities/utilities.py
    """
    if "units" in ds.time.attrs.keys():
        ds.time.attrs.pop("units")
    if "calendar" in ds.time.attrs.keys():
        ds.time.attrs.pop("calendar")
    ds["time"].encoding["units"] = "seconds since 1970-01-01T00:00:00Z"
    for var_name in list(ds):
        if "time" in var_name.lower() and not var_name == "time":
            for drop_attr in ["units", "calendar", "dtype"]:
                if drop_attr in ds[var_name].attrs.keys():
                    ds[var_name].attrs.pop(drop_attr)
            ds[var_name].encoding["units"] = "seconds since 1970-01-01T00:00:00Z"
    return ds


def split_deployment(deployment_name):
    """
    Split the deployment string into glider name, and date deployed
    Splits by "-"
    Returns a tuple of the glider name and deployment date
    """
    deployment_split = deployment_name.split("-")
    deployment_date = deployment_split[1]
    if len(deployment_date) != 8:
        _log.error(
            "The deployment must be the glider name, "
            + "followed by the deployment date",
        )
        raise ValueError(f"Invalid glider deployment date: {deployment_date}")

    return deployment_split


def year_path(project, deployment_name):
    """
    From the glider project and deployment name (both strings),
    generate and return the year string to use in file paths
    for ESD glider deployments

    For the FREEBYRD project, this will be the year of the second
    half of the Antarctic season. For instance, hypothetical
    FREEBYRD deployments amlr01-20181231 and amlr01-20190101 are both
    during season '2018-19', and thus would return '2019'.

    For all other projects, the value returned is simply the year.
    For example, ringo-20181231 would return 2018,
    and ringo-20190101 would return 2019
    """

    deployment_split = split_deployment(deployment_name)
    deployment_date = deployment_split[1]
    year = deployment_date[0:4]

    if project == "FREEBYRD":
        month = deployment_date[4:6]
        if int(month) <= 7:
            year = f"{int(year)}"
        else:
            year = f"{int(year) + 1}"

    return year


def mkdir_pass(dir):
    """
    Convenience wrapper to try to make a directory path,
    and pass if it already exists
    """
    _log.debug(f"Trying to make directory {dir}")
    try:
        os.mkdir(dir)
    except FileExistsError:
        pass


def makedirs_pass(dir):
    """
    Convenience wrapper to try to make a directory path,
    and pass if it already exists
    """
    _log.debug(f"Trying to make directory {dir}")
    if not os.path.exists(dir):
        os.makedirs(dir)


def rmtree(dir, ignore_errors=False):
    """
    Light wrapper around shutil.rmtree
    Checks if directory exists before deleting
    """
    if os.path.isdir(dir):
        _log.info(f"Removing the following directory and all files in it: {dir}")
        shutil.rmtree(dir, ignore_errors=ignore_errors)


def remove_file(file_path):
    """
    Light wrappoer to check if a file exists at file_path,
    and to remove it if so
    """
    if os.path.exists(file_path):
        _log.info(f"Removing file: {file_path}")
        os.remove(file_path)
    else:
        _log.debug(f"No file to remove at: {file_path}")


def find_extensions(dir_path):  # ,  excluded = ['', '.txt', '.lnk']):
    """
    Get all the file extensions in the given directory
    From https://stackoverflow.com/questions/45256250
    """
    extensions = set()
    for _, _, files in Path(dir_path).walk():
        for f in files:
            extensions.add(Path(f).suffix)
            # ext = Path(f).suffix.lower()
            # if not ext in excluded:
            #     extensions.add(ext)
    return extensions


def line_prepender(filename, line):
    """
    Title: prepend-line-to-beginning-of-a-file
    https://stackoverflow.com/questions/5914627
    """

    with open(filename, "r+") as f:
        content = f.read()
        f.seek(0, 0)
        f.write(line.rstrip("\r\n") + "\n" + content)


def calc_ts(ds):
    """
    Calculate variables for temperature/salinity plots
    Code adapted from Jacob Partida
    """
    s_lims = (
        np.floor(np.min(ds.salinity) - 0.5),
        np.ceil(np.max(ds.salinity) + 0.5),
    )

    t_lims = (
        np.floor(np.min(ds.potential_temperature) - 0.5),
        np.ceil(np.max(ds.potential_temperature) + 0.5),
    )
    # print(t_lims)
    S = np.arange(s_lims[0], s_lims[1] + 0.1, 0.1)
    T = np.arange(t_lims[0], t_lims[1] + 0.1, 0.1)
    Tg, Sg = np.meshgrid(T, S)
    sigma = gsw.sigma0(Sg, Tg)

    return Sg, Tg, sigma


"""Dictionary for mapping profile_direction values to strings"""
direction_phase_mapping = {1: "descent", -1: "ascent"}


# Define helper functions for calc_profile_summary
def _profile_agg(group, tas_depth=5):
    """
    Custom aggregation function for profile_summary.
    See 'calc_profile_summary' docs for more details

    Parameters
    ----------
    'group' is the current pandas series group from calc_profile_summary
    'tas_depth' is the maximum depth that is considered the surface
        for 'time at surface' calculations.
    """

    # Profile direction is 0 if between profiles
    if all(group["profile_index"] % 1 == 0.5):
        profile_direction = 0
    else:
        profile_direction = group["profile_direction"].mode().iloc[0]

    # Get start and end depths - drop in case any depths are nan
    depth_nona = group["depth"].dropna().values
    if depth_nona.shape[0] == 0:
        start_depth = np.nan
        end_depth = np.nan
        min_depth = np.nan
        max_depth = np.nan
        depth_range = np.nan
    else:
        start_depth = depth_nona[0]
        end_depth = depth_nona[-1]
        min_depth = depth_nona.min()
        max_depth = depth_nona.max()
        depth_range = abs(max_depth - min_depth)

    # Get min and max lat/lons
    lat_nona = group["latitude"].dropna().values
    if lat_nona.shape[0] == 0:
        min_lat = np.nan
        max_lat = np.nan
    else:
        min_lat = lat_nona.min()
        max_lat = lat_nona.max()

    lon_nona = group["longitude"].dropna().values
    if lon_nona.shape[0] == 0:
        min_lon = np.nan
        max_lon = np.nan
    else:
        min_lon = lon_nona.min()
        max_lon = lon_nona.max()

    # Time at surface
    surface_pts = group["time"][group["depth"] <= tas_depth]
    if surface_pts.shape[0] == 0:
        tas = 0
    else:
        tas = int((surface_pts.max() - surface_pts.min()).total_seconds())

    # Profile phase and duration calculations can be vectorized,
    # and thus they are calculates after the aggregation
    # in calc_profile_summary

    return pd.Series(
        {
            "profile_direction": profile_direction,
            "start_time": group["time"].min(),
            "end_time": group["time"].max(),
            "start_depth": start_depth,
            "end_depth": end_depth,
            "min_depth": min_depth,
            "max_depth": max_depth,
            "depth_range": depth_range,
            "min_lon": min_lon,
            "max_lon": max_lon,
            "min_lat": min_lat,
            "max_lat": max_lat,
            "distance_traveled": np.ptp(group["distance_over_ground"]),
            "num_points": group.shape[0],
            "time_at_surface_s": tas,
        },
    )


def calc_profile_phase(profile_index, profile_direction, min_depth):
    """
    Determine the phase of the profile.
    Even though this is by profile, rather than the terminology is from
    https://github.com/OceanGlidersCommunity/OG-format-user-manual/blob/main/vocabularyCollection/phase.md

    Note that inflection may be happen after an ascent or descent,
    depending on if the glider makes it to the surface.
    A surfacing only occurs if profile_direction=0 and proifle min_depth<1

    Returns an array of profile phase descriptions
    """
    prof = profile_index % 1 == 0
    surf = min_depth < 1

    profile_phase = np.where(
        prof,
        profile_direction.map(direction_phase_mapping),
        np.where(surf, "surfacing", "inflection"),
    )

    return profile_phase


# def _calc_profile_description(df, surface_depth=10):
#     """Determine if a between profile is at the surface or at depth"""
#     st = df["start_depth"] < surface_depth
#     en = df["end_depth"] < surface_depth
#     prof = df["profile_index"] % 1 == 0
#     prof_description = np.where(
#         prof,
#         df["profile_direction"].map(direction_phase_mapping),
#         np.where(st | en, "surfacing", "inflection")
#     )
#     return prof_description


def calc_profile_summary(ds: xr.Dataset, depth_var: str) -> pd.DataFrame:
    """
    For each profile, ie after grouping by profile_index,
    calculate summary information.

    Parameters
    ----------
    ds : xarray Dataset
        Dataset with glider timeseries data. Can be raw, eng, or sci
    depth_var: str
        Variable names of depth in ds

    Returns
    -------
    pandas Dataframe
        'profile summary' data frame. All data are on a by-profile basis.
        Columns include:
            - profile_index: The profile index
            - profile_direction: 1/-1/0, indicating dive/climb/between profiles
            - profile_phase: See documentation for 'calc_profile_phase'
            - start/end time: the minimum/maximum timestamps
            - start/end depth: the first/last non-nan depth value
            - depth_range: the abs value of the difference between the depth min/max
            - min/max lat/lon: the minimum/maximum latitudes and longitudes
            - distance_traveled: the distance traveled during that profile (max-min)
            - num_points: the number of records during that profile
            - time_at_surface_s: the time at the surface, in integer seconds.
                The amount of time during the profile the glider was at a depth <5m.
                In seconds, not timedelta, for more intuitive writing to CSV files
            - profile_duration_s: the difference between the time max/min.
                In seconds, not timedelta, for more intuitive writing to CSV files
    """
    # Minimum columns needed by aggregation function
    _log.info("Calculating profile summary using var %s", depth_var)
    ds = ds.rename({depth_var: "depth"})    
    grouped_columns = [
        "time",
        "depth",
        "profile_index",
        "distance_over_ground",
        "profile_direction",
        "latitude",
        "longitude",
    ]

    # Group by profile_index, and run profile aggregation function
    df = (
        ds.to_pandas()
        .reset_index()
        .groupby(["profile_index"], as_index=False)[grouped_columns]
        .apply(_profile_agg)
    )

    # Calculate additional variables, and return
    df["profile_duration_s"] = (df["end_time"] - df["start_time"]).dt.total_seconds()
    df["profile_phase"] = calc_profile_phase(
        df["profile_index"],
        df["profile_direction"],
        df["min_depth"],
    )

    new_start = ["profile_index", "profile_direction", "profile_phase"]
    df_cols = new_start + [i for i in df.columns if i not in new_start]
    df = df[df_cols]

    return df


def check_profiles(df: pd.DataFrame) -> pd.DataFrame:
    """
    Perform profile sanity checks, including:
    - Check for the same numbers of dive and climb profiles
    - Checks that no surface profiles have a start or end depth of >3
    - Checks that no deep profiles have a depth range of >10
    - Checks for More than 60s at the surface (ie above 5m) during a profile

    Parameters
    ----------
    df : pandas DataFrame
        Output of calc_profile_summary()

    Returns
    -------
    pandas Dataframe
        The unchanged input df
    """

    # _log.info("Calculating profile summaries")
    # df = calc_profile_summary(ds, depth_var)

    # Generate profile summary data frame, other products
    _log.info("Starting profile checks")
    diveclimb_df = df[df["profile_index"] % 1 == 0.0]
    between_df = df[df["profile_index"] % 1 == 0.5]
    between_surf = between_df[between_df.profile_phase == "surfacing"]
    # between_infl = between_df[between_df.profile_phase == "inflection"]

    # Check: the number of dives and climbs are the same
    num_profiles = df.shape[0]
    num_dives = np.count_nonzero(df["profile_direction"] == 1)
    num_climbs = np.count_nonzero(df["profile_direction"] == -1)
    str_divesclimbs = f"dives: {num_dives}; climbs: {num_climbs}"
    _log.debug("Total profiles: %s; %s", num_profiles, str_divesclimbs)

    if num_dives != num_climbs:
        _log.warning(
            f"There are different numbers of dives and climbs: {str_divesclimbs}",
        )

    # Check: sequence of as
    # 1) All ascents/descents are followed by a surfacing/inflection,
    # 2) All inflections (including surfacings) are followed by an ascent/descent
    df1 = df["profile_direction"].iloc[:-1]
    df1_shift = df["profile_direction"].shift(-1).iloc[:-1]
    e1 = df1.isin([1, -1]) & ~df1_shift.isin([0])
    e2 = df1.isin([0]) & ~df1_shift.isin([1, -1])

    if e1.any():
        df_error = df.iloc[:-1].loc[e1, "profile_index"]
        _log.warning(
            " (OPT) The following %s dives/climbs are not followed by an inflection: %s",
            df_error.shape[0],
            ", ".join([str(i) for i in df_error.values]),
        )

    if e2.any():
        df_error = df.iloc[:-1].loc[e2, "profile_index"]
        _log.warning(
            "The following %s inflections are not followed by a dive/climb: %s",
            df_error.shape[0],
            ", ".join([str(i) for i in df_error.values]),
        )

    # 3) All inflections are 2-after followed by an inflection
    # 4) All dives are 2-after followed by a climb
    # 5) All climbs are 2-after followed by a dive
    df2 = df["profile_direction"].iloc[:-2]
    df2_shift = df["profile_direction"].shift(-2).iloc[:-2]

    e3 = (df2 == 0) & (df2_shift != 0)
    e4 = (df2 == 1) & (df2_shift != -1)
    e5 = (df2 == -1) & (df2_shift != 1)
    if e3.any():
        df_error = df.iloc[:-2].loc[e3, "profile_index"]
        _log.warning(
            "(OPT) The following %s inflections are not 2-followed by an inflection: %s",
            df_error.shape[0],
            ", ".join([str(i) for i in df_error.values]),
        )
    if e4.any():
        df_error = df.iloc[:-2].loc[e4, "profile_index"]
        _log.warning(
            "(OPT) The following %s dives are not 2-followed by a climb: %s",
            df_error.shape[0],
            ", ".join([str(i) for i in df_error.values]),
        )
    if e5.any():
        df_error = df.iloc[:-2].loc[e5, "profile_index"]
        _log.warning(
            "(OPT) The following %s climbs are not 2-followed by a dive: %s",
            df_error.shape[0],
            ", ".join([str(i) for i in df_error.values]),
        )

    # Check: no surface profiles have both a start or end depth of >3
    # The depth range check makes sure we don't catch gliders that turn around
    # below the surface
    depth_start_end_check = between_surf[
        (
            ((between_surf["start_depth"] > 3) | (between_surf["end_depth"] > 3))
            & (between_surf["depth_range"] > 3)
        )
    ]
    if depth_start_end_check.shape[0] > 0:
        _log.warning(
            "There are %s surface profiles "
            + "that have both a start or end depth >3m, and depth range >3m. "
            + "Profile indices: %s",
            depth_start_end_check.shape[0],
            ", ".join([str(i) for i in depth_start_end_check.profile_index.values]),
        )

    # Check: no between (inflection/surfacing) profiles have a depth range of >10
    if between_df.depth_range.max() >= 10:
        df_towarn = between_df.profile_index[between_df.depth_range >= 10]
        _log.warning(
            "The depth difference is >= 10 for %s 'between' profile(s): %s",
            df_towarn.shape[0],
            ", ".join([str(i) for i in df_towarn.values]),
        )

    # Check: More than 60s at the surface (ie above 5m) during a profile
    surface_max = 180
    tas_check = diveclimb_df[diveclimb_df["time_at_surface_s"] >= surface_max]
    if tas_check.shape[0] > 0:
        _log.warning(
            "There are %s profiles with more "
            + "than %ss at depths less than or equal to 5m. "
            + "Profile indices: %s",
            tas_check.shape[0],
            surface_max,
            ", ".join([str(i) for i in tas_check.profile_index.values]),
        )

    return df


def check_depth(x: xr.DataArray, y: xr.DataArray, depth_ok=5) -> xr.Dataset:
    """
    Parameters
    ----------
    x : xarray DataArray
        DataArray of the glider measured depth (i.e., m_depth)
        Must have dimension 'time'.
        For ESD, this argument will often be tseng["depth"] or tsraw["depth"]
    y : xarray DataArray
        DataArray of the CTD depth (i.e., depth calculated from sci_water_pressure).
        Must have dimension 'time'.
        For ESD, this argument will often be tssci["depth"] or tsraw["depth_ctd"]
    depth_ok : numeric
        The maximum acceptable depth difference. If the absolute value of
        the difference between the measured depth and CTD-calcualted depth
        is greater than this, a warning will be raised

    Returns
    -------
    An xarray dataset with variables
    x ("depth_measured") and y ("depth_ctd"), as well as
    1) x interpolated onto all timestamps of y ("depth_measured_interp"),
    2) the difference between y and interpolated da1 ("depth_diff"), and
    3) the absolute difference between y and interpolated da1 ("depth_diff_abs")
    """

    _log.info("Starting depth checks (measured vs CTD)")

    # Interpolate x onto the time points of y, and get the differences
    x_interp = x.dropna("time").interp(time=y["time"])
    depth_diff = abs(x_interp - y)
    depth_diff_abs = abs(depth_diff)
    depth_diff_max = np.nanmax(depth_diff_abs)
    _log.info(
        "The max absolute difference between the glider measured depth and "
        + "depth calculated from the CTD is %sm",
        np.round(depth_diff_max, 1),
    )
    _log.debug(depth_diff.to_pandas().describe())
    if depth_diff_max > depth_ok:
        _log.warning(
            "The max absolute depth difference is greater than %sm",
            depth_ok,
        )
        d = depth_diff_abs.to_pandas()
        _log.warning(d[depth_diff_abs.values>depth_ok].describe())

    ds = xr.merge(
        [
            x.rename("depth_measured"),
            y.rename("depth_ctd"),
            x_interp.rename("depth_measured_interp"),
            depth_diff.rename("depth_diff"),
            depth_diff_abs.rename("depth_diff_abs"),
        ],
    )

    return ds


def get_utc_offset_integer(timezone_name, dt_object, is_dst=None):
    """
    Returns the integer UTC offset in hours for a given Olsen (IANA) timezone
    and a specific datetime object.
    Adapted from Gemini

    Parameters
    ----------
    timezone_name: str
        The Olsen (IANA) time zone name (e.g., 'America/New_York').
    dt_object : datetime.datetime
        A datetime object representing the point in time for which to calculate the offset
    is_dst : boolean | None (default None)
        Passed to pytz's localize. 'None' allows pytz to determine DST

    Returns
    -------
    int
        The UTC offset, in hours as an integer, for the given timezone and date
    """

    try:
        tz = pytz.timezone(timezone_name)
        localized_dt = tz.localize(dt_object, is_dst=is_dst)
        offset_timedelta = localized_dt.utcoffset()
        # Convert timedelta to total seconds and then to hours
        offset_hours = int(offset_timedelta.total_seconds() / 3600)  # type: ignore
        return offset_hours
    except pytz.UnknownTimeZoneError:
        print(f"Error: Unknown time zone '{timezone_name}'.")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def get_sunrise_sunset(time, lat, lon):
    """
    Heavily based on GliderTools.optics.sunset_sunrise.
    The glidertools function calculates the local sunrise/sunset of the
    glider location using the Skyfield package.
    However, it does not account for the local time, and thus the
    joined sunrise/sunset times are often not right for the given local day

    Currently, this function groups the timestamps by local day,
    and calculates the mean lat/lon. These are passed to the skyfield package
    to calculate a single sunrise/sunset for each day, rather than calculating
    sunrise/sunset for each individual point. Thus, this assumes the glider
    doesn't travel far enough in one day to make the sunrise/sunset
    meaningfully different for different points during that day.

    If there is a 'polar' sunrise/sunset, i.e. the sun doesn't actually
    rise or set as defined by skyfield,
    then a nan is returned for sunrise/sunset for that day.

    Specificly:
    1) Calculates local timezone string for each image using timezonefinder,
        lat, and lon. because of the grouping (described next), if there are
        multiple timezones the most common is chosen
    2) Groups the image timestamps by local day,
        and calculates the mean lat/lon for each day.
    3) For each day timestamp, which has a local time of 00:00:00:
        - Calculate the UTC time.
        - Use skyfield to calculate any sunrises/sunsets for the given lat/lon,
        between the UTC time, and the UTC time + one day. This guarantees
        there will be exactly one sunrise and one sunset in the given window.
        If there is a 'polar' sunrise or sunset, return nan.
        Add these values to the grouped data frame.
    4) Left join the original data frame and the grouped data frame
        (with sunrise/sunset times) by local day.
    5) Return sunrise_local, sunset_local, time_local

    Parameters
    ----------
    time: numpy.ndarray or pandas.Series
        The date & time array in a numpy.datetime64 format, in UTC.
        This parameter cannot have nans.
    lat: numpy.ndarray or pandas.Series
        The latitude of the glider position. This parameter cannot have nans.
    lon: numpy.ndarray or pandas.Series
        The longitude of the glider position. This parameter cannot have nans.

    Returns
    -------
    All arrays are of the same length as input time, and of type datetime64[s]

    sunrise: numpy.ndarray
        An array of the sunrise times, in local time.
    sunset: numpy.ndarray
        An array of the sunset times, in local time.
    local time: numpy.ndarray
        An array of the calculated local times.
    """
    
    from skyfield import almanac, api
    from timezonefinder import TimezoneFinder

    ts = api.load.timescale()
    eph = api.load("de421.bsp")
    sun = eph["Sun"]

    # Determine local timezones
    _log.info("Calculating local timezone string")
    tf = TimezoneFinder()
    tz_all = np.array([tf.timezone_at(lat=i, lng=j) for i, j in zip(lat, lon)])

    # Establish working dataframe, and convert times to local
    df = pd.DataFrame.from_dict(
        dict([("time", time), ("lat", lat), ("lon", lon)]),
    )
    df["time"] = df["time"].dt.tz_localize("UTC")

    uq, counts = np.unique(tz_all, return_counts=True)
    if uq.shape[0] == 1:
        tz = uq[0]
    else:
        _log.warning("The points span multiple timezones. Using the most frequent tz")
        _log.warning("unique %s ", uq)
        _log.warning("counts %s ", counts)
        tz = uq[np.argmax(counts)]
    _log.info("Timezone '%s'", tz)

    # Calculate a column for local day
    df["time_local"] = df["time"].dt.tz_convert(tz.item())
    df["day_local"] = (
        df["time_local"].dt.tz_localize(None).values.astype("datetime64[D]")
    )

    # Group by local day
    grp_avg = (
        df.groupby(pd.Grouper(key="time_local", freq="D"))
        .mean(numeric_only=False)
        .reset_index(drop=False)
    )
    grp_avg["time_utc"] = grp_avg["time_local"].dt.tz_convert("UTC")

    # Caluclate and save relevant sunrises and sunsets
    sunrise_list = []
    sunset_list = []
    for n, row in grp_avg.iterrows():
        _log.debug("n %s", n)
        _log.debug("row %s", row)
        observer = eph["Earth"] + api.wgs84.latlon(row["lat"], row["lon"])
        date = row["time_utc"]

        t0 = ts.utc(date.year, date.month, date.day, date.hour)
        t1 = ts.utc(date.year, date.month, date.day + 1, date.hour)

        tu, yu = almanac.find_risings(observer, sun, t0, t1)
        td, yd = almanac.find_settings(observer, sun, t0, t1)

        # Since the times span exactly 24hrs, we assume there will be exactly
        # one each sunrise/sunset. But might as well check
        if (len(tu) != 1) or (len(td) != 1):
            _log.warning("An almanac output has a length of not one")
            _log.warning("row %s", row)

        # Sunrise
        if yu:
            su_local = pd.to_datetime(tu.utc_iso(" ")).tz_convert(tz)
        else:
            _log.debug("polar sunrise")
            su_local = np.nan
        sunrise_list.append(su_local)

        # Sunset
        if yd:
            sd_local = pd.to_datetime(td.utc_iso(" ")).tz_convert(tz)
        else:
            _log.debug("polar sunset")
            sd_local = np.nan
        sunset_list.append(sd_local)

    # Join local sunrise/sunset with data by local day
    sunrise_local = np.array(sunrise_list).squeeze()
    sunset_local = np.array(sunset_list).squeeze()
    grp_avg["sunrise_local"] = sunrise_local
    grp_avg["sunset_local"] = sunset_local
    grp_avg_tojoin = grp_avg[["day_local", "sunrise_local", "sunset_local"]]

    # Coerce to np.datetime64, while maintaining local timezone
    df_out = pd.merge(df, grp_avg_tojoin, how="left", on="day_local")
    sunrise_local = (
        df_out["sunrise_local"].dt.tz_localize(None).to_numpy().astype("datetime64[s]")
    )
    sunset_local = (
        df_out["sunset_local"].dt.tz_localize(None).to_numpy().astype("datetime64[s]")
    )
    time_local = (
        df_out["time_local"].dt.tz_localize(None).to_numpy().astype("datetime64[s]")
    )

    return sunrise_local, sunset_local, time_local
