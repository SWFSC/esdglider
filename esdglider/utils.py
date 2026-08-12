import collections
import logging
import os
import shutil
from datetime import datetime, timezone, date
from pathlib import Path

import ast
import gsw
import numpy as np
import pandas as pd
import pytz
import statistics
import xarray as xr
import yaml

_log = logging.getLogger(__name__)


"""
Utilities, mostly specific to ESD needs and ways of processing
"""

# Logistical utilities ########################################################
def _get_deployment_netcdfvars(deploymentyaml):
    """
    Loop through deploymentyaml files, and concatenate all netcdf vars
    from the various deployment YAML files.
    Allows for yaml files with only netcdf variables, 
    for raw and engineering datasets.

    Parameters
    ----------
    deploymentyaml : str or list of str
        Path(s) to the deployment YAML file(s).

    Returns
    -------
    dict
        A dictionary containing the concatenated NetCDF variables from the deployment YAML files.
    """
    ncvar = {}
    if isinstance(deploymentyaml, str):
        deploymentyaml = [deploymentyaml]
    for nn, d in enumerate(deploymentyaml):
        with open(d) as fin:
            deployment_ = yaml.safe_load(fin)
            if "netcdf_variables" in deployment_.keys():
                for key, value in deployment_["netcdf_variables"].items():
                    if key not in ncvar:
                        ncvar[key] = value

    return ncvar


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
    _log.info("Dropping bogus times")

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
                max_dt,
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
        String representing the minimum datetime to keep.
        Passed to np.datetime64 to be used to filter.
        For instance, '2017-01-01', or '2020-03-06 12:00:00'.
    max_drop: bool; default=False
        If True, drop times that are after the current UTC time.

    Returns
    -------
    xarray Dataset
        Dataset with bogus rows rows dropped, and bogus values changed to nan
    """

    # Drop bogus times, as specified
    _log.info("Dropping bogus values")
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
            num_orig - len(ds.time),
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
                num_orig - len(ds.time),
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


def dataframe_col_reorder(df: pd.DataFrame, new_start):
    """
    Reorder the columns of a dataframe

    new_start is a list of the data variable names from df that
    will be moved to 'first' in the dataframe

    Returns df, with reordered columns
    """
    cols_orig = df.columns
    if not all([i in cols_orig for i in new_start]):
        _log.error("new_start %s", new_start)
        _log.error("df.columns %s", cols_orig)
        raise ValueError("All values of new_start must be in df.columns")

    new_order = new_start + [col for col in df.columns if col not in new_start]
    df = df[new_order]

    # Double check that all values are present in new ds
    if not (
        all(
            [j in cols_orig for j in new_order]
            + [j in new_order for j in cols_orig],
        )
    ):
        raise ValueError("Error reordering data variables")

    return df


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


def year_path(deployment_name):
    """
    From the glider project and deployment name (both strings),
    generate and return the year string to use in file paths
    for ESD glider deployments

    Given Prod directory structure changes, for all deployments now
    the value returned is simply the year.
    For example, ringo-20181231 would return 2018,
    and ringo-20190101 would return 2019
    """

    deployment_split = split_deployment(deployment_name)
    deployment_date = deployment_split[1]
    year = deployment_date[0:4]

    # if project == "FREEBYRD":
    #     month = deployment_date[4:6]
    #     if int(month) <= 7:
    #         year = f"{int(year)}"
    #     else:
    #         year = f"{int(year) + 1}"

    return year


# def _parse_deployment_info(delpoyment_info: dict):
#     """
#     """
#     deploymentyaml = deployment_info["deploymentyaml"]
#     mode = deployment_info["mode"]

#     return deploymentyaml, mode


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
        _log.warning(d[depth_diff_abs.values > depth_ok].describe())

    x = x.drop_vars(["latitude", "longitude", "depth"], errors="ignore")
    y = y.drop_vars(["latitude", "longitude", "depth"], errors="ignore")
    ds = xr.merge(
        [
            x.rename("depth_measured"),
            y.rename("depth_ctd"),
            x_interp.rename("depth_measured_interp"),
            depth_diff.rename("depth_diff"),
            depth_diff_abs.rename("depth_diff_abs"),            
        ],
        join="outer", 
    )

    _log.info("Completed depth checks (measured vs CTD)")
    return ds


def check_string_length(x: list) -> list:
    """
    Check that all strings in the given list are the same length

    Returns a list of the elements with different lengths than the mode.
    """
    diff_files = []
    x_nchar = [len(i) for i in x]
    x_set = set(x_nchar)
    if len(x_set) != 1:
        # What are the different string lengths, and how often do they occur
        _log.warning(
            "The given strings are not all the same length, "
            + "and thus shuld be checked carefully. "
            + "String length | number of strings with that length:"
        )
        big_count = 0
        x_counts = collections.Counter(x_nchar)
        for item, count in x_counts.items():
            if count > 20:
                big_count+=1
            _log.warning(f"{item:<5} | {count:<8}")

        # Get strings with different lengths
        nchar_mode = statistics.mode(x_nchar)
        diff_idx = [i for i, f in enumerate(x) if len(f) != nchar_mode]
        diff_files = [x[j] for j in diff_idx]
            
        # Outputs for the user
        if big_count > 1:
            _log.warning(
                "There are too many string to print all. "
                + "Printing the first 10 for each string length"
            )
            for j in x_set:
                _log.warning("Some file names of string length %s:", j)
                j_idx = [i for i, f in enumerate(x) if len(f) == j]
                j_files = [x[j] for j in j_idx[:10]]
                for f in j_files:
                    _log.warning("string: %s", f)
        else:
            # Print all the file names with different lengths
            _log.warning(
                "The following strings are of a different length: %s",
                ", ".join(diff_files),
            )
        
    return diff_files
    


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
    joined sunrise/sunset times are often not right for the given local day.
    Would like to submit this as a PR to GliderTools, but have
    not had the bandwidth.

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
        df["time_local"].dt.tz_localize(None).values.astype("datetime64[D]") # type: ignore
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


def get_instrument_sn_date(ds: xr.Dataset, instrument_str: str) -> tuple:
    """
    Get the sn and calibration date for the given instrument, 
    if it exists in the dataset attributes

    Parameters
    ----------
    ds: `xarray.Dataset`
        processed glider data; expected to be science timeseries
    instrument_str: str
        The instrument string to look for in the dataset attributes (e.g., "instrument_flbbcd")

    Returns
    -------
    tuple
        A tuple (sn, calibration date) for the FLBBCD instrument, if it exists in the dataset attributes; otherwise, (None, None)
    """
    
    _log.debug("Checking instrument sn and calibration date")
    if instrument_str in ds.attrs:
        instr_attrs = ast.literal_eval(ds.attrs[instrument_str])
        try:
            sn = instr_attrs["serial_number"]
            # cdate = datetime.fromisoformat(instr_attrs["calibration_date"])
            cdate = instr_attrs["calibration_date"]
            return sn, cdate
        except (KeyError, ValueError, SyntaxError):
            _log.warning(
                "Could not parse instrument attributes for %s. "
                + "Expected keys 'serial_number' and 'calibration_date'.",
                instrument_str,
            )
            return None, None
    else:
        return None, None


def check_cdom_date(ds: xr.Dataset) -> str:
    """
    Check the calibration date of the instrument_flbbcd and return a 
    CDOM status string (described in returns). 
    Status date ranges defined by Sea-Bird Scientific notices 
    'Out-of-tolerance UV LED' and 'Incorrect CDOM values'. 

    Parameters
    ----------
    ds: `xarray.Dataset`
        processed glider data; expected to have an 'instrument_flbbcd' attribute

    Returns
    -------
    str
        One of the following status strings:
        - "oot": out-of-tolerance (January 2021 to July 2023)
        - "raf": requires Reference Adjustment Factor (before January 2023)
        - "ok": calibration date is within acceptable range
        - "none": no instrument_flbbcd attribute found
    """
    
    _log.info("Determining CDOM status")
    _sn, cdate = get_instrument_sn_date(ds, "instrument_flbbcd")

    if cdate is None:
        _log.info("No instrument_flbbcd attribute found")
        return "none"

    else:
        cdate_dt = date.fromisoformat(cdate)
        _log.debug("instrument_flbbcd calibration date %s", 
                   cdate_dt.strftime("%Y-%m-%d"))

        if date(2021, 1, 1) <= cdate_dt <= date(2023, 7, 31):
            _log.warning("CDOM data are out-of-tolerance, and thus irretrievable")
            return "oot"
        elif cdate_dt < date(2023, 1, 13):
            _log.warning("CDOM data require a Reference Adjustment Factor (RAF)")
            return "raf"
        else:
            _log.info("CDOM data are ok")
            return "ok"


def correct_cdom(ds: xr.Dataset) -> xr.Dataset:
    """
    Correct cdom values, depending on status calculated by check_cdom_date. 

    Specifcally:
        1) Remove values collected with instrument serviced between 
            January 2021 and July 2023 (change to np.nan)
        2) Apply Reference Adjustment Factor (RAF) of 5.62 to the CDOM data, 
            if instrument serviced before January 2023

    If either case, update metadata: 
        - comment within instrument_flbbcd attribute
        - cdom comment attribute

    Parameters
    ----------
    ds: `xarray.Dataset`
        processed glider data; expected to be raw or science timeseries

    Returns
    -------
        input dataset ds, corrected as necessary
    """

    _log.info("Starting CDOM correction check")
    if "instrument_flbbcd" in ds.attrs:
        # Check status of CDOM data, based on flbbcd calibration date
        cdom_status = check_cdom_date(ds)
        _log.debug("CDOM status: %s", cdom_status)

        # Get instrument attributes
        instr_attrs = ast.literal_eval(ds.instrument_flbbcd)

        # Depending on CDOM status, remove or correct CDOM values as necessary
        if cdom_status == "oot":
            _log.info(
                "Based on instrument_flbbcd calibration date, "
                + "CDOM data is out-of-tolerance and irretrievable. Dropping"
            )
            cdom_oot_message = (
                "Per Sea-Bird Scientific notice 'Out-of-tolerance UV LED', " 
                + "these CDOM data were irretrievable, "
                + "and have been removed from the dataset"
            )

            # ds['cdom'].values[:] = np.nan
            # ds["cdom"].attrs["comment"] = append_string(
            #     ds["cdom"].attrs["comment"], cdom_oot_message)
            ds = ds.drop_vars("cdom")
            instr_attrs["comment"] = append_string(
                instr_attrs["comment"], cdom_oot_message)
            ds.attrs["instrument_flbbcd"] = str(instr_attrs)

        elif cdom_status == "raf":
                _log.info(
                    "Based on instrument_flbbcd calibration date, "
                    + "need to apply RAF to CDOM data. Applying"
                )          
                cdom_raf_message = (
                    "Per Sea-Bird Scientific notice 'Incorrect CDOM values', " 
                    + "applied Reference Adjustment Factor (RAF) of 5.62. " 
                    + "to the data: CDOM adjusted = 5.62 * CDOM"
                )
                
                ds['cdom'] = ds['cdom'] * 5.62
                ds["cdom"].attrs["comment"] = append_string(
                    ds["cdom"].attrs["comment"], cdom_raf_message)
                instr_attrs["comment"] = append_string(
                    instr_attrs["comment"], cdom_raf_message)
                ds.attrs["instrument_flbbcd"] = str(instr_attrs)        
        
        elif cdom_status == "ok":
            _log.info(
                "instrument_flbbcd calibration date is outside of "
                + "the Sea-Bird correction windows, "
                + "and thus no values need correction"
            )
        
        else:
            _log.warning(
                "CDOM status returned an unexpected value: %s", cdom_status
            )

        return ds
    
    else:
        _log.info("No instrument_flbbcd, and thus no CDOM correction needed")
        return ds


def calc_flbbcd(
    ds: xr.Dataset, 
    chlor_calib: tuple,
    cdom_calib: tuple,
    bb_calib: tuple, 
) -> xr.Dataset:
    """
    Calculate corrected FLBBCD output values 

    In some glider deployments, 
    the cwo (clean water offset) and sf (scaling factor) values
    for FLBBCD variables were not properly set in the slocum autoexec files. 
    Thus, the output values for 'chlorophyll', 'cdom', and 'backscatter_700'
    need to be recalculated using the raw signal and correct cwo/sf values. 

    All three values are calculated using the same formula:
    output value = sf * (signal value - cwo).

    ds: `xarray.Dataset`
        glider timeseries, likely raw timeseries. Must contain 
    {var}_calib: tuple: (cwo, sf)
        Tuple of variable calibration values: clean water offset, and scaling factor
        Same structure for each of chlorophyll, cdom, and backscatter.
        These values come from the calibration sheet

    Returns
    -------
    xarray.Dataset
        Dataset with corrected FLBBCD output values and comments
    """

    _log.info("Recalculating FLBBCD output values")
    msg = (
        " Recalculated using raw signal values, calibration values, "
        + "and esdglider.utils.calc_flbbcd"
    )

    if all(v in ds.data_vars for v in ["chlorophyll", "chlorophyll_signal"]):
        _log.debug("Recalculating chlorophyll")
        ds["chlorophyll"] = chlor_calib[1] * (ds["chlorophyll_signal"] - chlor_calib[0])
        ds["chlorophyll"].attrs["comment"] = append_string(
            ds["chlorophyll"].attrs["comment"], msg)
    else:
        _log.warning(
            "chlorophyll variables not present in dataset, and thus not recalculated"
        )

    if all(v in ds.data_vars for v in ["cdom", "cdom_signal"]):
        _log.debug("Recalculating cdom")
        ds["cdom"] = cdom_calib[1] * (ds["cdom_signal"] - cdom_calib[0])
        ds["cdom"].attrs["comment"] = append_string(
            ds["cdom"].attrs["comment"], msg)
    else:
        _log.warning(
            "cdom variables not present in dataset, and thus not recalculated"
        )

    if all(v in ds.data_vars for v in ["backscatter_700", "backscatter_700_signal"]):
        _log.debug("Recalculating backscatter_700")
        ds["backscatter_700"] = bb_calib[1] * (ds["backscatter_700_signal"] - bb_calib[0])
        ds["backscatter_700"].attrs["comment"] = append_string(
            ds["backscatter_700"].attrs["comment"], msg)
    else:
        _log.warning(
            "backscatter_700 variables not present in dataset, and thus not recalculated"
        )

    _log.info("Finished recalculating FLBBCD output values")

    return ds


def append_string(text, msg):
    """
    Append a message to a string, with a space in between if the string is not empty

    Parameters
    ----------
    text: str
        Original string to which the message will be appended.
    msg: str
        Message to append to the original string.

    Returns
    -------
    str
        The original string with the message appended, separated by a 
        semicolon and a space if the original string is not empty.
    """
    if not text.strip():
        return msg
    else:
        return text + "; " + msg


def check_dbdreader_c_extension():
    """
    Check the status of the DBDREADER_C_EXTENSION environment variable and print a log message.

    This function prints a message indicating whether dbdreader is using the Pure Python backend,
    the C-extension backend, or the package defaults based on the value of the DBDREADER_C_EXTENSION
    environment variable.
    """
    _log.debug("Getting the status of DBDREADER_C_EXTENSION environment variable")
    c_ext_status = os.environ.get("DBDREADER_C_EXTENSION")

    if c_ext_status == "0":
        _log.info("DBDREADER_C_EXTENSION=0, and thus, dbdreader using Python backend")
    elif c_ext_status == "1":
        _log.info("DBDREADER_C_EXTENSION=1, and thus, dbdreader using C-extension backend")
    elif c_ext_status is None:
        _log.info("DBDREADER_C_EXTENSION is not set. Using package defaults")
    else:
        _log.warning("DBDREADER_C_EXTENSION is set to an unexpected value: %s", c_ext_status)