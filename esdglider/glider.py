import logging
import os
from importlib import metadata, resources
import tempfile

import netCDF4
import numpy as np
import pandas as pd
import pyglider.ncprocess as pgncprocess
import pyglider.slocum as pgslocum
import pyglider.utils as pgutils
import xarray as xr
import yaml

try:
    import dbdreader

    have_dbdreader = True
except ImportError:
    have_dbdreader = False

from esdglider import plots, utils

_log = logging.getLogger(__name__)


"""
ESD-specific glider values. 
Note that depth bins will be defined by `np.arange(0, depth_max, i)`, 
where i is in element of bin_size

gridded_exclude_vars: list
    variables to exclude when gridding the science timeseries
    If other (eg, engineering) variables are added to the timeseries/yaml,
    then this list will need to be updated

bin_size: list
    a list of the gridded depth bin sizes 

depth_max: float
    The maximum value to use when making depth bins    
"""
gridded_exclude_vars = [
    "distance_over_ground",
    "heading",
    "pitch",
    "roll",
    "waypoint_latitude",
    "waypoint_longitude"
]
bin_size=[1, 5]
depth_max=1200.1


def get_path_yaml(yaml_type: str) -> str:
    """
    Get the path to the specified yaml (raw or eng). 
    The yamls are included as part of the package data, 
    and contain the relevant NetCDF variables to extract from the binary files

    Parameters
    ----------
    yaml_type : str
        A string that defines the type of yaml to get. 
        Must be either 'raw' or 'eng'

    Returns
    -------
    str
        the path of the yaml
    """
    if not (yaml_type in ["raw", "eng"]):
        _log.error("yaml_type %s", yaml_type)
        raise ValueError("yaml_type must be either 'raw' or 'eng'")
    
    ref = resources.files("esdglider.data") / f"deployment-{yaml_type}-vars.yml"
    with resources.as_file(ref) as path:
        return str(path)


def get_path_deployment(
    deployment_info: dict,
    deployments_path: str,
) -> dict:
    """
    Return a dictionary of paths for use by other esdglider functions.
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    Parameters
    ----------
    deployment_info : dict
        A dictionary with the relevant deployment info. A dictionary is
        used to make it easier if arguments are added or removed.
        This dictionary must contain at least:
        deploymentyaml : str
            The filepath of the glider deployment yaml.
            This file will have relevant info,
            including deployment name (eg, amlr01-20210101) and project
        mode : str
            Mode of the glider data being processed.
            Must be either 'rt', for real-time, or 'delayed
    deployments_path : str
        The path to the top-level folder of the glider data.
        This is intended to be the path to the mounted glider deployments bucket

    Returns
    -------
        A dictionary with the relevant paths
    """

    # Extract or calculate relevant info
    deploymentyaml = deployment_info["deploymentyaml"]
    mode = deployment_info["mode"]
    deployment = utils.read_deploymentyaml(deploymentyaml)

    deployment_name = deployment["metadata"]["deployment_name"]
    project = deployment["metadata"]["project"]
    year = utils.year_path(project, deployment_name)

    # Check that relevant deployment path exists
    glider_path = os.path.join(deployments_path, project, year, deployment_name)
    if not os.path.isdir(glider_path):
        _log.error(f"glider_path ({glider_path}) does not exist")
        return {}

    cacdir = os.path.join(deployments_path, "cache")
    binarydir = os.path.join(glider_path, "data", "binary", mode)
    rawyaml = get_path_yaml("raw")
    engyaml = get_path_yaml("eng")
    logdir = os.path.join(deployments_path, "logs")

    procl1dir = os.path.join(glider_path, "data", "processed-L1")
    procl2dir = os.path.join(glider_path, "data", "processed-L2")
    plotdir = os.path.join(glider_path, "plots", mode)

    # Separate, in case in the future they end up in their own directories
    rawdir = procl1dir
    tsdir = procl1dir
    griddir = procl1dir
    profdir = os.path.join(procl1dir, "ngdac", mode)

    # Create common file names
    path_raw = os.path.join(tsdir, f"{deployment_name}-{mode}-raw.nc")
    path_sci = os.path.join(tsdir, f"{deployment_name}-{mode}-sci.nc")
    path_eng = os.path.join(tsdir, f"{deployment_name}-{mode}-eng.nc")
    path_gr1 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-1m.nc")
    path_gr5 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-5m.nc")
    path_prof_summ = os.path.join(tsdir, f"{deployment_name}-{mode}-profiles.csv")

    return {
        "cacdir": cacdir,
        "binarydir": binarydir,
        "deploymentyaml": deploymentyaml,
        "rawyaml": rawyaml,
        "engyaml": engyaml,
        "logdir": logdir,
        "rawdir": rawdir,
        "tsdir": tsdir,
        "griddir": griddir,
        "profdir": profdir,
        "plotdir": plotdir,
        "procl1dir": procl1dir,
        "procl2dir": procl2dir,
        "tsrawpath": path_raw,
        "tsscipath": path_sci,
        "tsengpath": path_eng,
        "gr1path": path_gr1,
        "gr5path": path_gr5,
        "profsummpath": path_prof_summ,
        "mode": mode,
    }


def binary_to_nc(
    deployment_info: dict,
    paths: dict,
    *, 
    write_raw: bool = True,
    write_timeseries: bool = True,
    sci_timeseries_pyglider: bool = True, 
    write_gridded: bool = True,
    file_info: str | None = None,
    **kwargs,
):
    """
    Process binary ESD slocum glider data to netCDF file(s).
    For more info, see:
    https://swfsc.github.io/glider-lab-manual/content/glider-data.html

    The contents of this function used to just be in scripts/binary_to_nc.py.
    They were moved to this structure for easier development and debugging

    Parameters
    ----------
    deployment_info : dict
        A dictionary with the relevant deployment info. A dictionary is
        used to make it easier if arguments are added or removed.
        This dictionary must contain at least:
        deploymentyaml : str
            The filepath of the glider deployment yaml.
            This file will have relevant info,
            including deployment name (eg, amlr01-20210101) and project
        mode : str
            Mode of the glider data being processed.
            Must be either 'rt', for real-time, or 'delayed
    paths : dict
        A dictionary of file/directory paths for various processing steps.
        Intended to be the output of esdglider.glider.get_path_deployment()
        See this function for the expected key/value pairs
    write_raw, write_timeseries, write_gridded : bool, default True
        Should the raw, timeseries, and gridded, respectively,
        xarray DataSets be created and written to files?
        Raw files are created by binary_to_raw, and include uninterpolated data
        Timeseries files are created by pyglider's binary_to_timeseries;
        both 'engineering' and 'science' timeseries files are created.
        Eng and sci files have m_depth and sci_water_temp as the time bases,
        respectively. Gridded files are created by pyglider's make_gridfiles,
        using the science timeseries as the input.
        Both 1m and 5m gridded datasets are created.
        Note: if True then any existing files will be clobbered
    sci_timeseries_pyglider : bool, default False
        Should the function use pyglider.slocum.binary_to_timeseries to create 
        create the science timeseries (True), 
        or glider.timeseries_raw_to_sci (False)
    file_path: str | None, default None
        The path of the parent processing script.
        If provided, will be included in the history attribute
    **kwargs
        Optional arguments passed to utils.findProfiles

    Returns
    -------
    A dictionary of the filenames of the various netCDF files, as strings.
    In order: the raw data, the engineering and science timeseries,
    and the 1m and 5m gridded files
    """

    deploymentyaml = deployment_info["deploymentyaml"]
    mode = deployment_info["mode"]

    # --------------------------------------------
    # Check files, and get vars + directory paths
    if paths["deploymentyaml"] != deploymentyaml:
        raise ValueError(
            "Provided yaml path (%s) is not the same as the paths yaml path (%s)",
            deploymentyaml, 
            paths['deploymentyaml'], 
        )

    deployment = utils.read_deploymentyaml(deploymentyaml)
    deployment_name = deployment["metadata"]["deployment_name"]

    deploymentyaml = paths["deploymentyaml"]
    rawdir = paths["rawdir"]
    tsdir = paths["tsdir"]
    griddir = paths["griddir"]

    # Check mode, set binary_search regex
    if mode == "delayed":
        binary_search = "*.[D|E|d|e][Bb][Dd]"
    elif mode == "rt":
        binary_search = "*.[S|T|s|t][Bb][Dd]"
    else:
        raise ValueError("mode must be either 'rt' or 'delayed'")

    # commonly used parameters in timeseries/gridded data
    maxgap_esd = 60

    # Dictionary with info needed by post-processing functions
    postproc_info = deployment_info | {
        "file_info": file_info,
        "metadata_dict": {"deployment_name": deployment_name},
        "device_dict": {},
        "profile_summary_path": paths["profsummpath"],
        "maxgap": maxgap_esd, 
    }

    # --------------------------------------------
    # Raw
    outname_tsraw = paths["tsrawpath"]
    if write_raw:
        utils.remove_file(outname_tsraw)
        utils.makedirs_pass(rawdir)

        _log.info("Generating raw nc")
        outname_tsraw = binary_to_raw_timeseries(
            paths["binarydir"],
            paths["cacdir"],
            rawdir,
            [deploymentyaml, paths["engyaml"], paths["rawyaml"]],
            search=binary_search,
            include_source=True,
            fnamesuffix=f"-{mode}-raw",
            pp=postproc_info,
            **kwargs,
        )

        # Save profile summary
        tsraw = xr.load_dataset(outname_tsraw)
        prof_summ_path = postproc_info["profile_summary_path"]
        _log.info("Writing profile summary CSV to %s", prof_summ_path)
        prof_summ = utils.calc_profile_summary(tsraw, "depth_measured")
        prof_summ.to_csv(prof_summ_path, index=False)
        num_dives = np.count_nonzero(prof_summ.profile_direction.values == 1)
        _log.info("Deployment %s performed %s dives", deployment_name, num_dives)

        # Write deployment_start and deployment_end to postproc_info
        postproc_info["deployment_start"] = tsraw.attrs["deployment_start"]
        postproc_info["deployment_end"] = tsraw.attrs["deployment_end"]

        # Brief profile and depth sanity checks
        _log.info("raw timeseries checks")
        utils.check_profiles(prof_summ)
        utils.check_depth(tsraw["depth_measured"], tsraw["depth_ctd"])

    else:
        _log.info("Not writing raw nc")
        with xr.open_dataset(outname_tsraw) as tsraw:
            # tsraw = xr.load_dataset(outname_tsraw)
            postproc_info["deployment_start"] = tsraw.attrs["deployment_start"]
            postproc_info["deployment_end"] = tsraw.attrs["deployment_end"]

    # --------------------------------------------
    # Timeseries
    outname_tseng = paths["tsengpath"]
    outname_tssci = paths["tsscipath"]
    outname_gr1m = paths["gr1path"]
    outname_gr5m = paths["gr5path"]
    if write_timeseries:
        # Delete previous files before starting run. Can't delete whole directory
        # Since gridded depend on ts, also delete gridded
        utils.remove_file(outname_tseng)
        utils.remove_file(outname_tssci)
        utils.remove_file(outname_gr1m)
        utils.remove_file(outname_gr5m)
        utils.makedirs_pass(tsdir)

        # Engineering - uses m_depth as time base
        _log.info("Generating engineering timeseries")
        outname_tseng = pgslocum.binary_to_timeseries(
            paths["binarydir"],
            paths["cacdir"],
            tsdir,
            [deploymentyaml, paths["engyaml"]],
            search=binary_search,
            fnamesuffix=f"-{mode}-eng",
            time_base="m_depth",
            profile_filt_time=None,  # type: ignore
            maxgap=maxgap_esd,
        )

        _log.info(f"Post-processing engineering timeseries: {outname_tseng}")
        tseng = xr.load_dataset(outname_tseng)
        tseng = postproc_eng_timeseries(tseng, postproc_info, **kwargs)
        utils.to_netcdf_esd(tseng, outname_tseng)

        if sci_timeseries_pyglider:
            # Science - uses sci_water_pressure as time_base sensor
            _log.info("Generating science timeseries")
            outname_tssci = pgslocum.binary_to_timeseries(
                paths["binarydir"],
                paths["cacdir"],
                tsdir,
                deploymentyaml,
                search=binary_search,
                fnamesuffix=f"-{mode}-sci",
                time_base="sci_water_pressure",
                profile_filt_time=None,  # type: ignore
                maxgap=maxgap_esd,
            )

            _log.info(f"Post-processing science timeseries: {outname_tssci}")
            postproc_info["drop_vars"] = ["pressure"]
            tssci = xr.load_dataset(outname_tssci)
            tssci = postproc_sci_timeseries(tssci, postproc_info, **kwargs)
            utils.to_netcdf_esd(tssci, outname_tssci)

        else:
            _log.info("Generating science timeseries, via raw_to_sci_timeseries")
            outname_tssci = timeseries_raw_to_sci(
                outname_tsraw, 
                tsdir, 
                deploymentyaml,
                fnamesuffix=f"-{mode}-sci",
                maxgap = maxgap_esd, 
                pp=postproc_info, 
                **kwargs
            )
            # raw_to_sci_timeseries calls postproc_sci_timeseries internally
            tssci = xr.load_dataset(outname_tssci)

        _log.info("final eng/sci timeseries checks")
        # Brief profile sanity check - check_profiles done in postproc-general
        prof_max_diff = abs(
            (tssci.profile_index.max() - tseng.profile_index.max()).values,
        )
        if prof_max_diff > 0.5:
            _log.warning(
                "The max profile idx of eng and sci timeseries is different "
                + "by more than 0.5. This means they may have "
                + "a different number of functional profiles",
            )
            _log.warning(f"Min idx for eng: {tseng.profile_index.values.min()}")
            _log.warning(f"Min idx for sci: {tssci.profile_index.values.min()}")
            _log.warning(f"Max idx for eng: {tseng.profile_index.values.max()}")
            _log.warning(f"Max idx for sci: {tssci.profile_index.values.max()}")
        else:
            _log.info("The eng and sci timeseries have the same functional profiles")

        # Depth check, across the eng/sci datasets
        utils.check_depth(tseng["depth"], tssci["depth"])

    else:
        _log.info("Not writing timeseries nc")

    # --------------------------------------------
    # Gridded data, 1m and 5m
    if write_gridded:
        grid_esd(outname_tssci, paths=paths)

        # utils.remove_file(outname_gr1m)
        # utils.remove_file(outname_gr5m)
        # if not os.path.isfile(outname_tssci):
        #     raise FileNotFoundError(f"Could not find {outname_tssci}")

        # _log.debug("Excluded vars: %s", ", ".join(gridded_exclude_vars))

        # _log.info("Generating 1m gridded data")
        # outname_gr1m = pgncprocess.make_gridfiles(
        #     outname_tssci,
        #     griddir,
        #     deploymentyaml,
        #     depth_bins=np.arange(0, 1200.1, 1),
        #     fnamesuffix=f"-{mode}-1m",
        #     exclude_vars=gridded_exclude_vars,
        # )

        # _log.info("Generating 5m gridded data")
        # outname_gr5m = pgncprocess.make_gridfiles(
        #     outname_tssci,
        #     griddir,
        #     deploymentyaml,
        #     depth_bins=np.arange(0, 1200.1, 5),
        #     fnamesuffix=f"-{mode}-5m",
        #     exclude_vars=gridded_exclude_vars,
        # )

    else:
        _log.info("Not writing gridded nc")

    # --------------------------------------------
    return {
        "outname_tsraw": outname_tsraw,
        "outname_tseng": outname_tseng,
        "outname_tssci": outname_tssci,
        "outname_gr1m": outname_gr1m,
        "outname_gr5m": outname_gr5m,
    }


def postproc_attrs(ds: xr.Dataset, pp: dict):
    """
    Update attrbites of xarray DataSet ds
    pp is dictionary that provides values needed by postproc_attrs
    Used for all of eng, sci, and raw timeseries
    """

    # Rerun pyglider metadata functions, now that drop_bogus has been run
    #   pp is a 'hack' to be able to use pyglider function
    ds = pgutils.fill_metadata(ds, pp["metadata_dict"], pp["device_dict"])

    # When used within binary_to_nc, this code makes sure the values
    # are only calculated from the raw dataset
    if "deployment_start" in pp.keys():
        ds.attrs["deployment_start"] = pp["deployment_start"]
        ds.attrs["deployment_end"] = pp["deployment_end"]
    else:
        ds.attrs["deployment_start"] = str(ds["time"].values[0].astype("datetime64[s]"))
        ds.attrs["deployment_end"] = str(ds["time"].values[-1].astype("datetime64[s]"))

    # Determine the glider ID using min_dt, and check vs ID from time
    # min_dt = ds.deployment_min_dt
    min_dt64 = np.datetime64(ds.deployment_min_dt)
    min_dt_str = min_dt64.item().strftime("%Y%m%dT%H%M")
    ds.attrs["id"] = f"{ds.attrs['glider_name']}-{min_dt_str}"

    time_str = ds.time.values[0].astype("datetime64[s]").item().strftime("%Y%m%dT%H%M")
    if min_dt_str != time_str:
        _log.warning(
            "The dataset ID generated from the metadata (%s) "
            + "is different from that generated from the time (%s)."
            + "Using the ID from the metadata",
            min_dt_str,
            time_str,
        )

    # Other ESD updates, or fixes, of pyglider attributes
    # ds.attrs["id"] = utils.get_file_id_esd(ds)
    ds.attrs["title"] = ds.attrs["id"]
    ds.attrs["processing_level"] = (
        "Minimal data screening. "
        + "Data provided as is, with no expressed or implied assurance "
        + "of quality assurance or quality control."
    )
    file_info = pp["file_info"]
    if file_info is None:
        file_info = "netCDF files created using"
    ds.attrs["history"] = f"{utils.datetime_now_utc()}: {file_info}: " + "; ".join(
        [
            f"deployment_name={ds.deployment_name}",
            f"mode={pp['mode']}",
            f"pyglider v{metadata.version('pyglider')}",
            f"esdglider v{metadata.version('esdglider')}",
        ],
    )

    return ds


def postproc_general(
    ds: xr.Dataset,
    pp: dict,
    # drop_vars: list | None = None,
    **kwargs,
) -> xr.Dataset:
    """
    Post-processing steps shared by the science and engineering timeseries

    Returns the ds Dataset with updated values and attributes
    """

    # VALUES
    # Remove times that are nan or <min_dt, and drop other bogus values
    _log.info("The given timeseries has %s data points", ds.time.shape[0])
    ds = utils.drop_bogus(ds, min_dt=ds.deployment_min_dt, max_drop=True)

    # Check for and verbosely remove any duplicated timestamps
    ds_index = ds.get_index("time")
    if ds_index.duplicated().any():
        df_dup = ds_index.duplicated()
        _log.warning(
            "There are %s duplicated timestamps in the current dataset. "
            + "The second of the duplicated timestamps will be dropped. "
            + "Indexes, of the original dataset: %s",
            df_dup.sum(),
            ", ".join([str(i[0]) for i in np.argwhere(df_dup)]),  # type: ignore
        )
        ds = ds.sel(time=~df_dup)

    # Drop nan values for any other specified parameters
    # if drop_vars is not None:
    if "drop_vars" in pp.keys():
        # This functionality is here so it is run after drop_bogus
        for var in pp["drop_vars"]:
            if var in list(ds.keys()):
                _log.info(f"Dropping points with nan values for {var}")
                num_orig = len(ds.time)
                var_nan = np.isnan(ds[var].values)
                _log.debug(f"depth values: {ds.depth.values[var_nan]}")
                if any(ds.depth.values[var_nan] >= 5):
                    _log.warning(
                        f"Some nan {var} values that will be "
                        + "dropped have a depth >=5",
                    )
                ds = ds.where(~np.isnan(ds[var]), drop=True)
                if (num_orig - len(ds.time)) > 0:
                    _log.info(f"Dropped {num_orig - len(ds.time)} nan {var} values")

    # After dropping timestamps, recalculate distance over ground
    ds = pgutils.get_distance_over_ground(ds)

    # Calculate profiles using measured depth
    # This is required because we need profile_direction for sci/eng
    ds = utils.get_fill_profiles(ds, "time", "depth", **kwargs)

    # If provided, then update the profile indices by joining raw profiles
    if "profile_summary_path" in pp.keys():
        # Join profiles generated using raw timeseries
        prof_summ = pd.read_csv(
            pp["profile_summary_path"],
            parse_dates=["start_time", "end_time"],
        )
        ds = utils.join_profiles(ds, prof_summ, **kwargs)
        depth_var = "depth"
    else:
        # Assuming the raw dataset
        depth_var = "depth_measured"


    # ATTRIBUTES, after dropping vars, etc
    ds = postproc_attrs(ds, pp)

    # Profiles check
    prof_summ= utils.calc_profile_summary(ds, depth_var)
    utils.check_profiles(prof_summ)

    return ds


def postproc_eng_timeseries(ds: xr.Dataset, pp: dict, **kwargs) -> xr.Dataset:
    """
    Engineering timeseries-specific post-processing, including:
        - Removing CTD vars
        - Calculating profiles using depth_measured
        - Updating attributes

    Parameters
    ----------
    ds : str
        Engineering timeseries dataset
    pp: dict
        Dictionary with info needed for post-processing.
        For instance: mode and min_dt

    Returns
    -------
    xarray.Dataset
        post-processed engineering timeseries dataset
    """

    # ds = xr.load_dataset(ds_file)
    _log.debug(f"begin eng postproc: ds has {len(ds.time)} values")

    # Drop CTD variables required or created by binary_to_timeseries,
    # which are not relevant for the engineering NetCDF
    # ds = ds.drop_vars(
    #     [
    #         "depth",
    #         "conductivity",
    #         "temperature",
    #         "pressure",
    #         "salinity",
    #         "potential_density",
    #         "density",
    #         "potential_temperature",
    #     ],
    # )

    # With depth (CTD) gone, rename depth_measured
    if ('depth_measured' in ds):
        ds = ds.rename({"depth_measured": "depth"})

    # General updates
    ds = postproc_general(ds, pp, **kwargs)

    # Reorder data variables
    new_start = ["latitude", "longitude", "depth", "profile_index"]
    ds = utils.data_var_reorder(ds, new_start)

    # Update eng-specific attributes
    eng_comment = "Engineering-only timeseries. "
    if not ds.attrs["comment"].strip():
        ds.attrs["comment"] = eng_comment
    else:
        ds.attrs["comment"] += ". " + eng_comment
    ds.attrs["processing_level"] += (
        " All values have been interpolated via linear fill"
    )

    _log.debug(f"end eng postproc: ds has {len(ds.time)} values")
    # utils.to_netcdf_esd(ds, ds_file)

    return ds


def postproc_sci_timeseries(ds: xr.Dataset, pp: dict, **kwargs) -> xr.Dataset:
    """
    Science timeseries-specific post-processing, including:
        - remove bogus times. Eg, 1970, or before deployment start date
        - Calculating profiles using depth (derived from ctd's pressure)

    Parameters
    ----------
    ds : str
        Science timeseries dataset
    pp: dict
        Dictionary with info needed for post-processing.
        For instance: mode and min_dt

    Returns
    -------
    xarray.Dataset
        post-processed science timeseries dataset
    """

    # ds = xr.load_dataset(ds_file)
    _log.debug("begin sci postproc: ds has %s values", len(ds.time))

    # In case ds is coming from raw_to_timeseries
    if ('depth_ctd' in ds):
        ds = ds.rename({"depth_ctd": "depth"})

    # General updates
    # Drop rows in science where pressure is nan, because:
    #   1) in principle there should be no depth if pressure is nan
    #   2) pyglider does a 'zero screen'
    #   3) nan pressure values all appear to be at the surface,
    #       and often have weird associated values
    # drop_vars is now part of pp
    ds = postproc_general(ds, pp, **kwargs)

    # Science-specific attribute updates
    ds.attrs["processing_level"] += (
        " Science values have been interpolated via linear fill, "
        + f"with a maxgap of {pp["maxgap"]} seconds. "
    )

    # Reorder data variables
    new_start = [
        "latitude",
        "longitude",
        # "depth",
        "profile_index",
        "conductivity",
        "temperature",
        "pressure",
        "salinity",
        "density",
        "potential_temperature",
        "potential_density",
    ]
    new_start[2:2] = sorted([i for i in ds.keys() if "depth" in i]) # type: ignore
    ds = utils.data_var_reorder(ds, new_start)

    _log.debug("end sci postproc: ds has %s values", len(ds.time))
    # utils.to_netcdf_esd(ds, ds_file)

    return ds


def drop_ts_ranges(
    ds,
    drop_list,
    dstype,
    plotdir=None,
    profsummdir=None,
    outname=None,
    **kwargs,
):
    """
    Drop dataset points that are within given time ranges,
    and perform relevant post-processing.

    This function is used within processing scripts, if a certain time range
    has been decided to exclude during review

    Post-processing includes:
    1) Plotting the points that were dropped, if plotdir is not None
    2) Rerunning pgutils.get_distance_over_ground
    3a) Writing new profiles and calculating new profile summary,
        if dstype is "raw", or
    3b) Reading in profile summary from profsummdir, and using summary
        to 'join' profile info to ds using utils.join_profiles
    4) Running utils.check_profiles
    5) Writing to netcdf file, if outnname is not None

    Paramaters
    ----------
    ds : xarray Dataset
        Timeseries dataset
    drop_list : list of string tuples
        A list of string tuples of time ranges to drop from ds.
        These strings will be processed by np.datetime64()
        If dropping a single time, use this value for both values of the tuple
    dstype : str
        String indicating if ds is a raw, eng, or sci timeseries;
        passed to plots.scatter_drop_plot
    plotdir : str | None (default None)
        Path to plot directory; passed to plots.scatter_drop_plot
        If None, then no plots are saved
    profsummdir : str | None (default None)
        Path to profile summary CSV. Ignored if dstype is raw.
        If not None and dstype is eng or sci, will join profiles
    outname : str | None (default None)
        If not None, then ds is written to this path using utils.to_netcdf_esd

    Returns
    -------
    xarray Dataset
        Input ds, with points within specified time ranges dropped.
        Also saves 'dropped' scatter plots to plotdir, if specified.
    """
    _log.info(
        "There are %s points in the original %s dataset", 
        len(ds.time), 
        dstype, 
    )

    # Create the mask framework
    todrop = np.full(len(ds.time), False)

    # For each tuple in drop_list, update todrop array
    for i in drop_list:
        i_todrop = (ds.time.values >= np.datetime64(i[0])) & (
            ds.time.values <= np.datetime64(i[1])
        )
        todrop = todrop | i_todrop
        num_todrop = np.count_nonzero(i_todrop)
        _log.info(f"Dropping {num_todrop} points between {i[0]} and {i[1]}")

    # Make plot
    if plotdir is not None:
        plots.scatter_drop_plot(ds, todrop, dstype, plotdir)

    # Drop time(s)
    todrop_mask = xr.DataArray(todrop, dims="time", coords={"time": ds.time})
    ds = ds.where(~todrop_mask, drop=True)
    _log.info(f"There are now {len(ds.time)} points in the dataset")

    # Distance over ground, if relevant
    if "distance_over_ground" in ds.keys():
        _log.info("Calculating new distance over ground")
        ds = pgutils.get_distance_over_ground(ds)

    # Profiles
    if dstype == "raw" and profsummdir is not None:
        _log.info("Calculating new profiles for raw dataset")
        tsraw = utils.get_fill_profiles(ds, "time", "depth_measured", **kwargs)
        prof_summ = utils.calc_profile_summary(tsraw, "depth_measured")
        prof_summ.to_csv(profsummdir, index=False)
        utils.check_profiles(prof_summ)
    elif profsummdir is not None:
        _log.info("Join-calculating new profiles for eng/sci dataset")
        prof_summ_raw = pd.read_csv(profsummdir, parse_dates=["start_time", "end_time"])
        utils.join_profiles(ds, prof_summ_raw, **kwargs)
        prof_summ = utils.calc_profile_summary(ds, "depth")
        utils.check_profiles(prof_summ)
    else:
        _log.info("No profile work")

    # Write to netcdf
    if outname is not None:
        utils.to_netcdf_esd(ds, outname)

    return ds


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
    pp={},
    **kwargs,
):
    """
    Extract raw, unprocessed glider data using dbdreader.
    Adaptation of pyglider.slocum.binary_to_timeseries
    dbdreader only deals with flight and science computers,
    hence only classifying variables as 'eng' or 'sci'

    the dbdreader MultiDBD.get() method is used,
    rather than get_sync, to read the parameters specified in
    deploymentyaml. The argument return_nans (of MultiDBD.get()) is set to
    True, so that there are two 'time bases' for the extracted data: one
    for engineering variables (from m_present_time), and one for science
    variables (from sci_m_present_time). These times are merged,
    and these values are the time index of the output file.

    No values are interpolated.
    Times less than the yaml fil's 'deployment_min_dt' are still dropped.

    pp is the ESD post-process dictionary
    kwargs is passed to utils.findProfiles
    """

    if not have_dbdreader:
        raise ImportError("Cannot import dbdreader")

    # Read and parse deployment yaml(s)
    deployment = pgutils._get_deployment(deploymentyaml)

    # NetCDF vars: 
    # Loop through deploymentyaml files, and append all new netcdf vars.
    # This chunk maintains the key from the first time it sees it.
    # This distinction from pgutils._get_deployment 
    # allows us to 'concatenate' netcdf vars for this raw dataset
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

    thenames = list(ncvar.keys())
    thenames.remove("time")

    # build a new data set based on info in `deployment.`
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
        raise ValueError("There are more than 2 time bases, which will break this")
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
            ValueError(f"{sensorname} not in sci or eng parameter names")

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
    min_dt_str = deployment["metadata"]["deployment_min_dt"]
    ds = utils.drop_bogus_times(ds, min_dt=min_dt_str, max_drop=True)
    # ds = ds.where(ds.time >= np.datetime64(min_dt_str), drop=True)
    ds["time"].attrs = attr

    # Drop rows with nan values across all data variables
    ds = ds.dropna("time", how="all")
    _log.info("The raw timeseries has %s data points", ds.time.shape[0])

    # Depth calculation #, and name management
    ds = pgutils.get_glider_depth(ds).rename({"depth": "depth_ctd"})
    # ds = ds.rename({"depth_measured": "depth"})

    # Only keep depth_ctd values where pressure is not nan
    # TODO: is this using mean lat for everything??
    ds["depth_ctd"] = ds["depth_ctd"].where(~np.isnan(ds["pressure"]))

    # Calculate profiles and distance_over_ground
    ds = utils.get_fill_profiles(ds, "time", "depth_measured", **kwargs)
    ds = pgutils.get_distance_over_ground(ds)

    new_start = [
        "latitude",
        "longitude",
        "depth_measured",
        "depth_ctd",
        "profile_index",
        "profile_direction",
    ]
    ds = utils.data_var_reorder(ds, new_start)

    # Add metadata - using postproc_attrs for consistency
    pp["metadata_dict"] = deployment["metadata"]
    pp["device_dict"] = deployment["glider_devices"]
    ds = postproc_attrs(ds, pp)

    outname = outdir + "/" + ds.attrs["deployment_name"] + fnamesuffix + ".nc"
    _log.info("writing %s", outname)
    utils.to_netcdf_esd(ds, outname)

    return outname


def timeseries_raw_to_sci(
    inname, 
    outdir, 
    deploymentyaml, 
    *,
    fnamesuffix="", 
    maxgap=300, 
    pp: dict, 
    **kwargs,
):
    """
    Go from raw timeseries (from esdglider.glider.binary_to_raw)
    to processed science timeseries. 
    This function can be used in cases where different science sensors are
    on at different times, e.g. PAM deployments, and thus it is not possible 
    to get the full science timeseries using dbdreader.get_sync.

    Other than not using get_sync, this function closely follows the
    pyglider.slocum.binary_to_timeseries

    Parameters
    ----------


    Returns
    -------
    """
    
    ds = xr.open_dataset(inname, decode_times=True)

    # Read and parse deployment yaml(s), to get variables
    deployment = pgutils._get_deployment(deploymentyaml)
    ncvar = deployment["netcdf_variables"]
    [ncvar[i]["source"] for i in ncvar]

    vars_tokeep = [i for i in ncvar.keys() if (i in ds.keys() and i != "time")]
    vars_tokeep[2:2] = [
        "depth_measured", "depth_ctd", 
        "profile_index", "profile_direction"
    ]
    vars_sci = (
        [i for i in ncvar if "sci" in ncvar[i]["source"] and i != "time"]
        + ["depth_ctd"]
    )

    # Use deployment yaml(s) to specify the variables to keep and interpolate
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
            _log.debug('number of gaps %s', np.count_nonzero(tg_ind))

        # Update ds object with values and attributes
        ds[i].values = val_interp
        ds[i].attrs["method"] = "linear fill"
        #The var already has the yaml-specified attributes from binary_to_raw

    # Drop rows where all science vars are nan
    _log.info(
        "Dropping datapoints that have nan values for all of these vars: %s", 
        ", ".join([str(i) for i in vars_sci]), 
    )
    # vars_ignore = list(set(vars_tokeep).difference(vars_sci))
    # vars_ignore = list(set(vars_tokeep).intersection([
    #     "depth", "profile_index", "profile_direction", "latitude", "longitude", 
    #     "heading", "pitch", "roll", 
    #     "water_velocity_eastward", "water_velocity_northward", 
    # ]))
    # vars_sci = list(set(vars_tokeep).difference(set(vars_ignore)))
    ds = ds.dropna(dim="time", how="all", subset=vars_sci)

    # Not necessary with subset arg
    # ds = xr.combine_by_coords(
    #     [ds[vars_ignore], ds_sci], join="inner", combine_attrs ="identical", 
    # )
    # ds = utils.data_var_reorder(ds, vars_tokeep)



    if ('temperature' in ds) and ('conductivity' in ds) and ('pressure' in ds):
        ds = pgutils.get_derived_eos_raw(ds)

    # Perform ESD-specific post-processing    
    _log.info(f"Post-processing science timeseries")
    ds = postproc_sci_timeseries(ds, pp, **kwargs)

    # if dstype == "eng":
    #     _log.info(f"Post-processing engineering timeseries")
    #     ds = postproc_eng_timeseries(ds, pp, **kwargs)
    # elif dstype == "sci":    
    #     _log.info(f"Post-processing science timeseries")
    #     ds = postproc_sci_timeseries(ds, pp, **kwargs)
    # else:
    #     _log.error("dstype %s", dstype)
    #     raise ValueError("dstype must be either 'sci' or 'eng'")

    # Write out to file
    outname = f"{outdir}/{ds.attrs['deployment_name'] + fnamesuffix}.nc"
    _log.info('writing %s', outname)
    utils.to_netcdf_esd(ds, outname)

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


def grid_esd(inname, paths):
    """
    Parameters
    ----------
    inname : str or Path
        netcdf file to break into profiles. 
        Passed directly to inname argument of pyglider.ncprocess.make_gridfiles
    paths : dict
        A dictionary of file/directory paths for various processing steps.
        Intended to be the output of esdglider.glider.get_path_deployment()
        See this function for the expected key/value pairs

    Returns
    -------
    list of strings
        A list of the generated gridded datasets, i.e.
        a list of the output(s) from pyglider.ncprocess.make_gridfiles
    """

    outnames = []
    for i in bin_size:        
        _log.info("Generating %sm gridded data", i)
        outname_gr = pgncprocess.make_gridfiles(
            inname,
            paths["griddir"], 
            paths["deploymentyaml"],
            depth_bins=np.arange(0, depth_max, i),
            fnamesuffix=f"-{paths["mode"]}-{i}m",
            exclude_vars=gridded_exclude_vars,
        )
        outnames.append(outname_gr)

    return outnames


def make_gridfiles_depth_measured(paths):
    """
    Make gridfiles using the measured depth. This function will be used
    if for instance the CTD was turned off during parts of a deployment, and 
    thus the depth calculated from the CTD does not span the full timeseries.

    A temporary nc file is written, to pass to 
    pyglider.ncprocess.make_gridfiles. The science dataset is not altered.

    Parameters
    ----------
    paths : dict
        A dictionary of file/directory paths for various processing steps.
        Intended to be the output of esdglider.glider.get_path_deployment()
        See this function for the expected key/value pairs

    Returns
    -------
    The output of grid_esd
    """

    _log.info("Generating gridded files using measured_depth")
    outname_tssci = paths["tsscipath"]
    outname_gr1m = paths["gr1path"]
    outname_gr5m = paths["gr5path"]
    
    utils.remove_file(outname_gr1m)
    utils.remove_file(outname_gr5m)
    if not os.path.isfile(outname_tssci):
        raise FileNotFoundError(f"Could not find {outname_tssci}")

    _log.debug("Excluded vars: %s", ", ".join(gridded_exclude_vars))
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Creating temporary science dataset with measured depth as depth
        temp_file = os.path.join(temp_dir, os.path.basename(outname_tssci))
        _log.debug("temp_file %s", temp_file)
        ds_sci_tmp = xr.load_dataset(outname_tssci)
        ds_sci_tmp = (
            ds_sci_tmp
            .drop_vars(["depth"])
            .rename({"depth_measured": "depth"})
        )
        
        # Add a comment that the bins were created useing depth_measured
        tmp_comment = (
            "Glider data was gridded using the glider measured depth (m_depth)"
        )
        if not ds_sci_tmp.attrs["comment"].strip():
            ds_sci_tmp.attrs["comment"] = tmp_comment
        else:
            ds_sci_tmp.attrs["comment"] += ". " + tmp_comment
        utils.to_netcdf_esd(ds_sci_tmp, temp_file)

        outnames = grid_esd(temp_file, paths=paths)
    return outnames
