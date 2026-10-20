# import datetime
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

from esdglider import utils

# from glidertools.optics import sunset_sunrise
# from timezonefinder import TimezoneFinder


_log = logging.getLogger(__name__)


# def solocam_filename_dt(filename, dt_idx_start, format="%Y%m%d-%H%M%S"):
#     """
#     Parse solocam (imagery) filename to return associated datetime
#     Requires index of start of datetime part of string

#     Parameters
#     ----------
#     filename : str
#         Full filename
#     dt_idx_start : int
#         The index of the start of the datetime string.
#         The datetime includes this index, plus the next 15 characters
#         Specifically: filename[dt_idx_start : (dt_idx_start + 15)]
#     format : str
#         format passed to datetime.strptime

#     Returns
#     -------
#         The datetime extracted from the imagery filename,
#         returned as a 'datetime64[s]' object
#     """

#     solocam_substr = filename[dt_idx_start : (dt_idx_start + 15)]
#     _log.debug(f"datetime substring: {solocam_substr}")
#     solocam_dt = datetime.strptime(solocam_substr, format)
#     solocam_dt64s = np.datetime64(solocam_dt).astype("datetime64[s]")

#     return solocam_dt64s


def get_solocam_dt(path, format="%Y:%m:%d %H:%M:%S")->pd.DataFrame:
    """
    Parse solocam (imagery) filename to return associated datetime
    Requires index of start of datetime part of string

    Parameters
    ----------
    path : str
        Full filename of the image metadata file. This file will be read via:
        `pd.read_json(path, lines=True)`
    format : str
        format passed to datetime.strptime

    Returns
    -------
        A pandas dataframe with 3 columns:
        - img_file: the image file name, dtype str
        - img_dir: the image directory name, dtype str
        - time: the image datetime, dtype datetime64[s]
    """

    _log.info("Reading image metadata file from %s", path)
    img_meta_df = pd.read_json(path, lines=True)

    # Verbosely filter out rows with an error message
    if "error" in img_meta_df.columns:
        img_meta_df_e = img_meta_df[img_meta_df["error"].notna()]
        _log.warning(
            "Found %d rows with errors in the image metadata file",
            img_meta_df_e.shape[0],
        )
        _log.warning(
            "Image names with errors:\n%s", 
            "\n    ".join(img_meta_df_e["n"].values)
        )

        img_meta_df = img_meta_df[~img_meta_df["error"].notna()]
    else:
        _log.debug("No error column found in image metadata file")

    # Check that all image names have the same string length
    imagery_files = list(img_meta_df["n"].values)
    utils.check_string_length(imagery_files)

    # Create output dataframe with image file name, directory, and datetime
    df_columns = {
        "n": "img_file", 
        "p": "img_dir", 
        "dt": "time_str", 
    }
    df = (
        img_meta_df
        .rename(columns=df_columns)
        .sort_values(by="img_file", ignore_index=True)
    )
    df["time_dt"] = df['time_str'].apply(lambda x: datetime.strptime(x, format))
    df["time"] = df["time_dt"].astype("datetime64[s]")

    return df[["img_file", "img_dir", "time"]]


def imagery_timeseries(ds, img_paths):
    """
    Matches up imagery files with data from pyglider by imagery filename
    Uses interpolated variables (hardcoded in function)
    Returns data frame with imagery+timeseries information

    Parameters
    ----------
    ds : xarray Dataset
        science timeseries NetCDF
    img_paths : dict
        dictionary of image paths, from paths.get_path_imagery

    Returns
    -------
        DataFrame: pd.DataFrame of imagery timeseries
    """

    deployment_name = ds.attrs["deployment_name"]
    # imagedir = img_paths["imagedir"]
    metadir = img_paths["metadir"]
    ancdir = img_paths["ancdir"]
    
    _log.info("Creating imagery ancillary data file for %s", deployment_name)
    # _log.info(f"Using images directory {imagedir}")
    _log.info("Using image metadata directory %s", metadir)

    csv_file =  img_paths["imgcsv"]
    if os.path.isfile(csv_file):
        _log.info("Deleting old imagery ancillary data file: %s", csv_file)
        os.remove(csv_file)


    # --------------------------------------------
    # Extract info from imagery file names
    _log.debug("Processing imagery file names")
    if not os.path.isfile(img_paths["imgmetapath"]):
        _log.error("Image metadata file not found: %s", img_paths['imgmetapath'])
        _log.error("Exiting function")
        return
    
    df = get_solocam_dt(img_paths["imgmetapath"])


    # # imagery_files = 
    # _log.debug("Processing imagery file names")

    # # Check that all filenames have the same number of characters
    # utils.check_string_length(imagery_files)
    # # imagery_files_nchar = [len(i) for i in imagery_files]
    # # if not len(set(imagery_files_nchar)) == 1:
    # #     _log.warning(
    # #         "The imagery file names are not all the same length, "
    # #         + "and thus shuld be checked carefully",
    # #     )
    # #     nchar_mode = statistics.mode(imagery_files_nchar)
    # #     diff_idx = [i for i, f in enumerate(imagery_files) if len(f) != nchar_mode]
    # #     diff_files = [f"{imagery_dirs[i]}/{imagery_files[i]}" for i in diff_idx]
    # #     _log.warning(
    # #         "The following filenames are of a different length: %s",
    # #         ", ".join(diff_files),
    # #     )

    # if dt_idx_start is None:
    #     _log.info("Calculating the datetime index, ...")
    #     # i0_split = imagery_files[0].split("-")
    #     space_idx = str.index(imagery_files[0], " ")
    #     if space_idx == -1:
    #         _log.error(
    #             "The imagery file name year index could not be found, "
    #             + "and thus the imagery ancillary data file cannot be generated",
    #         )
    #         raise ValueError("Incompatible file name spaces")
    #     dt_idx_start = space_idx + 1
    # _log.debug("dt_idx_start %s", dt_idx_start)

    # imagery_files_dt = np.array(
    #     [solocam_filename_dt(i, dt_idx_start) for i in imagery_files],
    # )

    # df = pd.DataFrame(
    #     data={
    #         "img_file": imagery_files,
    #         "img_dir": imagery_dirs,
    #         "time": imagery_files_dt,
    #     },
    # ).sort_values(by="img_file", ignore_index=True)

    # --------------------------------------------
    # Create ancillary data file
    _log.info("Interpolating glider data to image timestamps")
    vars_toignore = [
        "profile_index",
        "profile_direction",
        "trajectory", 
        "distance_over_ground",
        "waypoint_latitude",
        "waypoint_longitude",
        "water_velocity_eastward",
        "water_velocity_northward",
    ]
    vars_list = [var for var in ds.data_vars if var not in vars_toignore]
    qc_vars = [var for var in vars_list if var.endswith("_qc")]
    linear_vars = [var for var in vars_list if not var.endswith("_qc")]

    ds_prof = ds[["profile_index", "profile_direction"]]
    # ds_interp = ds.interp(time=df.time.values)

    # Must filter df.time for times >= start of ds_prof.time
    img_times = df.time[df.time >= min(ds_prof.time.values)].values
    ds_sel = ds_prof.reindex(time=img_times, method="pad")
    df = df.join(ds_sel.to_pandas(), on="time", how="left")
    target_times = df.time.values

    # Perform linear interpolation for continuous variables
    # This natively returns NaN for any point surrounded by or adjacent to NaN
    # This is what we want, because the timeseries has had max_gap applied
    _log.info("Interpolating continuous environmental data (linear)")
    ds_linear = ds[linear_vars].interp(
        time=target_times,
        method="linear",
        kwargs={"fill_value": np.nan},
    )
    for var in linear_vars:
        df[var] = ds_linear[var].values

    # Perform nearest-neighbor interpolation for QC flags
    if qc_vars:
        _log.info("Interpolating QC flags (nearest-neighbor)")
        ds_qc = ds[qc_vars].interp(
            time=target_times,
            method="nearest",
            kwargs={"fill_value": np.nan},
        )
        for var in qc_vars:
            df[var] = ds_qc[var].values
    # _log.info("Interpolating QC flags (nearest-neighbor)")
    # ds_nearest = ds.interp(
    #     time=df.time.values,
    #     method="nearest",
    #     kwargs={"fill_value": np.nan},
    # )

    # # Add variables to output data frame
    # for var in vars_list:
    #     _log.debug(f"Interpolating var {var}")
    #     if var not in list(ds_linear.keys()):
    #         _log.debug(f"{var} not present in ds - skipping interp")
    #         continue
    #     if var.endswith("_qc"):
    #         df[var] = ds_nearest[var].values
    #     else:
    #         df[var] = ds_linear[var].values


    _log.info("Determining mask for invalid values")
    time_mask = (
        ~np.isnan(ds_linear["time"])
        & ~np.isnan(ds_linear["latitude"])
        & ~np.isnan(ds_linear["longitude"])
    )
    ds_linear_ll = ds_linear.where(time_mask, drop=True)

    # Calculate sunrise and sunset
    su, sd, tl = get_sunrise_sunset(
        time=ds_linear_ll["time"].values,
        lat=ds_linear_ll["latitude"].values,
        lon=ds_linear_ll["longitude"].values,
    )

    su_full = np.full(ds_linear.time.shape[0], np.nan, dtype="datetime64[us]")
    sd_full = np.full(ds_linear.time.shape[0], np.nan, dtype="datetime64[us]")
    tl_full = np.full(ds_linear.time.shape[0], np.nan, dtype="datetime64[us]")

    su_full[time_mask] = su
    sd_full[time_mask] = sd
    tl_full[time_mask] = tl

    df["sunrise_local"] = su_full
    df["sunset_local"] = sd_full
    df["time_local"] = tl_full

    # --------------------------------------------
    # Export ancillary data file
    utils.mkdir_pass(ancdir)
    _log.info(f"Writing imagery ancillary data to: {csv_file}")
    df.to_csv(csv_file, index=False)

    return df


def extract_deployment_metadata(image_path: str, deployment_name: str):
    """
    Extracts deployment-wide tags, i.e. metadata, from a single image.

    This function was written by Gemini, and adapted by Sam Woodman

    Parameters
    ----------
    image_path : str
        The path to the imagery file from which to get 'global', 
        i.e., deployment-level, metadata
    deployment_name : str
        The name of the deployment

    Returns
    -------
    A dictionary with the deployment-level metadata. These data include
    the EXIF metadata tags from WASSOC, as well as the following:
        - Deployment name
        - image height, in pixels
        - image width, in pixels
        - image format (e.g., JPEG, PNG)
        - image mode (e.g., RGB, or L (grayscale))
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for image metadata extraction. "
            "Install it with `pip install pillow`."
        ) from exc

    _log.info("Extracting deployment-level metadata from %s", image_path)
    try:
        with Image.open(image_path) as img:            
            global_metadata = {
                "Deployment": deployment_name,
                "Height": img.height,
                "Width": img.width,
                "Format": img.format,
                "Mode": img.mode,
            }

            exifdata = img.getexif()
            for tagid in exifdata:        
                # Get the tag name
                tagname = TAGS.get(tagid, tagid)

                # Get the value, and format it
                value = exifdata.get(tagid)
                _log.debug(f"{tagname}: {value}")
                if isinstance(value, bytes):
                    value = value.decode(errors='ignore').strip('\x00')
                elif hasattr(value, 'numerator'):
                    value = float(value) # type: ignore

                global_metadata[str(tagname)] = value

    except Exception as e: # noqa: BLE001
        _log.error("Failed to extract global metadata from %s: %s", image_path, e )
        global_metadata = {"file": image_path, "error": str(e)}    

    return global_metadata


def extract_image_metadata(image_path):
    """
    Worker function for per-image high-frequency data.
    
    This function was written by Gemini, and adapted by Sam Woodman

    Parameters
    ----------
    image_path : str
        The path to the imagery file from which to extract metadata

    Returns
    -------
    A dictionary with the following image-level key-value pairs:
        - f: file name
        - p: directory name
        - dt: Datetime string, from the tag ID 306 ('Datetime')
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for image metadata extraction. "
            "Install it with `pip install pillow`."
        ) from exc
    
    try:
        with Image.open(image_path) as img:
            exifdata = img.getexif()
            
            # Extract just the datetime
            # Tag 36867 is DateTimeOriginal
            dt = exifdata.get(36867) or exifdata.get(306) # Fallback to DateTime
            
            if hasattr(dt, 'decode'): 
                dt = dt.decode() # type: ignore
            dt_str = str(dt).strip('\x00') if dt else "UNKNOWN"

            return {
                "n": image_path.name,            # Filename
                "p": image_path.parent.name,     # Immediate parent directory
                "dt": dt_str                     # DateTime
            }
        
    except Exception:  # noqa: BLE001
        return {"n": image_path.name, "error": "failed"}


def generate_osi_manifest(
    jsonl_filepath: str | Path,
    output_filepath: str | Path,
    target_dirs: set[str] | list[str],
    base_uri: str,
    log_interval: int = 10000
) -> None:
    """
    Streams a JSONL file line-by-line, 
    filters by directory, and writes a streaming
    JSON manifest format without holding the full dataset in memory.

    This function generates an image manifest compatible with the
    Optics SI (OSI) DAG Pipelines. 

    Parameters
    ----------
    jsonl_filepath : str | Path
        Path to the input JSONL file containing image metadata.
    output_filepath : str | Path
        Path to the output JSON manifest file.
    target_dirs : set[str] | list[str]
        Directories to include in the manifest.
    base_uri : str
        Base URI for constructing image URLs.
    log_interval : int, optional
        Interval for logging progress, by default 10000.

    Returns
    -------
    None
    """
    _log.info(f"Starting processing for file: '{jsonl_filepath}'")

    jsonl_filepath = str(jsonl_filepath)
    output_filepath = str(output_filepath)

    # yr = utils.get_path_year(deployment_name)
    # base_uri = f"gs://{bucket_name}/{yr}/{deployment_name}/images"
    target_dirs_set = set(target_dirs)
    _log.info(f"Target directories to filter: {target_dirs_set}")

    total_lines = 0
    matched_count = 0
    malformed_count = 0

    try:
        with open(jsonl_filepath, "r", encoding="utf-8") as infile, \
             open(output_filepath, "w", encoding="utf-8") as outfile:
            
            outfile.write('{"instances": [{"input_images": [')
            
            is_first_match = True
            
            for line in infile:
                total_lines += 1
                
                # Log progress periodically for large files
                if total_lines % log_interval == 0:
                    _log.info(f"Processed {total_lines:,} lines... ({matched_count:,} matches found so far)")
                
                line = line.strip()
                if not line or not line.startswith('{'):
                    continue
                
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    malformed_count += 1
                    _log.warning(f"Line {total_lines:,}: Malformed JSON syntax. Skipping.")
                    continue
                
                # Check target filter
                if data.get("p") in target_dirs_set:
                    file_name = data.get("n")
                    dir_name = data.get("p")
                    
                    if not file_name:
                        _log.warning(f"Line {total_lines:,}: Missing filename key 'n'. Skipping.")
                        continue

                    uri = f"{base_uri}/{dir_name}/{file_name}"
                    
                    if not is_first_match:
                        outfile.write(",\n")
                    else:
                        is_first_match = False
                    
                    outfile.write(f'"{uri}"')
                    matched_count += 1

            outfile.write(']}]}')

        _log.info("Processing complete.")
        _log.info(f"Total lines read: {total_lines:,}")
        _log.info(f"Total matched records written to '{output_filepath}': {matched_count:,}")
        if malformed_count > 0:
            _log.warning(f"Encountered {malformed_count:,} malformed or skipped lines.")

    except FileNotFoundError:
        _log.error(f"Input file not found at: '{jsonl_filepath}'")
        raise
    except Exception as e:
        _log.error(f"An unexpected error occurred on line {total_lines:,}: {e!s}")
        raise

def get_unique_directories(jsonl_path: str) -> list[str]:
    """
    Extracts unique directory names ('p' key) from a JSONL file.

    Parameters
    ----------
        jsonl_path (str): Path to the input JSONL file.

    Returns
    -------
        list[str]: A list of unique directory names extracted from the JSONL file.
    """
    unique_dirs = set()
    _log.info(f"Processing JSONL file: {jsonl_path}")

    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for line_number, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if 'p' in data:
                        unique_dirs.add(data['p'])
                except json.JSONDecodeError as e:
                    _log.warning(f"Skipping invalid JSON on line {line_number}: {e}")

        _log.info(f"Successfully extracted {len(unique_dirs)} unique directory name(s).")
    except Exception as e:
        _log.error(f"Failed to read file at {jsonl_path}: {e}")
        raise

    return sorted(unique_dirs)


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
    int | None
        The UTC offset, in hours as an integer, for the given timezone and date, 
        or None if there is an error
    """

    try:
        tz = pytz.timezone(timezone_name)
        localized_dt = tz.localize(dt_object, is_dst=is_dst)
        offset_timedelta = localized_dt.utcoffset()
        # Convert timedelta to total seconds and then to hours
        offset_hours = int(offset_timedelta.total_seconds() / 3600)  # type: ignore
        return offset_hours
    except pytz.UnknownTimeZoneError:
        _log.error(f"Error: Unknown time zone '{timezone_name}'.")
        return None
    except Exception as e:  # noqa: BLE001
        _log.error(f"An error occurred: {e}")
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

    Specifically:
    1) Calculates local timezone string for each image using timezonefinder,
        lat, and lon. because of the grouping (described next), if there are
        multiple timezones the most common is chosen
    2) Groups the image timestamps by local day,
        and calculates the mean lat/lon for each day.
        This greatly reduces the processing time required for the calculations,
        vs calculating a sunrise/sunset for each individual timestamp.
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

    try:
        from skyfield import almanac, api
        from timezonefinder import TimezoneFinder
    except ImportError as exc:
        raise ImportError(
            "Skyfield and TimezoneFinder are required for sunrise/sunset calculations. "
            "Install them with `pip install 'esdglider[optics]'`."
        ) from exc

    ts = api.load.timescale()
    eph = api.load("de421.bsp")
    sun = eph["Sun"]

    # Determine local timezones
    _log.info("Calculating local timezone string")
    tf = TimezoneFinder()
    tz_all = np.array([tf.timezone_at(lat=i, lng=j) for i, j in zip(lat, lon)])

    # Establish working dataframe, and convert times to local
    df = pd.DataFrame.from_dict(
        {"time": time, "lat": lat, "lon": lon},
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