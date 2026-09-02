"""
Slocum pipeline functions, for ESD-specific processing of slocum glider files
"""

import logging
import math
import os
import tempfile
from importlib import metadata

import dbdreader
import numpy as np
import pandas as pd
import pyglider.ncprocess as pgncprocess
import pyglider.slocum as pgslocum
import pyglider.utils as pgutils
import xarray as xr
import yaml

import esdglider.profiles as prof
from esdglider import paths, plots, qartod, utils
from esdglider.slocum import core
from esdglider.slocum.core import time_encoding

_log = logging.getLogger(__name__)


"""
Glider values for ESD slocum pipeline
Note that depth bins will be defined by `np.arange(0, depth_max, i)`,
where i is in element of bin_size

bin_size: list
    a list of the gridded depth bin sizes
depth_max: float
    The maximum value to use when making depth bins
maxgap_esd : float
    The maximum allowed gap (in seconds) for ESD processing.
"""

bin_size = [1, 5]
depth_max = 1200.1
maxgap_esd = 60


def generate_timeseries(
    deployment_name: str, 
    mode: str, 
    glider_paths: dict,
    *,
    write_raw: bool = True,
    write_eng: bool = True,
    write_sci: bool = True,
    sci_use_m_depth: bool = False,
    run_qc: bool = False,
    file_info: str | None = None,
    binary_search: str | None = None,
    maxgap: int | None = None,
    prof_args: dict | None = None,
) -> dict:
    """
    Generate timeseries netCDF files for the slocum glider deployment.

    Parameters
    ----------
    deployment_name : str
        The name of the glider deployment.
    mode : str
        The mode of operation, either 'rt' (real-time) or 'delayed'.
    glider_paths : dict
        A dictionary containing paths relevant to the glider deployment.
    write_raw : bool, optional
        Whether to write raw timeseries files, by default True.
    write_eng : bool, optional
        Whether to write engineering timeseries files, by default True.
    write_sci : bool, optional
        Whether to write science timeseries files, by default True.
    sci_use_m_depth : bool, optional
        Whether the science timeseries should use the glider measured depth 
        (i.e., "depth_measured" from source "m_depth") as its depth value (True), 
        or use the CTD-calculated depth (i.e., "depth_ctd" from source "pressure";
        False, default). 
        In practice, this also specifies whether to use pyglider's 
        binary_to_timeseries to generate the science timeseries (False), 
        or interpolate the science timeseries from the raw timeseries (True)
        Note that this variable does not affect 
        which depth variable is used to calculate profiles. 
        For esdglider, this is always "depth_measured" (from m_depth)
    run_qc : bool, optional
        Whether to run qartod check on science timeseries, by default False.
        Only relevant if write_sci is True.
    file_info : str | None, optional
        Information about the processing file, by default None.
        Will be included in the history attribute of the output netCDF files.
    binary_search : str | None, optional
        The search pattern for binary files.
        If None (default), will default to all uncompressed binary files
    maxgap : int | None, optional
        The maximum allowed gap (in seconds) for interpolation. 
        If None (default), will use the module's maxgap_esd value.
    prof_args : dict | None, optional
        named optional arguments, passed to esdglider.profiles.findProfiles

    Returns
    -------
    dict
        A dictionary containing the paths to the timeseries files.
    """

    deploymentyaml = glider_paths["deploymentyaml"]
    rawdir = glider_paths["rawdir"]
    tsdir = glider_paths["tsdir"]
    ancdir = glider_paths["ancillarydir"]

    deployment = pgutils._get_deployment(deploymentyaml)

    # Check mode, set binary_search regex. Use uncompressed by default
    if mode == "delayed":
        if binary_search is None:
            binary_search = "*.[DEde][Bb][Dd]"
    elif mode == "rt":
        if binary_search is None:
            binary_search = "*.[STst][Bb][Dd]"
    else:
        raise ValueError("mode must be either 'rt' or 'delayed'")

    # Set defaults
    if maxgap is None:
        maxgap = maxgap_esd

    if prof_args is None:
        prof_args = {}

    # if use_m_depth:
    prof_depth_var = "m_depth"
    other_depth_var = "depth_ctd"
    # else:
    #     prof_depth_var = "depth_ctd"
    #     other_depth_var = "depth_measured"

    # Check which dbdreader backend is being used
    if write_raw or write_eng or write_sci:
        utils.check_dbdreader_c_extension()

    # --------------------------------------------
    # Raw
    outname_tsraw  = glider_paths["tsrawpath"]
    outname_tseng  = glider_paths["tsengpath"]
    outname_tssci  = glider_paths["tsscipath"]
    outname_gr1m   = glider_paths["gr1path"]
    outname_gr5m   = glider_paths["gr5path"]
    prof_summ_path = glider_paths["profsummpath"]

    if write_raw:
        # utils.remove_file(outname_tsraw)
        # utils.remove_file(outname_tseng)
        # utils.remove_file(outname_tssci)
        # utils.remove_file(outname_gr1m)
        # utils.remove_file(outname_gr5m)
        # utils.remove_file(prof_summ_path)
        utils.rmtree(glider_paths["outdir"]) #raw: remove all
        utils.makedirs_pass(rawdir)
        utils.makedirs_pass(ancdir)

        _log.info("Generating raw nc")
        raw_yaml_list = [
            deploymentyaml, 
            paths.get_path_yaml_deployment_vars("raw")
        ]
        # # Create raw Netcdf yaml list
        # raw_netcdf_yaml_list = [
        #     deploymentyaml, 
        #     paths.get_path_yaml_deployment_vars("eng"), 
        #     paths.get_path_yaml_deployment_vars("raw")
        # ]
        
        # i_solocam = ["instrument_shadowgraph", "instrument_glidercam"]
        # if any(i in deployment["glider_devices"] for i in i_solocam):
        #     raw_netcdf_yaml_list.append(paths.get_path_yaml_deployment_vars("raw-solocam"))

        # i_flbbcd = ["instrument_flbbcd"]
        # if any(i in deployment["glider_devices"] for i in i_flbbcd):
        #     raw_netcdf_yaml_list.append(paths.get_path_yaml_deployment_vars("raw-flbbcd"))
        # _log.debug("Raw YAML list: %s", raw_netcdf_yaml_list)

        # deployment_raw = pgutils._get_deployment(raw_yaml_list)
        # deployment_raw["netcdf_variables"] = utils._get_deployment_netcdfvars(raw_netcdf_yaml_list)

        # with tempfile.TemporaryDirectory() as temp_dir:
        #     temp_file = os.path.join(temp_dir, "temp.yml")
        #     with open(temp_file, "w", encoding="utf-8") as f:
        #         yaml.dump(deployment_raw, f, sort_keys=False, default_flow_style=False)

        outname_tsraw = core.binary_to_raw_timeseries(
            glider_paths["binarydir"],
            glider_paths["cacdir"],
            rawdir,
            raw_yaml_list, 
            search=binary_search,
            include_source=True,
            fnamesuffix=f"-{mode}-raw",
            prof_depth_var=prof_depth_var,
            prof_args=prof_args,
        )

        # Run postprocessing
        _log.info(f"Post-processing raw timeseries: {outname_tsraw}")
        tsraw = xr.load_dataset(outname_tsraw)

        # tsraw = tsraw.reset_coords(["latitude", "longitude"])

        # new_start = [
        #     "profile_index",
        #     "profile_direction",
        #     "depth_measured",
        #     "depth_ctd",
        # ]
        # tsraw = utils.data_var_reorder(tsraw, new_start)

        tsraw = postproc_attrs(
            tsraw, 
            mode, 
            file_info=file_info,
        )
        tsraw.attrs["comment"] = utils.append_string(
            tsraw.attrs["comment"], 
            (
                "The variable names for this raw dataset are the glider "
                + "sensor names. See the relevant masterdata file "
                + "for sensor name details"
            ), 
        )

        tsraw.to_netcdf(
            outname_tsraw, 
            mode="w", 
            encoding={'time': time_encoding}
        )

        # Save profile summary, get profile index attributes
        _log.info("Writing profile summary CSV to %s", prof_summ_path)
        prof_summ = prof.calc_profile_summary(tsraw, prof_depth_var)
        prof_summ.to_csv(prof_summ_path, index=False)
        num_dives = np.count_nonzero(prof_summ.profile_direction.values == 1)
        _log.info("Deployment %s performed %s dives", deployment_name, num_dives)

        prof_index_attrs = tsraw["profile_index"].attrs

        # Profile and depth sanity checks
        _log.info("raw timeseries checks")
        _log.info("Running profile checks on profile depth var, '%s'", prof_depth_var)
        prof.check_profiles(prof_summ)

        _log.info("Running profile checks on other depth var, '%s'", other_depth_var)
        prof_summ2 = prof.calc_profile_summary(tsraw, other_depth_var)
        prof.check_profiles(prof_summ2)

        utils.check_depth(tsraw["m_depth"], tsraw["depth_ctd"])

    else:
        _log.info("Not writing raw nc. Looking for pre-existing files")
        # Get profile summary
        prof_summ_path = glider_paths["profsummpath"]
        try: 
            prof_summ = pd.read_csv(
                glider_paths["profsummpath"],
                parse_dates=["start_time", "end_time"],
            )
        except FileNotFoundError:
            _log.error("Profile summary CSV file not found: %s", prof_summ_path)
            raise FileNotFoundError(f"File not found: {prof_summ_path}")

        # Get profile index attributes
        try:
            with xr.open_dataset(outname_tsraw) as tsraw:
                _log.debug("Opened existing raw nc file: %s", outname_tsraw)
                prof_index_attrs = tsraw["profile_index"].attrs
        except FileNotFoundError:
            _log.error("The raw nc file not found: %s", outname_tsraw)
            raise FileNotFoundError(f"File not found: {outname_tsraw}")
    # --------------------------------------------
    # Eng Timeseries

    if write_eng:
        # Delete previous files before starting run
        utils.remove_file(outname_tseng)
        utils.makedirs_pass(tsdir)

        # Engineering - uses m_depth as time base
        _log.info("Generating engineering timeseries")
        outname_tseng = pgslocum.binary_to_timeseries(
            glider_paths["binarydir"],
            glider_paths["cacdir"],
            tsdir,
            [deploymentyaml, paths.get_path_yaml_deployment_vars("eng")],
            search=binary_search,
            fnamesuffix=f"-{mode}-eng",
            time_base="m_depth",
            profile_filt_time=None,  # type: ignore
            maxgap=maxgap_esd,
        )

        _log.info(f"Post-processing engineering timeseries: {outname_tseng}")
        tseng = xr.load_dataset(outname_tseng)
        tseng = postproc_tsl1_eng(
            tseng, 
            mode, 
            maxgap, 
            # deployment_start=deployment_start,
            # deployment_end=deployment_end,
            file_info=file_info,
            prof_summ=prof_summ,
            # prof_summ_path=glider_paths["profsummpath"],
            prof_index_attrs=prof_index_attrs,
            # prof_depth_var=prof_depth_var,
            # prof_args=prof_args
        )
        pgutils._save_dataset(
            tseng,
            outname_tseng, 
            deployment, 
            mode='w',
            encoding={'time': time_encoding},
        )
        del tseng

    # --------------------------------------------
    # Sci Timeseries
    if write_sci:
        # Since gridded depend on ts, also delete gridded
        utils.remove_file(outname_tssci)
        # utils.remove_file(outname_gr1m)
        # utils.remove_file(outname_gr5m)
        
        utils.rmtree(glider_paths["griddir"]) #sci: remove gridded
        utils.makedirs_pass(tsdir)

        if sci_use_m_depth: 
            _log.info("Generating science timeseries, via raw_to_sci_timeseries")
            outname_tssci = core.raw_to_sci_timeseries(
                outname_tsraw,
                tsdir,
                deploymentyaml,
                fnamesuffix=f"-{mode}-sci",
                maxgap=maxgap_esd,
            )
            drop_vars = None

        else:            
            time_base_var = "sci_water_pressure"
            _log.info(
                "Generating science timeseries, "
                + "via pyglider with %s as time_base sensor", 
                time_base_var
            )
            outname_tssci = pgslocum.binary_to_timeseries(
                glider_paths["binarydir"],
                glider_paths["cacdir"],
                tsdir,
                deploymentyaml,
                search=binary_search,
                fnamesuffix=f"-{mode}-sci",
                time_base=time_base_var,
                profile_filt_time=None,  # type: ignore
                maxgap=maxgap_esd,
            )
            drop_vars = ["pressure"]

        _log.info("Post-processing science timeseries: %s", outname_tssci)
        tssci = xr.load_dataset(outname_tssci)
        tssci = postproc_tsl1_sci(
            tssci, 
            mode, 
            maxgap, 
            sci_use_m_depth=sci_use_m_depth,
            file_info=file_info,
            drop_vars=drop_vars, 
            prof_summ=prof_summ,
            prof_index_attrs=prof_index_attrs,
        )
        pgutils._save_dataset(
            tssci,
            outname_tssci, 
            deployment, 
            mode='w',
            encoding={'time': time_encoding},
        )        
        del tssci

        if run_qc:
            _log.debug("Creating qc variables for science netCDF files")
            qartod.run_qartod_qc(
                input_file=outname_tssci,
                output_file=outname_tssci,
                overwrite_qc=True
            )
            _log.info("Completed QARTOD QC workflow for science timeseries")



    if write_eng or write_sci:
        _log.info("final eng/sci timeseries checks")
        tseng = xr.load_dataset(outname_tseng)
        tssci = xr.load_dataset(outname_tssci)

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
            _log.warning(
                "Min idx for eng / sci: %d / %d", 
                tseng.profile_index.values.min(), tssci.profile_index.values.min()
            )
            _log.warning(
                "Max idx for eng / sci: %d / %d", 
                tseng.profile_index.values.max(), tssci.profile_index.values.max()
            )
        else:
            _log.info("The eng and sci timeseries have the same functional profiles")

    else:
        _log.info("Not writing timeseries nc")

    # --------------------------------------------
    if write_sci:
        _log.info("Checking flbbcd autoexec values, and cdom status")
        check_flbbcd_autoexec(
            glider_paths["binarydir"], 
            glider_paths["cacdir"], 
            deploymentyaml,
            search=binary_search,
        )

        tssci = xr.load_dataset(outname_tssci)
        utils.check_cdom_date(tssci) #cdom_status = 
            
        _log.info("Done checks for flbbcd autoexec values and cdom status")

    # --------------------------------------------
    return {
        "outname_tsraw": outname_tsraw,
        "outname_tseng": outname_tseng,
        "outname_tssci": outname_tssci,
    }


def postproc_attrs(
        ds: xr.Dataset, 
        mode: str, 
        *, 
        file_info: str | None = None,
    ) -> xr.Dataset:
    """
    Update attributes of xarray Dataset ds, including:
        - running pyglider's utils.fill_metadata
        - determining glider ID. The datetime is extracted from either 
          'deployment_min_dt' attribute if it exists, 
          or the first glider timestamp
        - setting 'title' as equivalent to 'id'
        - setting other ESD-specific attributes (e.g., license, file, history)

    Used for all of eng, sci, and raw timeseries

    Parameters
    ----------
    ds : xarray.Dataset
        The dataset to update attributes for.
    mode : str
        Deployment mode, either 'rt' or 'delayed'
    file_info : str | None, optional
        Information about the processing file, by default None.

    Returns
    -------
    xr.Dataset
        ds, with updated attributes
    """

    # Rerun pyglider metadata functions, now that drop_bogus has been run,
    # for the sake of times
    # metadata and device info have already been added, so not needed here
    ds = pgutils.fill_metadata(ds, {}, {})

    # Determine the glider ID using min_dt, and check vs ID from time
    time_str = ds.time.values[0].astype("datetime64[s]").item().strftime("%Y%m%dT%H%M")
    if "deployment_min_dt" in ds.attrs:
        min_dt64 = np.datetime64(ds.deployment_min_dt)
        min_dt_str = min_dt64.item().strftime("%Y%m%dT%H%M")
        if min_dt_str != time_str:
            _log.warning(
                "The dataset ID generated from the metadata (%s) "
                + "is different from that generated from the time (%s)."
                + "Using the ID from the metadata",
                min_dt_str,
                time_str,
            )
    else:
        _log.info(
            "There is no deployment_min_dt attribute in the dataset. "
            + "Using the first time value for the ID."
        )
        min_dt_str = time_str
        
    ds.attrs["id"] = f"{ds.attrs['glider_name']}-{min_dt_str}"

    # Other ESD-specific updates
    # ds.attrs["id"] = utils.get_file_id_esd(ds)
    ds.attrs["title"] = ds.attrs["id"]
    ds.attrs["license"] = (
        "This data may be redistributed and used without restriction.  "
        + "Data provided as is with no expressed or implied assurance "
        + "of quality assurance or quality control"
    )
    
    if file_info is None:
        file_info = "netCDF files created using"
    ds.attrs["history"] = f"{utils.datetime_now_utc()}: {file_info}: " + "; ".join(
        [
            f"deployment_name={ds.deployment_name}",
            f"mode={mode}",
            f"dbdreader v{metadata.version('dbdreader')}",
            f"pyglider v{metadata.version('pyglider')}",
            f"esdglider v{metadata.version('esdglider')}",
        ],
    )

    return ds


def postproc_tsl1(
    ds: xr.Dataset,
    mode: str,
    maxgap: int, 
    *, 
    file_info: str | None = None,
    drop_vars: list | None = None,
    prof_summ: pd.DataFrame | None = None,
    prof_index_attrs: dict | None = None,
) -> xr.Dataset:
    """
    Post-processing steps shared by both the L1 timeseries (sci and eng):
        - dropping bogus times, meaning times:
            - before 1970-01-01
            - before the deployment start (if specified via attr 'deployment_min_dt')
            - after the current time
        - dropping bogus values (utils.drop_bogus)
        - dropping data 'rows' where a variable specified in 'drop_vars'
          contains NaN values
        - updating dataset distance_over_ground
        - updating the profile indices from the profile summary CSV file (if 
          prof_summ or prof_index_attrs is None, then profiles will not be joined)
        - running postproc_attrs
        - updating 'processing_level' attribute

    Returns the Dataset ds with updated values and attributes

    Parameters
    ----------    
    ds : xarray.Dataset
        L1 (eng or sci) timeseries dataset
    mode : str
        Deployment mode, either 'rt' or 'delayed'
    maxgap : int
        The maximum allowed gap (in seconds) for interpolation.
    file_info : str | None, optional
        Information about the processing file, by default None.
    drop_vars : list | None, optional
        List of variables for which to drop the whole timestamp 
        if they contain NaN values, by default None
    prof_summ : pd.DataFrame | None, optional
        Profile summary DataFrame, by default None
    prof_index_attrs : dict | None, optional
        Profile index attributes, by default None

    Returns
    -------
    xarray.Dataset
        post-processed L1 timeseries dataset
    """

    # DROP BOGUS VALUES
    # Remove times that are nan / <min_dt / >current time, and drop other bogus values
    _log.info("The given timeseries has %s data points", ds.time.shape[0])
    if "deployment_min_dt" in ds.attrs:
        min_dt = ds.deployment_min_dt
    else:
        min_dt = "1970-01-01"
    ds = utils.drop_bogus(ds, min_dt=min_dt, max_drop=True)

    # Check for and verbosely remove any duplicated timestamps
    ds_index = ds.get_index("time")
    if ds_index.duplicated().any():
        df_dup = ds_index.duplicated()
        _log.warning(
            "There are %d duplicated timestamps in the current dataset. "
            + "The second of the duplicated timestamps will be dropped. "
            + "Indexes, of the original dataset: %s",
            df_dup.sum(),
            ", ".join([str(i[0]) for i in np.argwhere(df_dup)]),  # type: ignore
        )
        ds = ds.sel(time=~df_dup)

    # Drop nan values for any other specified parameters
    if drop_vars is not None:
        # This functionality is here so it is run after drop_bogus
        for var in drop_vars:
            if var in list(ds.keys()):
                _log.info(f"Dropping points with nan values for {var}")
                num_orig = len(ds.time)
                var_nan = np.isnan(ds[var].values)
                _log.debug(f"depth values: {ds.depth.values[var_nan]}")
                if any(ds.depth.values[var_nan] >= 5):
                    _log.warning(
                        "Some nan %s values that will be "
                        + "dropped have a depth >=5",
                        var
                    )
                ds = ds.where(~np.isnan(ds[var]), drop=True)
                num_dropped_values = num_orig - len(ds.time)
                if num_dropped_values > 0:
                    _log.info("Dropped %d nan %s values", num_dropped_values, var)

    # VALUES: RECALCULATE
    # After dropping bogus timestamps, recalculate distance over ground
    ds = pgutils.get_distance_over_ground(ds)

    # PROFILES
    # Update the profile indices from the profile summary CSV file
    if prof_summ is not None and prof_index_attrs is not None:
        _log.info("Join profiles, from raw timeseries, by time windows")
        prof_index_attrs["sources"] += ", from raw dataset"
        ds = prof.join_profiles(ds, prof_summ, prof_index_attrs)        
    else:
        _log.debug("Profile info not provided - skipping profiles")

    # ATTRIBUTES
    ds = postproc_attrs(ds, mode, file_info=file_info)

    # Update attribute specific to eng and sci timeseries
    ds.attrs["processing_level"] = (
        "Values have been interpolated via linear fill, "
        + f"with a maxgap of {maxgap} seconds. "
        + "Minimal data screening."
    )

    return ds


def postproc_tsl1_eng(
    ds: xr.Dataset,
    mode: str, 
    maxgap: int, 
    *, 
    file_info: str | None = None,
    prof_summ: pd.DataFrame | None = None,
    prof_index_attrs: dict | None = None,
) -> xr.Dataset:
    """
    Engineering timeseries-specific post-processing, including:
        - Renames depth_measured to depth
        - Calls postproc_tsl1
        - Updates dataset's comment attribute: adds text "Engineering-only timeseries"

    Parameters
    ----------
    ds : xarray.dataset
        Engineering timeseries dataset
    mode : str
        Deployment mode, either 'rt' or 'delayed'
    maxgap : int
        The maximum allowed gap (in seconds) for interpolation.
    file_info : str | None, optional
        Information about the processing file, by default None
    prof_summ : pd.DataFrame | None, optional
        Profile summary DataFrame, by default None
    prof_index_attrs : dict | None, optional
        Profile index attributes, by default None

    Returns
    -------
    xarray.Dataset
        post-processed engineering timeseries dataset
    """

    _log.debug("begin eng postproc: ds has %d values", len(ds.time))

    # Rename to depth
    try:
        ds = ds.rename({"depth_measured": "depth"})
    except ValueError:
        _log.error("depth_measured not found in engineering dataset")
        raise ValueError("depth_measured not found in engineering dataset")

    # # Get profile_direction from m_depth
    # ds = prof.get_fill_profiles(ds, "time", "depth_measured", prof_args)
    # ds = ds.drop_vars("profile_index")

    # General updates
    ds = postproc_tsl1(
        ds=ds, 
        mode=mode, 
        maxgap=maxgap, 
        file_info=file_info,
        prof_summ=prof_summ,
        prof_index_attrs=prof_index_attrs,
    )

    # Update eng-specific attributes
    eng_comment = "Engineering-only timeseries"
    ds.attrs["comment"] = utils.append_string(ds.attrs["comment"], eng_comment)

    _log.debug("end eng postproc: ds has %d values", len(ds.time))

    return ds


def postproc_tsl1_sci(
        ds: xr.Dataset, 
        mode: str,
        maxgap: int, 
        sci_use_m_depth: bool,
        *,
        file_info: str | None = None,
        drop_vars: list | None = None,
        prof_summ: pd.DataFrame | None = None,
        prof_index_attrs: dict | None = None,
    ) -> xr.Dataset:
    """
    Science timeseries-specific post-processing, including:
        - if sci_use_m_depth is True, try to rename depth_measured to depth
        - calculate variable 'profile_direction' from variable 'depth'
        - run postproc_tsl1

    Parameters
    ----------
    ds : xarray.Dataset
        Science timeseries dataset
    mode : str
        Deployment mode, either 'rt' or 'delayed'
    maxgap : int
        The maximum allowed gap (in seconds) for interpolation.
    file_info : str | None, optional
        Information about the processing file, by default None.
    drop_vars : list | None, optional
        List of variables for which to drop the whole timestamp 
        if they contain NaN values, by default None
    sci_use_m_depth : bool, optional
        If True, then tries to rename the variable 'depth_measured'
        to 'depth'. Passed directly from generate_timeseries
    prof_summ : pd.DataFrame | None, optional
        Profile summary DataFrame, by default None
    prof_index_attrs : dict | None, optional
        Profile index attributes, by default None

    Returns
    -------
    xarray.Dataset
        post-processed science timeseries dataset
    """

    # ds = xr.load_dataset(ds_file)
    _log.debug("begin sci postproc: ds has %d values", len(ds.time))

    # # Get profile_direction from specified depth field
    # if prof_depth_var is None:
    #     raise ValueError("prof_depth_var must be specified for profile processing")

    # If using measured depth, rename it to 'depth' for consistency
    try:
        if sci_use_m_depth:
            ds = ds.rename({"depth_measured": "depth"})
    except KeyError:
        _log.warning(
            "depth_measured not found in dataset, cannot rename to depth. "
            + "This function will likely fail."
        )

    ds = prof.get_fill_profiles(ds, "time", "depth")
    ds = ds.drop_vars("profile_index")

    # General updates
    # Drop rows in science where pressure is nan, because:
    #   1) in principle there should be no depth if pressure is nan
    #   2) pyglider does a 'zero screen'
    #   3) nan pressure values all appear to be at the surface,
    #       and often have weird associated values
    ds = postproc_tsl1(
        ds=ds,
        mode=mode,
        maxgap=maxgap,
        file_info=file_info,
        drop_vars=drop_vars,
        prof_summ=prof_summ,
        prof_index_attrs=prof_index_attrs,
    )

    _log.debug("end sci postproc: ds has %s values", len(ds.time))

    return ds


def generate_gridded(
    glider_paths: dict,
    write_gridded: bool = True,
    # use_m_depth: bool = False,
    # raw_to_sci: bool = False,
) -> dict:
    """
    Generate gridded netCDF files for the slocum glider deployment.
    
    Parameters
    ----------
    glider_paths : dict
        A dictionary containing paths relevant to the glider deployment.
    write_gridded : bool, optional
        Whether to write gridded netCDF files, by default True.
    use_m_depth : bool, optional
        If True, grid using the glider's measured depth ('depth_measured')
        instead of the CTD-calculated 'depth'. Default is False.
    raw_to_sci : bool, optional
        Deprecated fallback alias for `use_m_depth`.
    
    Returns
    -------
    dict
        A dictionary containing the paths to the gridded netCDF files.
    """
    
    outname_tssci = glider_paths["tsscipath"]
    if bin_size != [1, 5]:
        _log.warning(
            "The bin_size variable is not the default [1, 5]. "
            "The output paths may be different than expected."
        )

    if write_gridded:
        if not os.path.isfile(outname_tssci):
            raise FileNotFoundError(f"Could not find {outname_tssci}")
        utils.rmtree(glider_paths["griddir"])

        # if use_m_depth:
        #     _log.info("Gridding science data using glider measured depth (depth_measured)")
        #     with tempfile.TemporaryDirectory() as temp_dir:
        #         temp_file = os.path.join(temp_dir, os.path.basename(outname_tssci))
        #         _log.debug("Creating temporary science dataset with measured depth as depth: %s", temp_file)
                
        #         with xr.open_dataset(outname_tssci) as ds_sci:
        #             ds_sci_tmp = (
        #                 ds_sci.drop_vars(["depth"])
        #                 .rename({"depth_measured": "depth"})
        #             )
        #             # Add a comment that the bins were created using depth_measured
        #             tmp_comment = "Glider data was gridded using the glider measured depth (depth_measured)"
        #             ds_sci_tmp.attrs["comment"] = utils.append_string(
        #                 ds_sci_tmp.attrs.get("comment", ""), tmp_comment)
        #             ds_sci_tmp.to_netcdf(temp_file, encoding={'time': time_encoding})
                
        #         outnames = _run_pyglider_gridding(temp_file, glider_paths)
        # else:
        _log.info("Gridding science data using CTD-calculated depth")
        outnames = _run_pyglider_gridding(outname_tssci, glider_paths)
        _log.debug("gridded outnames %s", "; ".join(outnames))

    else:
        _log.info("Not writing gridded nc")
        keys_to_return = [f"outname_gr{i}m" for i in bin_size]
        keys_to_extract = [f"gr{i}path" for i in bin_size]
        outnames = {}
        for kr, ke in zip(keys_to_return, keys_to_extract):
            outnames |= {kr: glider_paths[ke]}
        
    return outnames


def _run_pyglider_gridding(inname, glider_paths) -> dict:
    """
    A consistent way of creating gridded datafiles for ESD. 
    Note that this function uses the module-level variables: 
    bin_size, depth_max. 

    Parameters
    ----------
    inname : str or Path
        netcdf file to break into profiles.
        Passed directly to inname argument of pyglider.ncprocess.make_gridfiles
    glider_paths : dict
        A dictionary of file/directory paths for various processing steps.
        Intended to be the output of get_path_glider()
        See this function for the expected key/value pairs

    Returns
    -------
    dict
        A dictionary of the generated gridded datasets, 
        with keys like "outname_gr1m" and "outname_gr5m"
    """

    outnames = {}

    for i in bin_size:
        _log.info("Generating %sm gridded data", i)
        outname_gr = pgncprocess.make_gridfiles(
            inname,
            glider_paths["griddir"],
            glider_paths["deploymentyaml"],
            depth_bins=np.arange(0, depth_max, i),
            fnamesuffix=f"-{glider_paths['mode']}-{i}m",
        )
        outnames = outnames | {f"outname_gr{i}m": outname_gr}
    return outnames


def check_flbbcd_autoexec(
        binarydir, 
        cacdir, 
        deploymentyaml,
        search="*.[Dd|Ee][Bb][Dd]",
):
    """


    Parameters
    ----------
    binarydir : str
        Path to the binary directory
    cacdir : str
        Path to the cache file directory
    search : str, optional
        Search pattern for the binary files, by default "*.[Dd|Ee][Bb][Dd]"

    Returns
    -------
    Nothing
    
    """

    _log.info("Checking device FLBBCD calibration values")

    with open(deploymentyaml) as fin:
        deployment = yaml.safe_load(fin)

    flbbcd_cal_names = [
        "u_flbbcd_chlor_cwo", 
        "u_flbbcd_chlor_sf", 
        "u_flbbcd_bb_cwo", 
        "u_flbbcd_bb_sf", 
        "u_flbbcd_cdom_cwo", 
        "u_flbbcd_cdom_sf", 
    ]

    device_data = deployment['glider_devices']
    if "instrument_flbbcd" in device_data:
        flbbcd_sn = device_data["instrument_flbbcd"].get("serial_number", None)
        flbbcd_cal_date = device_data["instrument_flbbcd"].get("calibration_date", None)

        # Load in esdglider calibration values
        with open(paths.get_path_flbbcd_calibrations(), "r") as fin:
            flbbcd_cals = yaml.safe_load(fin)
        
            try:
                flbbcd_cal_values = flbbcd_cals[flbbcd_sn][flbbcd_cal_date]
            except KeyError:
                _log.warning(
                    "No calibration values found for FLBBCD with serial number %s and calibration date %s", 
                    flbbcd_sn, flbbcd_cal_date
                )
                return 

            # Extract values in binary, from the autoexec, and confirm one value per key
            dbd = dbdreader.MultiDBD(pattern=f"{binarydir}/{search}", 
                                     cacheDir=cacdir)            
 
            sensor_data = dbd.get(*flbbcd_cal_names, return_nans=False)
            cal_values = [np.unique(i[1]) for i in sensor_data]
            if not all(len(item) == 1 for item in cal_values):
                _log.warning("Inconsistent calibration values found in binary files. Ending check")
                return 

            cal_values = [i[0] for i in cal_values]
            sensor_cals = dict(zip(flbbcd_cal_names, cal_values))
            sensor_cals["u_flbbcd_bb_sf"] = sensor_cals["u_flbbcd_bb_sf"] * 1e-6

            if flbbcd_cal_values.keys() != sensor_cals.keys():
                _log.warning("Mismatch between calibration keys in YAML and binary files. Ending check")
                return 

            for key in flbbcd_cal_names:
                if key in sensor_cals:
                    if not math.isclose(sensor_cals[key], flbbcd_cal_values[key], abs_tol=1e-6):
                        _log.warning(
                            "Calibration value for %s does not match the expected value. "
                            + "Expected: %s, Found: %s", 
                            key, flbbcd_cal_values[key], sensor_cals[key]
                        )
                    else:
                        _log.info("Calibration value for %s matches the expected value", key)                        
                        _log.debug("cal values, from binary files: %s", sensor_cals[key])
                        _log.debug("cal values, from cal document: %s", flbbcd_cal_values[key])
                else:
                    _log.warning("Calibration value for %s not found in binary files", key)
    
            _log.info("Finished checking FLBBCD calibration values")
    else:
        _log.info("No FLBBCD instrument found in deployment yaml")

    

def correct_flbbcd_raw_sci(
    glider_paths: dict, 
    chlor_cal: tuple | None = None,
    cdom_cal: tuple | None = None,
    bb_cal: tuple | None = None,
) -> tuple:
    """
    Calculate and save corrected ecopuck (FLBBCD) output values
    
    For the raw dataset, use esdglider's calc_flbbcd function. 

    For the science timeseries, do not calculate the corrected output 
    values from interpolated signal values. 
    Rather, calculate corrected FLBBCD output values using the raw dataset, 
    and then interpolate these values to the science timeseries. 

    Additionally, to mirror the behavior of esdglider/pyglider, 
    the following functions are run on the interpolated science timeseries
    values: pyglider's find_gaps and _zero_screen functions, and
    esdglider's drop_bogus. 

    If the calibration ({var}_cal) inputs are None 
    (this will usually be the case), 
    then the function will attempt to read the calibration values 
    esdglider's data/flbbcd_calibration.yml.

    See `esdglider.utils.calc_flbbcd` for more details. 

    Parameters
    ----------
    glider_paths: dict
        Dictionary containing glider-related paths. 
        Expected keys are "tsrawpath" and "tsscipath".
    chlor_cal: tuple | None
        Calibration values for chlorophyll channel, as (cwo, sf). If None, read from YAML.
    cdom_cal: tuple | None
        Calibration values for CDOM channel, as (cwo, sf). If None, read from YAML.
    bb_cal: tuple | None
        Calibration values for backscatter channel, as (cwo, sf). If None, read from YAML.

    Returns
    -------
    outnames of raw and science dataset as a tuple (outname_tsraw, outname_tssci)
    """

    _log.info("Starting FLBBCD corrections")

    # Read in datasets
    outname_tsraw = glider_paths["tsrawpath"]
    outname_tssci = glider_paths["tsscipath"]
    ds_raw = xr.load_dataset(outname_tsraw)
    ds_sci = xr.load_dataset(outname_tssci)

    # Get calibration values
    if chlor_cal is None or cdom_cal is None or bb_cal is None:
        with open(paths.get_path_flbbcd_calibrations(), "r") as fin:
            flbbcd_cals = yaml.safe_load(fin)

            sn, cdate = utils.get_instrument_sn_date(ds_raw, "instrument_flbbcd")
            try:
                flbbcd_cal_values = flbbcd_cals[sn][cdate]
                if chlor_cal is None:
                    chlor_cal = (flbbcd_cal_values["u_flbbcd_chlor_cwo"], 
                                flbbcd_cal_values["u_flbbcd_chlor_sf"])
                if cdom_cal is None:
                    cdom_cal = (flbbcd_cal_values["u_flbbcd_cdom_cwo"], 
                                flbbcd_cal_values["u_flbbcd_cdom_sf"])
                if bb_cal is None:
                    bb_cal = (flbbcd_cal_values["u_flbbcd_bb_cwo"], 
                            flbbcd_cal_values["u_flbbcd_bb_sf"])
            except KeyError:
                _log.warning(
                    "No calibration values found for FLBBCD with serial number %s and calibration date %s. Exiting", 
                    sn, cdate
                )
                return outname_tsraw, outname_tssci

    # Calculate corrected raw values
    ds_raw_cor = utils.calc_flbbcd(ds_raw, chlor_cal, cdom_cal, bb_cal)

    # Interpolate raw values for science timeseries. Add comments
    ds_sci_cor = ds_sci.copy(deep=True)
    msg = "Interpolated from raw values, after correct calibration values applied."
    t = ds_sci_cor.time.values.astype(np.int64) / 1e9

    for var in ["chlorophyll", "cdom", "backscatter_700"]:
        if var in ds_raw_cor.data_vars and var in ds_sci_cor.data_vars:
            # Filter for non-nan raw values
            val_raw_notna = ~np.isnan(ds_raw_cor[var].values)
            _t = ds_raw_cor.time.values.astype(np.int64)[val_raw_notna] / 1e9
            val = ds_raw_cor[var].values[val_raw_notna] 
            
            # Interpolate raw values to sci timestamps, and do screens
            val_interp = np.interp(t, _t, val, left=np.nan, right=np.nan)
            tg_ind = pgutils.find_gaps(_t, t, maxgap_esd)
            val_interp[tg_ind] = np.nan
            val_interp = pgutils._zero_screen(val_interp)

            # Update ds object with values and attributes
            ds_sci_cor[var].values = val_interp
            ds_sci_cor[var].attrs["comment"] = utils.append_string(
                ds_sci_cor[var].attrs["comment"], msg)

    ds_sci_cor = utils.drop_bogus(ds_sci_cor)

    _log.info("Writing corrected raw and science timeseries to netcdf")
    ds_raw_cor.to_netcdf(outname_tsraw, encoding={'time': time_encoding})
    ds_sci_cor.to_netcdf(outname_tssci, encoding={'time': time_encoding})

    _log.info("Done FLBBCD correction")
    return outname_tsraw, outname_tssci


def correct_cdom_raw_sci(glider_paths: dict):
    """
    Correct or remove CDOM values in the raw and science timeseries datasets, 
    via the check_cdom function.

    Parameters
    ----------
    glider_paths: dict
        Dictionary containing glider-related paths. 
        Expected keys are "tsrawpath" and "tsscipath".

    Returns
    -------
    outnames of raw and science dataset as a tuple (outname_tsraw, outname_tssci)
    """


    # Read in datasets
    outname_tsraw = glider_paths["tsrawpath"]
    outname_tssci = glider_paths["tsscipath"]
    ds_raw = xr.load_dataset(outname_tsraw)
    ds_sci = xr.load_dataset(outname_tssci)

    _log.info("Starting CDOM correction for raw dataset")
    ds_raw_cor = utils.correct_cdom(ds_raw)
    _log.info("Writing corrected raw timeseries to netcdf")
    ds_raw_cor.to_netcdf(outname_tsraw, encoding={'time': time_encoding})

    _log.info("Starting CDOM correction for science dataset")
    ds_sci_cor = utils.correct_cdom(ds_sci)
    _log.info("Writing corrected science timeseries to netcdf")
    ds_sci_cor.to_netcdf(outname_tssci, encoding={'time': time_encoding})

    _log.info("Done CDOM correction")
    return outname_tsraw, outname_tssci


def drop_ts_ranges(
    ds : xr.Dataset,
    drop_list : list[tuple[str, str]],
    dstype : str,
    *, 
    plotdir: str | None = None,    
    # prof_summ: pd.DataFrame | None = None,
    # prof_index_attrs: dict | None = None,
    # outname: str | None = None,
    # profsummdir: str | None = None,
    # # prof_depth_var : dict | None = None,
    # prof_args : dict | None = None,
) -> xr.Dataset:
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

    Parameters
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


    outname : str | None (default None)
        If not None, then ds is written to this path
    profsummdir : str | None (default None)
        Path to profile summary CSV. Ignored if dstype is raw.
        If not None and dstype is eng or sci, will join profiles
    prof_args : dict | None, optional
        named optional arguments, for esdglider.profiles.findProfiles

    Returns
    -------
    xarray Dataset
        Input ds, with points within specified time ranges dropped.
        Also saves 'dropped' scatter plots to plotdir, if specified.
    """

    _log.info(
        "There are %d points in the original %s dataset",
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
    if "distance_over_ground" in ds:
        _log.info("Calculating new distance over ground")
        ds = pgutils.get_distance_over_ground(ds)

    # # Profiles
    # if prof_summ is not None and prof_index_attrs is not None:
    #     _log.info("Join profiles, from raw timeseries, by time windows")
    #     prof_index_attrs["sources"] += ", from raw dataset"
    #     ds = prof.join_profiles(ds, prof_summ, prof_index_attrs)

    # if dstype == "raw" and profsummdir is not None:
    #     _log.info("Calculating new profiles for raw dataset")
    #     tsraw = prof.get_fill_profiles(ds, "time", "depth_measured", prof_args)
    #     prof_summ = prof.calc_profile_summary(tsraw, "depth_measured")
    #     prof_summ.to_csv(profsummdir, index=False)
    #     prof.check_profiles(prof_summ)
    # elif profsummdir is not None:
    #     _log.info("Join-calculating new profiles for eng/sci dataset")
    #     prof_summ_raw = pd.read_csv(profsummdir, parse_dates=["start_time", "end_time"])
    #     prof.join_profiles(ds, prof_summ_raw, prof_args)
    #     prof.check_profiles(prof.calc_profile_summary(ds, "depth"))
    # else:
    #     _log.info("No profile work")

    # # Write to netcdf
    # if outname is not None:
    #     _log.info(f"Writing dataset to {outname}")
    #     ds.to_netcdf(
    #         outname,
    #         mode='w',
    #         encoding={'time': time_encoding},
    #     )

    return ds


def complete_profile_correction(
        tsraw: xr.Dataset, 
        tseng: xr.Dataset | None, 
        tssci: xr.Dataset | None, 
        glider_paths: dict, 
        # use_m_depth: bool = False,
        # prof_depth_var: str | None = None,
        # prof_index_attrs: dict,   
        # prof_args: dict | None = None
    ):
    """
    Sometimes, the profile indices need to be adjusted by hand. For instance:
    `tsraw["profile_index"].loc[{"time": "2024-11-13 15:14:59"}] = 590.5`

    Typically, this is done for the raw dataset, 
    the profile summary CSV is written, 
    and the new profiles are applied to the eng and sci datasets.
    This function performs these steps, and writes the profile summary CSV 
    and eng/sci datasets to disk.

    The profile index attributes are extracted from the raw timeseries, 
    including the depth variable used for the profiles. 
    These attributes are added the the eng/sci datasets, with the phrase 
    ", from raw dataset" added to the end of the 'sources' attribute.

    If either tseng or tssci do not need to be updated, 
    they can be passed as `None` and will be ignored.


    Parameters
    ----------
    tsraw : xarray.Dataset
        Raw timeseries dataset
    tseng : xarray.Dataset | None
        Engineering timeseries dataset (or None if not available)
    tssci : xarray.Dataset | None
        Science timeseries dataset (or None if not available)
    glider_paths : dict
        Dictionary containing glider-related paths.

    Returns
    -------
    None
    """

    _log.info(
        "Completing profile correction by writing new profile summary, "
        + "applying profiles to eng/sci datasets (as applicable), "
        + "and saving three timeseries datasets (as applicable)"
    )

    # Extract info from raw dataset attributes
    prof_index_attrs = tsraw["profile_index"].attrs

    source_split = prof_index_attrs["sources"].split()
    prof_depth_var = next((i for i in source_split if 'depth' in i), None)
    if prof_depth_var is None:
        _log.error("No depth variable found in profile index sources")
        raise ValueError("No depth variable found in profile index sources")
        
    prof_index_attrs["sources"] += ", from raw dataset"

    # Finish profiles
    prof_summ = prof.calc_profile_summary(tsraw, prof_depth_var)
    prof_summ.to_csv(glider_paths["profsummpath"], index=False)
    prof.check_profiles(prof_summ)
    _log.info("Wrote new profile summary to %s", glider_paths["profsummpath"])

    # Save raw dataset
    tsraw.to_netcdf(
        glider_paths["tsrawpath"], 
        encoding={'time': time_encoding}
    )
    _log.info("Wrote raw timeseries to %s", glider_paths["tsrawpath"])

    # Apply new profiles to sci and eng, and save
    if tseng is not None:
        tseng = prof.join_profiles(tseng, prof_summ, prof_index_attrs)
        tseng.to_netcdf(
            glider_paths["tsengpath"], 
            encoding={'time': time_encoding}
        )
        _log.info("Wrote eng timeseries with new profiles to %s", glider_paths["tsengpath"])

    if tssci is not None:
        tssci = prof.join_profiles(tssci, prof_summ, prof_index_attrs)
        tssci.to_netcdf(
            glider_paths["tsscipath"], 
            encoding={'time': time_encoding}
        )
        _log.info("Wrote science timeseries with new profiles to %s", glider_paths["tsscipath"])
