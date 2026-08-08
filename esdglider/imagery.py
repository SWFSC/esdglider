# import datetime
from datetime import datetime
import logging
import os
from PIL import Image
from PIL.ExifTags import TAGS

import numpy as np
import pandas as pd

import esdglider.utils as utils

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
    df["time_dt"] = df['time_str'].apply(lambda x: datetime.strptime(x, format))  # noqa: DTZ007
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

    deployment = ds.attrs["deployment_name"]
    # imagedir = img_paths["imagedir"]
    metadir = img_paths["metadir"]
    ancdir = img_paths["ancdir"]
    
    _log.info(f"Creating imagery ancillary data file for {deployment}")
    # _log.info(f"Using images directory {imagedir}")
    _log.info(f"Using image metadata directory {metadir}")

    csv_file =  img_paths["imgcsv"]
    if os.path.isfile(csv_file):
        _log.info(f"Deleting old imagery ancillary data file: {csv_file}")
        os.remove(csv_file)

    # # --------------------------------------------
    # # Checks
    # if not os.path.isdir(imagedir):
    #     raise FileNotFoundError(f"{imagedir} does not exist")
    # else:
    #     # NOTE: this should probably be a separate function, and return a tuple
    #     filepaths = glob.glob(f"{imagedir}/**/*.{ext}", recursive=True)
    #     _log.debug(f"Found {len(filepaths)} files with the extension {ext}")
    #     if len(filepaths) == 0:
    #         _log.error(
    #             "Zero image files were found. Did you provide "
    #             + "the right path, and use the right file extension?",
    #         )
    #         raise ValueError("No files for which to generate ancillary data")
    #     imagery_files = [os.path.basename(path) for path in filepaths]
    #     imagery_dirs = [os.path.basename(os.path.dirname(path)) for path in filepaths]

    # --------------------------------------------
    # Extract info from imagery file names
    _log.debug("Processing imagery file names")
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

    # # TODO: filter for dates after deployment_min_dt?

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

    _log.info("Determining mask for invalid values")
    time_mask = (
        ~np.isnan(ds_interp["time"])
        & ~np.isnan(ds_interp["latitude"])
        & ~np.isnan(ds_interp["longitude"])
    )
    ds_interp_ll = ds_interp.where(time_mask, drop=True)

    # su, sd = sunset_sunrise(
    #     ds_interp_ll.time.values,
    #     ds_interp_ll.latitude.values,
    #     ds_interp_ll.longitude.values,
    # )
    # su_full = np.full(ds_interp.time.shape[0], np.nan, dtype='datetime64[us]')
    # su_full[ll_mask] = su
    # df["sunrise_utc"] = su_full
    # sd_full = np.full(ds_interp.time.shape[0], np.nan, dtype='datetime64[us]')
    # sd_full[ll_mask] = sd
    # df["sunset_utc"] = sd_full

    # # Calculate local timezone, based on lat/lon
    # _log.info("Calculating local timezone string")
    # tf = TimezoneFinder()
    # tz = [
    #     tf.timezone_at(lat=i.item(), lng=j.item())
    #     for i, j in zip(ds_interp_ll['latitude'], ds_interp_ll['longitude'])
    # ]

    # tz_full = np.full(ds_interp.time.shape[0], np.nan, dtype='object')
    # tz_full[time_mask] = tz
    # df["tz"] = tz_full

    # # Calculate utc offset as an integer, based on date and local tz
    # _log.info("Calculating local utc offset as an integer")
    # utc_offset = np.array([
    #     utils.get_utc_offset_integer(i, j.astype(datetime.datetime))
    #     for i, j in zip(tz, ds_interp_ll['time'].values)
    # ])

    # utc_offset_full = np.full(ds_interp.time.shape[0], np.nan, dtype='object')
    # utc_offset_full[time_mask] = utc_offset
    # df["tz_utc_offset"] = utc_offset_full

    # Calculate sunrise and sunset
    su, sd, tl = utils.get_sunrise_sunset(
        time=ds_interp_ll["time"].values,
        lat=ds_interp_ll["latitude"].values,
        lon=ds_interp_ll["longitude"].values,
    )

    su_full = np.full(ds_interp.time.shape[0], np.nan, dtype="datetime64[us]")
    sd_full = np.full(ds_interp.time.shape[0], np.nan, dtype="datetime64[us]")
    tl_full = np.full(ds_interp.time.shape[0], np.nan, dtype="datetime64[us]")

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
