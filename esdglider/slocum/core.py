"""
Core slocum functions for esdglider package. 
These functions are intended to be general enough for use outside ESD.
"""

import logging
import os

import netCDF4
import numpy as np
import xarray as xr
import yaml

from esdglider import utils
import esdglider.profiles as prof
import pyglider.utils as pgutils

try:
    import dbdreader
    have_dbdreader = True
except ImportError:
    have_dbdreader = False

_log = logging.getLogger(__name__)


"""
time_encoding for NetCDF time variables.

This dictionary can be used as the `encoding` argument when writing
time variables to NetCDF files using xarray or netCDF4, to be CF-compliant
"""

time_encoding = {
    'units': 'seconds since 1970-01-01T00:00:00Z',
    '_FillValue': np.nan,
    'calendar': 'gregorian',
    'dtype': 'float64',
}



def ngdac_profiles(inname, outdir, deploymentyaml, force=False):
    """
    ESD's version of extract_timeseries_profiles, from:
    https://github.com/c-proof/pyglider/blob/main/pyglider/ncprocess.py#L19

    Extract and save each profile from a timeseries netCDF.

    Parameters
    ----------
    inname : str or Path
        netcdf file to break into profiles
    outdir : str or Path
        directory to place profiles
    deploymentyaml : str or Path
        location of deployment yaml file for the netCDF file.  This should
        be the same yaml file that was used to make the timeseries file.
    force : bool, default False
        Force an overwite even if profile netcdf already exists

    Returns
    -------
    Nothing
    """
    try:
        os.makedirs(outdir)
    except FileExistsError:
        pass

    with open(deploymentyaml) as fin:
        deployment = yaml.safe_load(fin)

    # ESD: include all instrument vars
    # deployment["glider_devices"]
    instrument_meta = deployment["glider_devices"]
    instrument_str = ",".join(list(instrument_meta.keys()))

    meta = deployment["metadata"]
    with xr.open_dataset(inname) as ds:
        _log.info("Extracting profiles: opening %s", inname)
        trajectory = ds.attrs["id"].encode()
        trajlen = len(trajectory)

        profiles = np.unique(ds.profile_index)
        profiles = [p for p in profiles if (~np.isnan(p) and not (p % 1) and (p > 0))]
        for p in profiles:
            ind = np.where(ds.profile_index == p)[0]
            dss = ds.isel(time=ind)
            outname = outdir + "/" + utils.get_file_id_esd(dss) + ".nc"
            _log.info("Checking %s", outname)
            if force or (not os.path.exists(outname)):
                # this is the id for the whole file, not just this profile..
                dss["trajectory"] = trajectory
                # dss['trajectory'] = utils.get_file_id(ds).encode()
                # trajlen = len(utils.get_file_id(ds).encode())
                dss["trajectory"].attrs["cf_role"] = "trajectory_id"
                dss["trajectory"].attrs["comment"] = (
                    "A trajectory is a single"
                    "deployment of a glider and may span multiple data files."
                )
                dss["trajectory"].attrs["long_name"] = "Trajectory/Deployment Name"

                # profile-averaged variables....
                profile_meta = deployment["profile_variables"]
                if "water_velocity_eastward" in dss.keys():
                    dss["u"] = dss.water_velocity_eastward.mean()
                    dss["u"].attrs = profile_meta["u"]

                    dss["v"] = dss.water_velocity_northward.mean()
                    dss["v"].attrs = profile_meta["v"]
                elif "u" in profile_meta:
                    dss["u"] = profile_meta["u"].get("_FillValue", np.nan)
                    dss["u"].attrs = profile_meta["u"]

                    dss["v"] = profile_meta["v"].get("_FillValue", np.nan)
                    dss["v"].attrs = profile_meta["v"]
                else:
                    dss["u"] = np.nan
                    dss["v"] = np.nan

                dss["profile_id"] = np.int32(p)
                dss["profile_id"].attrs = profile_meta["profile_id"]
                if "_FillValue" not in dss["profile_id"].attrs:
                    dss["profile_id"].attrs["_FillValue"] = -1
                dss["profile_id"].attrs["valid_min"] = np.int32(
                    dss["profile_id"].attrs["valid_min"],
                )
                dss["profile_id"].attrs["valid_max"] = np.int32(
                    dss["profile_id"].attrs["valid_max"],
                )

                dss["profile_time"] = dss.time.mean()
                dss["profile_time"].attrs = profile_meta["profile_time"]
                # remove units so they can be encoded later:
                try:
                    del dss.profile_time.attrs["units"]
                    del dss.profile_time.attrs["calendar"]
                except KeyError:
                    pass
                dss["profile_lon"] = dss.longitude.mean()
                dss["profile_lon"].attrs = profile_meta["profile_lon"]
                dss["profile_lat"] = dss.latitude.mean()
                dss["profile_lat"].attrs = profile_meta["profile_lat"]

                dss["lat"] = dss["latitude"]
                dss["lon"] = dss["longitude"]
                dss["platform"] = np.int32(1)
                comment = f"{meta['glider_model']} operated by {meta['institution']}"
                dss["platform"].attrs["comment"] = comment
                dss["platform"].attrs["id"] = meta["glider_name"]
                dss["platform"].attrs["instrument"] = instrument_str
                dss["platform"].attrs["long_name"] = (
                    f"{meta['glider_model']} {dss['platform'].attrs['id']}"
                )
                dss["platform"].attrs["type"] = "platform"
                dss["platform"].attrs["wmo_id"] = meta["wmo_id"]
                if "_FillValue" not in dss["platform"].attrs:
                    dss["platform"].attrs["_FillValue"] = -1

                dss["lat_uv"] = np.nan
                dss["lat_uv"].attrs = profile_meta["lat_uv"]
                dss["lon_uv"] = np.nan
                dss["lon_uv"].attrs = profile_meta["lon_uv"]
                dss["time_uv"] = np.nan
                dss["time_uv"].attrs = profile_meta["time_uv"]

                # dss['instrument_ctd'] = np.int32(1.0)
                # dss['instrument_ctd'].attrs = profile_meta['instrument_ctd']
                # if '_FillValue' not in dss['instrument_ctd'].attrs:
                #     dss['instrument_ctd'].attrs['_FillValue'] = -1
                for key in instrument_meta.keys():
                    dss[key] = np.int32(1.0)
                    dss[key].attrs = instrument_meta[key]
                    if "_FillValue" not in dss[key].attrs:
                        dss[key].attrs["_FillValue"] = -1

                dss.attrs["date_modified"] = str(np.datetime64("now")) + "Z"

                # ancillary variables: link and create with values of 2.  If
                # we dont' want them all 2, then create these variables in the
                # time series
                to_fill = [
                    "temperature",
                    "pressure",
                    "conductivity",
                    "salinity",
                    "density",
                    "lon",
                    "lat",
                    "depth",
                ]
                for name in to_fill:
                    qcname = name + "_qc"
                    dss[name].attrs["ancillary_variables"] = qcname
                    if qcname not in dss.keys():
                        dss[qcname] = ("time", 2 * np.ones(len(dss[name]), np.int8))
                        dss[qcname].attrs = pgutils.fill_required_qcattrs({}, name)
                        # 2 is "not eval"

                _log.info("Writing %s", outname)
                timeunits = "seconds since 1970-01-01T00:00:00Z"
                timecalendar = "gregorian"
                try:
                    del dss.profile_time.attrs["_FillValue"]
                    del dss.profile_time.attrs["units"]
                except KeyError:
                    pass
                dss.to_netcdf(
                    outname,
                    encoding={
                        "time": {
                            "units": timeunits,
                            "calendar": timecalendar,
                            "dtype": "float64",
                        },
                        "profile_time": {
                            "units": timeunits,
                            "_FillValue": -99999.0,
                            "dtype": "float64",
                        },
                    },
                )

                # add traj_strlen using bare ntcdf to make IOOS happy
                with netCDF4.Dataset(outname, "r+") as nc:
                    nc.renameDimension("string%d" % trajlen, "traj_strlen")


def binary_to_raw_timeseries(
    indir,
    cachedir,
    outdir,
    deploymentyaml,
    *,
    search="*.[D|E]BD",
    include_source=False,
    fnamesuffix="",
    prof_depth_var : str | None ="depth_measured",
    prof_args: dict | None = None,
):
    """
    An adaptation of pyglider.slocum.binary_to_timeseries to 
    extract raw, unprocessed glider data using dbdreader.
    dbdreader only deals with flight and science computers,
    hence only classifying variables as 'eng' or 'sci'

    The dbdreader MultiDBD.get() method is used,
    rather than get_sync, to read the parameters specified in
    deploymentyaml. The argument return_nans (of MultiDBD.get()) is set to
    True, so that there are two 'time bases' for the extracted data: one
    for engineering variables (from m_present_time), and one for science
    variables (from sci_m_present_time). These times are merged,
    and these values are the time index of the output file.

    No values are interpolated. If the metadata contains a 'deployment_min_dt' 
    entry, timestamps before this minimum deployment time are dropped. If the 
    dataset contains (i.e., the yaml specifies) a variable named 'pressure', 
    then the depth from the CTD will be calculated and retained as 'depth_ctd'.

    Parameters
    ----------
    Majority of params are the same as pyglider.slocum.binary_to_timeseries
    include_source : bool
        Boolean indicating if the source file should be included in the raw ds.
        Passed to dbdreader.MULTIDBD.get
    prof_depth_var : str | None
        Name of the depth variable to use for profile detection in findProfiles.
        Must be either 'depth_measured' (for m_depth, default) 
        or 'depth_ctd' (for CTD-calculated depth).
        If None, then profiles will not be calcualted
    prof_args : dict, optional
        named optional arguments, for esdglider.profiles.findProfiles

    Returns
    -------
    outname : string
        name of the newly written netcdf file
    """

    if not have_dbdreader:
        raise ImportError("Cannot import dbdreader")

    if prof_args is None:
        prof_args = {}

    if not prof_depth_var in ("depth_measured", "depth_ctd"):
        _log.error("Invalid prof_depth_var: %s", prof_depth_var)
        raise ValueError("prof_depth_var must be either 'depth_measured' or 'depth_ctd'")

    # Read and parse deployment yaml(s)
    deployment = pgutils._get_deployment(deploymentyaml)

    # Concatenate all netcdf variables from the deployment YAML files
    ncvar = utils._get_deployment_netcdfvars(deploymentyaml)
    thenames = list(ncvar.keys())
    thenames.remove("time")

    # build a new data set based on info from deploymentyaml
    ds = xr.Dataset()
    attr = {}
    name = "time"
    for atts in ncvar[name].keys():
        if (atts != "coordinates") & (atts != "units") & (atts != "calendar"):
            attr[atts] = ncvar[name][atts]

    sensors = []
    for nn, name in enumerate(thenames):
        sensorname = ncvar[name]["source"]
        sensors.append(sensorname)
    _log.debug(f"sensors: {[i for i in sensors]}")

    # Check for uniqueness, because a duplicate causes an error when unioning
    if len(sensors) != len(set(sensors)):
        _log.error(f"sensors: {sensors}")
        raise ValueError("The sensor list has duplicate sensors")

    # get the dbd object
    _log.info(f"dbdreader pattern: {indir}/{search}")
    dbd = dbdreader.MultiDBD(pattern=f"{indir}/{search}", cacheDir=cachedir)  # type: ignore
    sci_params = dbd.parameterNames["sci"]
    eng_params = dbd.parameterNames["eng"]
    first_eng = np.where([i in eng_params for i in sensors])[0][0]
    first_sci = np.where([i in sci_params for i in sensors])[0][0]

    # Check that all sensor names are in sci_params or eng_params
    sensor_in_dbd = [i in (eng_params+sci_params) for i in sensors]
    if not all(sensor_in_dbd):
        _log.error("Not all sensors are recognized by dbdreader as sci or eng")
        sensors_not_in_dbd = [i for i, k in zip(sensors, sensor_in_dbd) if not k]
        _log.error("offending sensors: %s", "; ".join(sensors_not_in_dbd))
        raise ValueError("Not all sensors are recognized by dbdreader as sci or eng")

    # get the data, across all eng/sci timestamps
    # return_nans=True so data arrays are of exactly two lengths (eng/sci)
    source_data = dbd.get(
        *sensors,
        return_nans=True,
        include_source=include_source,
    )

    # If include_source is true, then parsing is a bit different
    if include_source:
        data_list, s = zip(*source_data)
        _log.debug("Parsing source filenames")
        eng_files = [os.path.basename(i.filename) for i in s[first_eng]]
        sci_files = [os.path.basename(i.filename) for i in s[first_sci]]
    else:
        data_list = source_data
    data_time, data = zip(*data_list)

    # Sanity check: only two sets of times
    # Note: the for loop checks that all sensors sci or eng
    data_time_len = [len(i) for i in data_time]
    _log.debug(f"data time lengths: {data_time_len}")
    _log.debug(f"data array lengths: {[len(i) for i in data]}")
    if len(set(data_time_len)) > 2:
        _log.error(f"data time lengths: {data_time_len}")
        raise ValueError("There are more than 2 time bases")
    # if not all([i in (eng_params+sci_params) for i in sensors]):
    #     _log.error(f'sensors: {sensors}')
    #     raise ValueError("Not all sensors are recognized by dbdreader as sci or eng")

    # get and union the exactly 2 unique sets of times: eng and sci
    # eng_time = np.int64(pgutils._time_to_datetime64(data_time[eng1])) #second
    eng_time = data_time[first_eng]
    sci_time = data_time[first_sci]
    time = np.union1d(eng_time, sci_time)
    _log.debug(
        f"eng/sci/total time counts: {len(eng_time)}/{len(sci_time)}/{len(time)})",
    )

    # get the indices of the sci and eng timestamps in the unioned times
    sci_indices = np.searchsorted(time, sci_time)
    eng_indices = np.searchsorted(time, eng_time)

    _log.debug(f"time array length: {len(time)}")
    ds["time"] = (("time"), time, attr)
    ds["latitude"] = (("time"), np.zeros(len(time)))
    ds["longitude"] = (("time"), np.zeros(len(time)))

    for nn, name in enumerate(thenames):
        _log.info("working on %s", name)
        if "method" in ncvar[name].keys():
            continue
        # variables that are in the data set or can be interpolated from it
        if "conversion" in ncvar[name].keys():
            convert = getattr(pgutils, ncvar[name]["conversion"])
        else:
            convert = pgutils._passthrough

        sensorname = ncvar[name]["source"]
        _log.info("names: %s %s", name, sensorname)
        val = np.full(len(time), np.nan)
        if sensorname in sci_params:
            _log.debug("Sci sensorname %s", sensorname)
            val[sci_indices] = data[nn]
            # val = pgutils._zero_screen(val)
            val = convert(val)
        elif sensorname in eng_params:
            _log.debug("Eng sensorname %s", sensorname)
            val[eng_indices] = data[nn]
            val = convert(val)
        else:
            raise ValueError(f"{sensorname} not in sci or eng parameter names")

        # make the attributes:
        ncvar[name]["coordinates"] = "time"
        attrs = ncvar[name]
        attrs = pgutils.fill_required_attrs(attrs)
        ds[name] = (("time"), val, attrs)

    # For ordering of data columns
    ds["distance_over_ground"] = (("time"), np.zeros(len(time)))

    # If specified, add the source filename
    if include_source:
        name = "source_filename"
        _log.info("working on %s", name)
        val = np.full(len(time), "nan", dtype="<U16")
        val[eng_indices] = eng_files  # type: ignore
        val[sci_indices] = sci_files  # type: ignore
        if np.any(np.count_nonzero(val == "nan")):
            _log.warning("Some datapoints have a nan 'source_filename' value")
        attrs = {
            "comment": "The source file where the datapoint originated from",
            "source": "os.path.basename(dbd.filename)",
        }
        ds[name] = (("time"), val, attrs)

    # screen out-of-range times; these won't convert:
    ds["time"] = ds.time.where((ds.time > 0) & (ds.time < 6.4e9), np.nan)
    ds["time"] = (ds.time * 1e9).astype("datetime64[ns]")
    # drop bogus times
    if "deployment_min_dt" in deployment["metadata"]:
        min_dt_str = deployment["metadata"]["deployment_min_dt"]
    else:
        min_dt_str = "1970-01-01"
    ds = utils.drop_bogus_times(ds, min_dt=min_dt_str, max_drop=True)
    
    # ds = ds.where(ds.time >= np.datetime64(min_dt_str), drop=True)
    ds["time"].attrs = attr

    # Drop rows with nan values across all data variables
    ds = ds.dropna("time", how="all")
    _log.info("The raw timeseries has %s data points", ds.time.shape[0])

    # Calculate depth (ctd); only keep values where pressure is not nan
    ds = pgutils.get_glider_depth(ds)
    ds["depth"] = ds["depth"].where(~np.isnan(ds["pressure"]))
    ds = ds.rename_vars({'depth': 'depth_ctd'})

    # Calcualte profiles with chosen variable
    if prof_depth_var is not None:
        _log.info("Calculating profiles using variable: %s", prof_depth_var)
        ds = prof.get_fill_profiles(ds, "time", prof_depth_var, prof_args)

    # Calculate DOG; only keep values where lat/lon is not nan
    ds = pgutils.get_distance_over_ground(ds)
    ll_good = ~np.isnan(ds.latitude.values + ds.longitude.values)
    ds["distance_over_ground"] = ds["distance_over_ground"].where(ll_good)

    # Add metadata
    device_data = deployment['glider_devices']
    ds = pgutils.fill_metadata(ds, deployment['metadata'], device_data)

    outname = outdir + "/" + ds.attrs["deployment_name"] + fnamesuffix + ".nc"
    _log.info("writing %s", outname)
    ds.to_netcdf(
        outname, 
        encoding={'time': time_encoding}
    ) 
    # pgutils._save_dataset(
    #     ds,
    #     outname,
    #     deployment,
    #     encoding={'time': time_encoding},
    # )

    return outname


def raw_to_sci_timeseries(
    inname,
    outdir,
    deploymentyaml,
    *,
    fnamesuffix="",
    maxgap=300,
):
    """
    Go from raw timeseries (from esdglider.glider.binary_to_raw_timeseries)
    to a processed science timeseries.
    This function can be used in cases where different science sensors are
    on at different times, e.g. PAM deployments, and thus it is not possible
    to get the full science timeseries using dbdreader.get_sync.

    Other than not using get_sync, this function closely follows the
    pyglider.slocum.binary_to_timeseries

    Parameters
    ----------
    All params are the same as pyglider.slocum.binary_to_timeseries

    Returns
    -------
    outname : string
        name of the newly written netcdf file
    """

    ds = xr.open_dataset(inname, decode_times=True)

    # Read and parse deployment yaml(s), to get variables
    deployment = pgutils._get_deployment(deploymentyaml)
    ncvar = deployment["netcdf_variables"]
    [ncvar[i]["source"] for i in ncvar]

    # Define variables to keep, and the science variables
    vars_tokeep = [i for i in ncvar.keys() if (i in ds.keys() and i != "time")]
    vars_sci = [i for i in ncvar if "sci" in ncvar[i]["source"] and i != "time"]

    ds = ds[vars_tokeep].dropna(dim="time", how="all")

    # For the science variables: interpolate and run find_gaps
    # To be consistent with pyglider, engineering variables are
    # interpolated, but not run through find_gaps
    t = ds.time.values.astype(np.int64) / 1e9
    for i in vars_tokeep:
        _log.info("variable %s", i)
        if i in ["time", "profile_index", "profile_direction"]:
            continue

        # interpolate
        da = ds[i].dropna(dim="time")
        _t = da.time.values.astype(np.int64) / 1e9
        val_interp = np.interp(t, _t, da.values, left=np.nan, right=np.nan)

        # To be consistent with pyglider.slocum.binary_to_timeseries,
        # only find gaps and zero screens for science vars
        # Ensure that _t, t, and maxgap are all in the same units
        if i in vars_sci:
            tg_ind = pgutils.find_gaps(_t, t, maxgap)
            val_interp[tg_ind] = np.nan
            val_interp = pgutils._zero_screen(val_interp)
            _log.debug("number of gaps %s", np.count_nonzero(tg_ind))

        # Update ds object with values and attributes
        ds[i].values = val_interp
        ds[i].attrs["method"] = "linear fill"
        # The var already has the yaml-specified attributes from binary_to_raw

    # For consistency with pyglider
    device_data = deployment['glider_devices']
    ds = pgutils.fill_metadata(ds, deployment['metadata'], device_data)

    # Drop rows where all science vars are nan
    _log.info(
        "Dropping datapoints that have nan values for all of these vars: %s",
        ", ".join([str(i) for i in vars_sci]),
    )
    ds = ds.dropna(dim="time", how="all", subset=vars_sci)

    if ("temperature" in ds) and ("conductivity" in ds) and ("pressure" in ds):
        ds = pgutils.get_derived_eos_raw(ds)

    # Write out to file
    outname = f"{outdir}/{ds.attrs['deployment_name'] + fnamesuffix}.nc"
    _log.info("writing %s", outname)
    pgutils._save_dataset(
        ds,
        outname,
        deployment,
        encoding={'time': time_encoding},
    )

    return outname


def decompress_dir(binarydir):
    """
    A light wrapper around the dbdreader function decompress_file
    Decompress all compressed bianry files in binarydir.
    Decompressed files will be written within binarydir.
    Compressed files will not be altered

    Parameters
    ----------
    binarydir : string
        A string representing the directory within which to decompress files
    """

    if not have_dbdreader:
        raise ImportError("Cannot import dbdreader")

    binarydir_files = os.listdir(binarydir)
    _log.info("There are %s files in %s", len(binarydir_files), binarydir)

    # FileDecompressor.decompress(dcd1)
    _log.info("decompressing all files in %s", binarydir)
    for fin in binarydir_files:
        _log.debug(fin)
        if dbdreader.decompress.is_compressed(fin):  # type: ignore
            dbdreader.decompress.decompress_file(os.path.join(binarydir, fin))  # type: ignore
        else:
            _log.debug("skipping %s", fin)

    binarydir_files = os.listdir(binarydir)
    _log.info("There are now %s files in %s", len(binarydir_files), binarydir)

