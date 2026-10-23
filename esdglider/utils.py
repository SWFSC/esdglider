import ast
import collections
import logging
import os
import shutil
import statistics
from datetime import UTC, date, datetime
from pathlib import Path

import gsw
import numpy as np
import pandas as pd
import xarray as xr
import yaml
from dateutil.parser import isoparse

_log = logging.getLogger(__name__)


"""
Utilities, mostly specific to ESD needs and ways of processing
"""

# Logistical utilities ########################################################
# def _get_deployment_netcdfvars(deploymentyaml):
#     """
#     Loop through deploymentyaml files, and concatenate all netcdf vars
#     from the various deployment YAML files.
#     Allows for yaml files with only netcdf variables, 
#     for raw and engineering datasets.

#     Parameters
#     ----------
#     deploymentyaml : str or list of str
#         Path(s) to the deployment YAML file(s).

#     Returns
#     -------
#     dict
#         A dictionary containing the concatenated NetCDF variables from the deployment YAML files.
#     """
#     ncvar = {}
#     if isinstance(deploymentyaml, str):
#         deploymentyaml = [deploymentyaml]
#     for nn, d in enumerate(deploymentyaml):
#         with open(d) as fin:
#             deployment_ = yaml.safe_load(fin)
#             if "netcdf_variables" in deployment_:
#                 for key, value in deployment_["netcdf_variables"].items():
#                     if key not in ncvar:
#                         ncvar[key] = value

#     return ncvar


def get_var_by_source(ds, sensor_name):
    """
    Returns the variable name in `ds` where attrs['source'] matches `sensor_name`.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to search.
    sensor_name : str
        The value to match against the 'source' attribute of the variables.

    Returns
    -------
    str
        The name of the variable whose 'source' attribute matches `sensor_name`.

    Raises
    ------
    KeyError
        If no variable with the specified 'source' attribute is found.
    """
    for var_name, da in ds.data_vars.items():
        if da.attrs.get('source') == sensor_name:
            return var_name
    raise KeyError(f"No variable found with source attribute '{sensor_name}'")


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
    min_dt_dt = parse_iso8601(min_dt)

    # For out of range or nan time/lat/lon, drop rows
    num_orig = len(ds.time)
    num_orig_nan = np.count_nonzero(np.isnan(ds.time.values))
    ds = ds.where(ds.time >= np.datetime64(min_dt_dt), drop=True)
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
    Remove and/or drop bogus times and values. Specifically:
        - dropping bogus times, meaning times:
            - before the deployment start, if specified via min_dt. 
              If not specified, then times before 1970-01-01
            - after the current time (if max_drop is True)
        - dropping rows with bogus lat/lon values (out of range or nan)
        - changing nonsensical science variable values to np.nan

    ds: `xarray.Dataset`
        processed glider data
    min_dt: str; default="1970-01-01"
        String representing the minimum datetime to keep.
        Must be in ISO 8601 format, e.g., 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS'.
    max_drop: bool; default=False
        If True, drop times that are after the current UTC time.

    Returns
    -------
    xarray Dataset
        Dataset with bogus rows rows dropped, and bogus values changed to nan
    """

    # Drop bogus times, as specified
    ds = drop_bogus_times(ds, min_dt=min_dt, max_drop=max_drop)

    _log.info("Dropping bogus values")
    
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
        _log.warning(
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
                "Var '%s' not present in ds - skipping drop_values check",
                var,
            )
            continue
        num_orig = len(ds[var])
        good = (ds[var] >= value[0]) & (ds[var] <= value[1])
        ds[var] = ds[var].where(good, drop=False)
        if num_orig - len(ds[var]) > 0:
            _log.warning(
                "Changed %d '%s' values outside range [%d, %d] to nan",
                num_orig - len(ds[var]),
                var,
                value[0],
                value[1],
            )

    return ds


# def get_file_id_esd(ds) -> str:
#     """
#     ESD's version of pyglider.utils.get_file_id
#     This version does not require the glider_serial
#     Make a file id for a Dataset: Id = *glider_name* + "YYYYMMDDTHHMM"
#     """

#     _log.debug(ds.time)
#     if ds.time.dtype != "datetime64[ns]":
#         dt = ds.time.values[0].astype("timedelta64[s]") + np.datetime64("1970-01-01")
#     else:
#         dt = ds.time.values[0].astype("datetime64[s]")
#     _log.debug("dt %s", dt)
#     id = (
#         ds.attrs["glider_name"]
#         + "-"
#         + dt.item().strftime("%Y%m%dT%H%M")
#     )
#     return id


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
    if not all(i in cols_orig for i in new_start):
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
    if not all(i in ds_vars_orig for i in new_start):
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
    return datetime.now(UTC).strftime(format)


def parse_iso8601(dstring: str | datetime) -> datetime | None:
    """
    Parse a string as an ISO 8601 date.
    # Examples:
    dt1 = parse_iso8601("20210616T143025")      # Returns datetime.datetime(2021, 6, 16, 14, 30, 25)
    dt2 = parse_iso8601("2021-06-16T14:30:25")  # Returns datetime.datetime(2021, 6, 16, 14, 30, 25)
    dt3 = parse_iso8601("June 16, 2021")        # Returns None

    Parameters
    ----------
    dstring : str | datetime
        The date string object to parse. 
        If already a datetime, object is simply returned

    Returns
    -------
    datetime | None
        A datetime object if parsing is successful, None otherwise.
    """
    if isinstance(dstring, datetime):
        _log.debug("Input is already a datetime object: %s", dstring)
        return dstring

    try:
        return isoparse(dstring)
    except (ValueError, TypeError):
        return None


def sort_attrs(ds):
    """
    Sort the attributes of a dataset in alphabetical order.

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset whose attributes are to be sorted.

    Returns
    -------
    xarray.Dataset
        The dataset with sorted attributes.
    """
    ds.attrs = collections.OrderedDict(sorted(ds.attrs.items()))
    return ds


# def is_iso8601(dstring: str) -> bool:
#     """
#     Check if a string is in ISO 8601 date format.
#     # print(is_iso8601("20210616T143025"))      # True (Basic format)
#     # print(is_iso8601("2021-06-16T14:30:25"))  # True (Extended format)
#     # print(is_iso8601("June 16, 2021"))        # False

#     Parameters
#     ----------
#     dstring : str
#         The date string to check.

#     Returns
#     -------
#     bool
#         True if the string is in ISO 8601 format, False otherwise.
#     """
#     try:
#         isoparse(dstring)
#         return True
#     except (ValueError, TypeError):
#         return False


def _split_deployment(deployment_name):
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


def get_glider_name(deployment_name):
    """
    Get the glider name from the deployment name.
    Assumes the deployment name is in the format 'glidername-YYYYMMDD'.
    Returns the glider name as a string.
    """
    deployment_split = _split_deployment(deployment_name)
    glider_name = deployment_split[0]
    return glider_name


def get_path_year(deployment_name):
    """
    From the deployment name (both strings),
    generate and return the year string to use in file paths
    for ESD glider deployments

    Given Prod directory structure changes, for all deployments now
    the value returned is simply the year.
    For example, ringo-20181231 would return 2018,
    and ringo-20190101 would return 2019
    """

    deployment_split = _split_deployment(deployment_name)
    deployment_date = deployment_split[1]
    year = deployment_date[0:4]

    # if project == "FREEBYRD":
    #     month = deployment_date[4:6]
    #     if int(month) <= 7:
    #         year = f"{int(year)}"
    #     else:
    #         year = f"{int(year) + 1}"

    return year

def get_deployment_date(deployment_name: str):
    """
    From the deployment name (both strings),
    generate and return the deployment date as a datetime object
    for ESD glider deployments

    For example, ringo-20181231 wopould return 31 Dec 2018 as a datetime object
    """

    deployment_split = _split_deployment(deployment_name)
    deployment_date = deployment_split[1]


    return  datetime.strptime(deployment_date, "%Y%m%d").date()


def get_deployment_name(ds: xr.Dataset) -> str:
    """
    Extract and validate the deployment name from an xarray Dataset.

    Parameters
    ----------
    ds : xarray.Dataset
        Glider dataset containing the global 'deployment_name' attribute.

    Returns
    -------
    str
        The validated deployment name (e.g., 'amlr08-20220513').

    Raises
    ------
    KeyError
        If 'deployment_name' is missing from ds.attrs.
    ValueError
        If 'deployment_name' is empty or not a string.
    """
    if "deployment_name" not in ds.attrs:
        _log.error("Dataset is missing required global attribute 'deployment_name'")
        raise KeyError(
            "Required global attribute 'deployment_name' not found in dataset. "
            "Ensure deployment metadata has been assigned before calling this function."
        )

    deployment_name = ds.attrs["deployment_name"]

    if not isinstance(deployment_name, str) or not deployment_name.strip():
        _log.error("Invalid 'deployment_name' attribute value: %r", deployment_name)
        raise ValueError(
            f"Global attribute 'deployment_name' must be a non-empty string, got {deployment_name!r}"
        )

    return deployment_name.strip()


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
    _log.debug("Trying to make directory %s", dir)
    try:
        os.mkdir(dir)
    except FileExistsError:
        pass


def makedirs_pass(dir):
    """
    Convenience wrapper to try to make a directory path,
    and pass if it already exists
    """
    _log.debug("Trying to make directory %s", dir)
    if not os.path.exists(dir):
        os.makedirs(dir)


def rmtree(dir, ignore_errors=False):
    """
    Light wrapper around shutil.rmtree
    Checks if directory exists before deleting
    """
    if os.path.isdir(dir):
        _log.info(
            "Removing the following directory and all files in it: %s", 
            dir,
        )
        shutil.rmtree(dir, ignore_errors=ignore_errors)
    else:
        _log.debug("No directory to remove at: %s", dir)


def remove_file(file_path):
    """
    Light wrappoer to check if a file exists at file_path,
    and to remove it if so
    """
    if os.path.exists(file_path):
        _log.info("Removing file: %s", file_path)
        os.remove(file_path)
    else:
        _log.debug("No file to remove at: %s", file_path)


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


def count_binary_files(dir: str | Path, log_pre: str = ""):
    """
    Count the number of total, dcd, ecd, dbd, and ebd files 
    in the given directory.

    Parameters
    ----------
    dir (str | Path): The path to the (binary) directory.

    log_pre (str, optional): A prefix for log messages. Defaults to "".

    Returns
    -------
    tuple: A tuple containing the counts of: 
    total files, dcd files, ecd files, dbd files, and ebd files.
    """
    dir = Path(dir)
    total_files = len(os.listdir(dir))
    _log.info("%s: There are %d total files in %s", log_pre, total_files, dir)

    dcd_files = len(list(dir.glob("*.dcd")))
    ecd_files = len(list(dir.glob("*.ecd")))
    _log.info(
        "%s: There are %d/%d dcd/ecd files",
        log_pre,
        dcd_files, 
        ecd_files
    )

    dbd_files = len(list(dir.glob("*.dbd")))
    ebd_files = len(list(dir.glob("*.ebd")))
    _log.info(
        "%sThere are %d/%d dbd/ebd files",
        log_pre,
        dbd_files, 
        ebd_files
    )

    return total_files, dcd_files, ecd_files, dbd_files, ebd_files


def calc_ts(
    ds: xr.Dataset,         
    salinity_var: str = "salinity",
    temp_var: str = "potential_temperature",
) -> tuple:
    """
    Calculate variables for temperature/salinity plots
    Code adapted from Jacob Partida
    
    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider science dataset.
    var : str
        The name of the variable to plot
    salinity_var : str
        The name of the salinity variable in the dataset
    temp_var : str
        The name of the temperature variable in the dataset

    Returns
    -------
    tuple: A tuple containing the gridded salinity (Sg), 
    temperature (Tg), and density (sigma) arrays.
    """
    s_lims = (
        np.floor(np.min(ds[salinity_var]) - 0.5),
        np.ceil(np.max(ds[salinity_var]) + 0.5),
    )

    t_lims = (
        np.floor(np.min(ds[temp_var]) - 0.5),
        np.ceil(np.max(ds[temp_var]) + 0.5),
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


def get_date_start_end(ds: xr.Dataset) -> tuple[str, str]:
    """
    Get the start and end dates of a deployment from an xarray dataset.

    Parameters
    ----------
    ds : xarray dataset
        The dataset, required to have 'time' values.
        The time values are expected to be sorted.

    Returns
    -------
    tuple of str
        The start (first) and end (last) dates in 'YYYY-MM-DD' format.
    """
    dt = ds.time.values
    start = np.datetime_as_string(dt[0], unit='D')
    end = np.datetime_as_string(dt[-1], unit='D')

    return start, end


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
        var_cdom = get_var_by_source(ds, "sci_flbbcd_cdom_units")

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
            ds = ds.drop_vars([var_cdom])
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
                ds[var_cdom] = ds[var_cdom] * 5.62
                ds[var_cdom].attrs["comment"] = append_string(
                    ds[var_cdom].attrs["comment"], cdom_raf_message)
                instr_attrs["comment"] = append_string(
                    instr_attrs["comment"], cdom_raf_message)
                ds.attrs["instrument_flbbcd"] = str(instr_attrs)        
        
        elif cdom_status == "ok":
            _log.info(
                "instrument_flbbcd calibration date not within "
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

    # Set/pull values based on sensor attribtue
    var_chlor_units = get_var_by_source(ds, "sci_flbbcd_chlor_units")
    var_chlor_sig = get_var_by_source(ds, "sci_flbbcd_chlor_sig")
    var_cdom_units = get_var_by_source(ds, "sci_flbbcd_cdom_units")
    var_cdom_sig = get_var_by_source(ds, "sci_flbbcd_cdom_sig")
    var_bb_units = get_var_by_source(ds, "sci_flbbcd_bb_units")
    var_bb_sig = get_var_by_source(ds, "sci_flbbcd_bb_sig")

    def _calc_flbbcd_units(sig, calib):
        return calib[1] * (sig - calib[0])

    if all(v in ds.data_vars for v in [var_chlor_units, var_chlor_sig]):
        _log.debug("Recalculating chlorophyll")
        ds[var_chlor_units].values = _calc_flbbcd_units(ds[var_chlor_sig].values, chlor_calib)
        ds[var_chlor_units].attrs["comment"] = append_string(
            ds[var_chlor_units].attrs["comment"], msg)
    else:
        _log.warning(
            "chlorophyll variables not present in dataset; not recalculated"
        )

    if all(v in ds.data_vars for v in [var_cdom_units, var_cdom_sig]):
        _log.debug("Recalculating cdom")
        ds[var_cdom_units].values = _calc_flbbcd_units(ds[var_cdom_sig].values, cdom_calib)
        ds[var_cdom_units].attrs["comment"] = append_string(
            ds[var_cdom_units].attrs["comment"], msg)
    else:
        _log.warning("cdom variables not present in dataset; not recalculated")

    if all(v in ds.data_vars for v in [var_bb_units, var_bb_sig]):
        _log.debug("Recalculating backscatter_700")
        ds[var_bb_units].values = _calc_flbbcd_units(ds[var_bb_sig].values, bb_calib)
        ds[var_bb_units].attrs["comment"] = append_string(
            ds[var_bb_units].attrs["comment"], msg)
    else:
        _log.warning(
            "backscatter_700 variables not present in dataset; not recalculated"
        )

    _log.info("Finished recalculating FLBBCD output values")

    return ds


def check_par(ds: xr.Dataset, var: str = "par"):
    """
    Check the PAR (Photosynthetically Active Radiation) values in the dataset.
    Checks:
        - Are any values less than 0. 
    Parameters
    ----------
    ds : xarray.Dataset
        The Dataset containing the PAR variable.
    var : str, optional
        The name of the PAR variable in the dataset. Default is "par".

    Returns
    -------
    None
    """
    if var not in ds.data_vars:
        _log.debug("PAR variable '%s' not present in dataset", var)
        return
    else:
        da = ds[var]
        
        par_nonnan = da.count()
        _log.debug(
            "In the science timeseries, PAR has %d non-NaN values", 
            par_nonnan.item()
        )

        par_neg = (da < 0).sum()
        if par_neg > 0:
            _log.warning(
                "In the science timeseries, PAR has %d negative values "
                "out of %d non-NaN values (%.2f%%)",
                par_neg.item(),
                par_nonnan.item(),
                (par_neg.item() / par_nonnan.item() * 100),
            )


# def correct_par(ds: xr.Dataset, var: str = "par"):
#     """
#     Correct the PAR (Photosynthetically Active Radiation) values in the dataset.
#     Specifically, set any values between 0 and -1 to 0. 
#     All other values, including nans, are left as-is.

#     Parameters
#     ----------
#     ds : xarray.Dataset
#         The Dataset containing the PAR variable.
#     var : str, optional
#         The name of the PAR variable in the dataset. Default is "par".

#     Returns
#     -------
#     xarray.Dataset
#         The corrected Dataset with values between 0 and -1 (inclusive) set to 0 for var.
#     """
#     da = ds[var]
#     n = ((da >= -1) & (da < 0)).sum()

#     ds[var] = xr.where((da >= -1) & (da < 0), 0, da, keep_attrs="no_conflicts")
#     ds[var].attrs["comment"] = append_string(
#         ds[var].attrs.get("comment", ""), 
#         f"Changed {n.item()} PAR values between 0 and -1 (inclusive) to 0"
#     )

#     return ds


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
        
        
def combine_datasets(nc_dir):
    """
    Combine profile NetCDF files into a single xarray
    Dataset.

    Parameters
    ----------
    nc_dir : str or pathlib.Path
        Directory containing NetCDF files.

    Returns
    -------
    xarray.Dataset
        Dataset containing all profiles concatenated
        along the ``time`` dimension.
    """

    # CONVERT INPUT TO A PATH OBJECT
    nc_dir = Path(nc_dir)

    # FIND ALL NETCDF FILES
    nc_files = sorted(nc_dir.glob("*.nc"))

    if not nc_files:
        raise FileNotFoundError(
            f"No NetCDF files found in {nc_dir}"
        )

    deployment_datasets = []

    # PROCESS EACH PROFILE FILE
    for nc_file in nc_files:
        ds = xr.load_dataset(nc_file)

        # STORE THE PROFILE FILENAME FOR EACH OBSERVATION
        profile_name = nc_file.stem.replace("_qc", "")
        ds = ds.assign(
            profile=xr.DataArray(
                np.full(ds.sizes["time"], profile_name),
                dims=("time",),
            )
        )
        deployment_datasets.append(ds)

    # COMBINE ALL PROFILES
    deployment_ds = xr.concat(
        deployment_datasets,
        dim="time",
        combine_attrs="override",
    )

    return deployment_ds


def get_unique_file_sizes(directory_path: str) -> set[int]:
    """
    Recursively scans a directory and returns a set of all unique file sizes in bytes.

    Parameters
    ----------
    directory_path : str
        Path to the directory to scan for file sizes.

    Returns
    -------
    set[int]
        Set of unique file sizes in bytes.
    """
    file_sizes = set()
    
    for root, _, files in os.walk(directory_path):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            try:
                # Get the file size in bytes and add to the set
                size = os.path.getsize(file_path)
                file_sizes.add(size)
            except (OSError, FileNotFoundError):
                # Handles broken symlinks or permission errors smoothly
                continue
                
    return file_sizes