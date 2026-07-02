"""
Slocum pipeline functions, for ESD-specific processing of slocum glider files
"""

import logging
import os
from importlib import metadata
import tempfile

import numpy as np
import pandas as pd
import pyglider.ncprocess as pgncprocess
import pyglider.slocum as pgslocum
import pyglider.utils as pgutils
import xarray as xr

import esdglider.plots as plots
import esdglider.utils as utils
from esdglider.slocum import core

_log = logging.getLogger(__name__)


"""
Glider values for ESD slocum pipeline
Note that depth bins will be defined by `np.arange(0, depth_max, i)`,
where i is in element of bin_size

bin_size: list
    a list of the gridded depth bin sizes
depth_max: float
    The maximum value to use when making depth bins
"""
bin_size = [1, 5]
depth_max = 1200.1


# def binary_to_nc(
#     # deployment_info: dict,
#     deployment_name: str, 
#     mode: str, 
#     glider_paths: dict,
#     *,
#     write_raw: bool = True,
#     write_timeseries: bool = True,
#     sci_timeseries_pyglider: bool = True,
#     write_gridded: bool = True,
#     check_cdom: bool = False,
#     # gridded_depth_measured: bool = False, 
#     binary_search: str | None = None,
#     file_info: str | None = None,
#     **kwargs,
# ):
#     """
#     Process binary ESD slocum glider data to netCDF file(s).
#     For more info, see:
#     https://swfsc.github.io/glider-lab-manual/content/glider-data.html

#     The contents of this function used to just be in scripts/binary_to_nc.py.
#     They were moved to this structure for easier development and debugging

#     Parameters
#     ----------
#     deployment_info : dict
#         A dictionary with the relevant deployment info. A dictionary is
#         used to make it easier if arguments are added or removed.
#         This dictionary must contain at least:
#         deploymentyaml : str
#             The filepath of the glider deployment yaml.
#             This file will have relevant info,
#             including deployment name (eg, amlr01-20210101) and project
#     mode : str
#         Mode of the glider data being processed.
#         Must be either 'rt', for real-time, or 'delayed. 
#         This is a separate argument, so that the yaml file can be used
#         for processing both rt and delayed data from the same deployment
#     glider_paths : dict
#         A dictionary of file/directory paths for various processing steps.
#         Intended to be the output of get_path_glider()
#         See this function for the expected key/value pairs
#     write_raw, write_timeseries, write_gridded : bool, default True
#         Should the raw, timeseries, and gridded, respectively,
#         xarray DataSets be created and written to files?
#         Raw files are created by binary_to_raw, and include uninterpolated data
#         Timeseries files are created by pyglider's binary_to_timeseries;
#         both 'engineering' and 'science' timeseries files are created.
#         Eng and sci files have m_depth and sci_water_temp as the time bases,
#         respectively. Gridded files are created by pyglider's make_gridfiles,
#         using the science timeseries as the input.
#         Both 1m and 5m gridded datasets are created.
#         Note: if True then any existing files will be clobbered
#     sci_timeseries_pyglider : bool, default True
#         Should the function use pyglider.slocum.binary_to_timeseries to create
#         create the science timeseries (True),
#         or glider.raw_to_sci_timeseries (False)
#     check_cdom : bool, default False
#         Should the function use check_cdom_esd 
#     gridded_depth_measured : bool, default False
#         Should the function pass the science timeseries directly to 
#         pyglider.ncprocess.make_gridfiles (False), 
#         or instead use glider.make_gridfiles_depth_measured (True)
#     binary_search : str | None, default None
#         A regex string of the binary file extensions for dbdreader to 
#         search for when reading in data. Passed directly to the 
#         'search' argument of pyglider.binary_to_timeseries. 
#         If 'None', then uses "*.[DEde][BCbc][Dd]" for delayed-mode data,
#         and "*.[STst][BCbc][Dd]" for real-time data
#     file_path: str | None, default None
#         The path of the parent processing script.
#         If provided, will be included in the history attribute
#     **kwargs
#         Optional arguments passed to utils.findProfiles

#     Returns
#     -------
#     A dictionary of the filenames of the various netCDF files, as strings.
#     In order: the raw data, the engineering and science timeseries,
#     and the 1m and 5m gridded files
#     """

#     # deploymentyaml = deployment_info["deploymentyaml"]
#     # mode = deployment_info["mode"]

#     # --------------------------------------------
#     # Check files, and get vars + directory paths
#     # if glider_paths["deploymentyaml"] != deploymentyaml:
#     #     raise ValueError(
#     #         "Provided yaml path (%s) is not the same as the paths yaml path (%s)",
#     #         deploymentyaml,
#     #         glider_paths["deploymentyaml"],
#     #     )

#     # deployment = utils.read_deploymentyaml(deploymentyaml)
#     # deployment_name = deployment["metadata"]["deployment_name"]

#     deploymentyaml = glider_paths["deploymentyaml"]
#     rawdir = glider_paths["rawdir"]
#     tsdir = glider_paths["tsdir"]
#     # griddir = paths["griddir"]

#     # Check mode, set binary_search regex
#     if mode == "delayed":
#         if binary_search is None:
#             binary_search = "*.[DEde][BCbc][Dd]"
#     elif mode == "rt":
#         if binary_search is None:
#             binary_search = "*.[STst][BCbc][Dd]"
#     else:
#         raise ValueError("mode must be either 'rt' or 'delayed'")

#     # commonly used parameters in timeseries/gridded data
#     maxgap_esd = 60

#     # Dictionary with info needed by post-processing functions
#     postproc_info = {
#         "deploymentyaml": deploymentyaml, 
#         "mode": mode, 
#         "file_info": file_info,
#         "metadata_dict": {"deployment_name": deployment_name},
#         "device_dict": {},
#         "profile_summary_path": glider_paths["profsummpath"],
#         "maxgap": maxgap_esd,
#     }

#     # --------------------------------------------
#     # Raw
#     outname_tsraw = glider_paths["tsrawpath"]
#     if write_raw:
#         utils.remove_file(outname_tsraw)
#         utils.makedirs_pass(rawdir)

#         _log.info("Generating raw nc")
#         outname_tsraw = core.binary_to_raw_timeseries(
#             glider_paths["binarydir"],
#             glider_paths["cacdir"],
#             rawdir,
#             [deploymentyaml, glider_paths["engyaml"], glider_paths["rawyaml"]],
#             search=binary_search,
#             include_source=True,
#             fnamesuffix=f"-{mode}-raw",
#             pp=postproc_info,
#             **kwargs,
#         )

#         # Save profile summary
#         tsraw = xr.load_dataset(outname_tsraw)
#         prof_summ_path = postproc_info["profile_summary_path"]
#         _log.info("Writing profile summary CSV to %s", prof_summ_path)
#         prof_summ = utils.calc_profile_summary(tsraw, "depth_measured")
#         prof_summ.to_csv(prof_summ_path, index=False)
#         num_dives = np.count_nonzero(prof_summ.profile_direction.values == 1)
#         _log.info("Deployment %s performed %s dives", deployment_name, num_dives)

#         # Write deployment_start and deployment_end to postproc_info
#         postproc_info["deployment_start"] = tsraw.attrs["deployment_start"]
#         postproc_info["deployment_end"] = tsraw.attrs["deployment_end"]

#         # Brief profile and depth sanity checks
#         _log.info("raw timeseries checks")
#         utils.check_profiles(prof_summ)
#         utils.check_depth(tsraw["depth_measured"], tsraw["depth_ctd"])

#         # CDOM check
#         if check_cdom:
#             utils.check_cdom(tsraw)
#             utils.to_netcdf_esd(tsraw, outname_tsraw)


#     else:
#         _log.info("Not writing raw nc")
#         try:
#             with xr.open_dataset(outname_tsraw) as tsraw:
#                 # tsraw = xr.load_dataset(outname_tsraw)
#                 postproc_info["deployment_start"] = tsraw.attrs["deployment_start"]
#                 postproc_info["deployment_end"] = tsraw.attrs["deployment_end"]
#         except FileNotFoundError:
#             _log.debug("Not writing raw nc file, and file could not be found")
#             if write_timeseries:
#                 _log.warning(
#                     f"The raw nc file ({outname_tsraw}) does not exist, "
#                     + "and thus postproc_info cannot be updated. "
#                     + "The timeseries file may have incorrect metadata")

#     # --------------------------------------------
#     # Timeseries
#     outname_tseng = glider_paths["tsengpath"]
#     outname_tssci = glider_paths["tsscipath"]
#     outname_gr1m = glider_paths["gr1path"]
#     outname_gr5m = glider_paths["gr5path"]

#     if write_timeseries:
#         # Delete previous files before starting run. Can't delete whole directory
#         # Since gridded depend on ts, also delete gridded
#         utils.remove_file(outname_tseng)
#         utils.remove_file(outname_tssci)
#         utils.remove_file(outname_gr1m)
#         utils.remove_file(outname_gr5m)
#         utils.makedirs_pass(tsdir)

#         # Engineering - uses m_depth as time base
#         _log.info("Generating engineering timeseries")
#         outname_tseng = pgslocum.binary_to_timeseries(
#             glider_paths["binarydir"],
#             glider_paths["cacdir"],
#             tsdir,
#             [deploymentyaml, glider_paths["engyaml"]],
#             search=binary_search,
#             fnamesuffix=f"-{mode}-eng",
#             time_base="m_depth",
#             profile_filt_time=None,  # type: ignore
#             maxgap=maxgap_esd,
#         )

#         _log.info(f"Post-processing engineering timeseries: {outname_tseng}")
#         tseng = xr.load_dataset(outname_tseng)
#         tseng = postproc_eng_timeseries(tseng, postproc_info, **kwargs)
#         utils.to_netcdf_esd(tseng, outname_tseng)

#         if sci_timeseries_pyglider:
#             # Science - uses sci_water_pressure as time_base sensor
#             _log.info("Generating science timeseries")
#             outname_tssci = pgslocum.binary_to_timeseries(
#                 glider_paths["binarydir"],
#                 glider_paths["cacdir"],
#                 tsdir,
#                 deploymentyaml,
#                 search=binary_search,
#                 fnamesuffix=f"-{mode}-sci",
#                 time_base="sci_water_pressure",
#                 profile_filt_time=None,  # type: ignore
#                 maxgap=maxgap_esd,
#             )
#             postproc_info["drop_vars"] = ["pressure"]

#         else:
#             _log.info("Generating science timeseries, via raw_to_sci_timeseries")
#             outname_tssci = core.raw_to_sci_timeseries(
#                 outname_tsraw,
#                 tsdir,
#                 deploymentyaml,
#                 fnamesuffix=f"-{mode}-sci",
#                 maxgap=maxgap_esd,
#             )
#             # raw_to_sci_timeseries calls postproc_sci_timeseries internally
#             tssci = xr.load_dataset(outname_tssci)

#         _log.info(f"Post-processing science timeseries: {outname_tssci}")
#         tssci = xr.load_dataset(outname_tssci)
#         tssci = postproc_sci_timeseries(tssci, postproc_info, **kwargs)
#         utils.to_netcdf_esd(tssci, outname_tssci)   

#         _log.info("final eng/sci timeseries checks")
#         # Brief profile sanity check - check_profiles done in postproc-general
#         prof_max_diff = abs(
#             (tssci.profile_index.max() - tseng.profile_index.max()).values,
#         )
#         if prof_max_diff > 0.5:
#             _log.warning(
#                 "The max profile idx of eng and sci timeseries is different "
#                 + "by more than 0.5. This means they may have "
#                 + "a different number of functional profiles",
#             )
#             _log.warning(f"Min idx for eng: {tseng.profile_index.values.min()}")
#             _log.warning(f"Min idx for sci: {tssci.profile_index.values.min()}")
#             _log.warning(f"Max idx for eng: {tseng.profile_index.values.max()}")
#             _log.warning(f"Max idx for sci: {tssci.profile_index.values.max()}")
#         else:
#             _log.info("The eng and sci timeseries have the same functional profiles")

#         # Depth check, across the eng/sci datasets
#         utils.check_depth(tseng["depth"], tssci["depth"])

#     else:
#         _log.info("Not writing timeseries nc")

#     # --------------------------------------------
#     # Gridded data, 1m and 5m
#     if write_gridded:
#         utils.remove_file(outname_gr1m)
#         utils.remove_file(outname_gr5m)
#         if not os.path.isfile(outname_tssci):
#             raise FileNotFoundError(f"Could not find {outname_tssci}")
        
#         if sci_timeseries_pyglider:
#             _log.info("Gridding science data using CTD-calculated depth")
#             outnames = make_gridfiles_esd(outname_tssci, glider_paths=glider_paths)
#         else:
#             outnames = core.make_gridfiles_depth_measured(glider_paths)
#         _log.debug("gridded outnames %s", "; ".join(outnames))

#         # _log.debug("Excluded vars: %s", ", ".join(gridded_exclude_vars))

#         # _log.info("Generating 1m gridded data")
#         # outname_gr1m = pgncprocess.make_gridfiles(
#         #     outname_tssci,
#         #     griddir,
#         #     deploymentyaml,
#         #     depth_bins=np.arange(0, 1200.1, 1),
#         #     fnamesuffix=f"-{mode}-1m",
#         #     exclude_vars=gridded_exclude_vars,
#         # )

#         # _log.info("Generating 5m gridded data")
#         # outname_gr5m = pgncprocess.make_gridfiles(
#         #     outname_tssci,
#         #     griddir,
#         #     deploymentyaml,
#         #     depth_bins=np.arange(0, 1200.1, 5),
#         #     fnamesuffix=f"-{mode}-5m",
#         #     exclude_vars=gridded_exclude_vars,
#         # )

#     else:
#         _log.info("Not writing gridded nc")

#     # --------------------------------------------
#     return {
#         "outname_tsraw": outname_tsraw,
#         "outname_tseng": outname_tseng,
#         "outname_tssci": outname_tssci,
#         "outname_gr1m": outname_gr1m,
#         "outname_gr5m": outname_gr5m,
#     }


def generate_timeseries(
    deployment_name: str, 
    mode: str, 
    glider_paths: dict,
    *,
    write_raw: bool = True,
    write_eng: bool = True,
    write_sci: bool = True,
    sci_timeseries_pyglider: bool = True,
    check_cdom: bool = False,
    binary_search: str | None = None,
    file_info: str | None = None,
    **kwargs,
):
    deploymentyaml = glider_paths["deploymentyaml"]
    rawdir = glider_paths["rawdir"]
    tsdir = glider_paths["tsdir"]
    # griddir = paths["griddir"]

    # Check mode, set binary_search regex
    if mode == "delayed":
        if binary_search is None:
            binary_search = "*.[DEde][BCbc][Dd]"
    elif mode == "rt":
        if binary_search is None:
            binary_search = "*.[STst][BCbc][Dd]"
    else:
        raise ValueError("mode must be either 'rt' or 'delayed'")

    # commonly used parameters in timeseries/gridded data
    maxgap_esd = 60

    # Dictionary with info needed by post-processing functions
    postproc_info = {
        "deploymentyaml": deploymentyaml, 
        "mode": mode, 
        "file_info": file_info,
        "metadata_dict": {"deployment_name": deployment_name},
        "device_dict": {},
        "profile_summary_path": glider_paths["profsummpath"],
        "maxgap": maxgap_esd,
    }

    # --------------------------------------------
    # Raw
    outname_tsraw = glider_paths["tsrawpath"]
    if write_raw:
        utils.remove_file(outname_tsraw)
        utils.makedirs_pass(rawdir)

        _log.info("Generating raw nc")
        outname_tsraw = core.binary_to_raw_timeseries(
            glider_paths["binarydir"],
            glider_paths["cacdir"],
            rawdir,
            [deploymentyaml, glider_paths["engyaml"], glider_paths["rawyaml"]],
            search=binary_search,
            include_source=True,
            fnamesuffix=f"-{mode}-raw",
            # pp=postproc_info,
            **kwargs,
        )

        # Run postprocessing
        tsraw = xr.load_dataset(outname_tsraw)
        new_start = [
            "latitude",
            "longitude",
            "depth_measured",
            "depth_ctd",
            "profile_index",
            "profile_direction",
        ]
        tsraw = utils.data_var_reorder(tsraw, new_start)

        # # Add metadata - using postproc_attrs for consistency
        # pp["metadata_dict"] = deployment["metadata"]
        # pp["device_dict"] = deployment["glider_devices"]
        # postproc_attrs calls pgutils.fill_metadata
        tsraw = postproc_attrs(tsraw, postproc_info)

        # Save profile summary
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

        # CDOM check
        if check_cdom:
            utils.check_cdom(tsraw)
            utils.to_netcdf_esd(tsraw, outname_tsraw)


    else:
        _log.info("Not writing raw nc")
        try:
            with xr.open_dataset(outname_tsraw) as tsraw:
                # tsraw = xr.load_dataset(outname_tsraw)
                postproc_info["deployment_start"] = tsraw.attrs["deployment_start"]
                postproc_info["deployment_end"] = tsraw.attrs["deployment_end"]
        except FileNotFoundError:
            _log.debug("Not writing raw nc file, and file could not be found")
            if write_sci or write_eng:
                _log.warning(
                    "The raw nc file (%s) does not exist, "
                    + "and thus sci and eng timeseries cannot be generated.", 
                    outname_tsraw
                )

    # --------------------------------------------
    # Timeseries
    outname_tseng = glider_paths["tsengpath"]
    outname_tssci = glider_paths["tsscipath"]
    outname_gr1m = glider_paths["gr1path"]
    outname_gr5m = glider_paths["gr5path"]

    if write_eng:
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
            glider_paths["binarydir"],
            glider_paths["cacdir"],
            tsdir,
            [deploymentyaml, glider_paths["engyaml"]],
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
        del tseng

    if write_sci:
        if sci_timeseries_pyglider:
            # Science - uses sci_water_pressure as time_base sensor
            _log.info("Generating science timeseries")
            outname_tssci = pgslocum.binary_to_timeseries(
                glider_paths["binarydir"],
                glider_paths["cacdir"],
                tsdir,
                deploymentyaml,
                search=binary_search,
                fnamesuffix=f"-{mode}-sci",
                time_base="sci_water_pressure",
                profile_filt_time=None,  # type: ignore
                maxgap=maxgap_esd,
            )
            postproc_info["drop_vars"] = ["pressure"]

        else:
            _log.info("Generating science timeseries, via raw_to_sci_timeseries")
            outname_tssci = core.raw_to_sci_timeseries(
                outname_tsraw,
                tsdir,
                deploymentyaml,
                fnamesuffix=f"-{mode}-sci",
                maxgap=maxgap_esd,
            )
            # raw_to_sci_timeseries calls postproc_sci_timeseries internally
            tssci = xr.load_dataset(outname_tssci)

        _log.info(f"Post-processing science timeseries: {outname_tssci}")
        tssci = xr.load_dataset(outname_tssci)
        tssci = postproc_sci_timeseries(tssci, postproc_info, **kwargs)
        utils.to_netcdf_esd(tssci, outname_tssci)
        del tssci

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

    return {
        "outname_tsraw": outname_tsraw,
        "outname_tseng": outname_tseng,
        "outname_tssci": outname_tssci,
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
    prof_summ = utils.calc_profile_summary(ds, depth_var)
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
    if "depth_measured" in ds:
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
    ds.attrs["processing_level"] += " All values have been interpolated via linear fill"

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
    if "depth_ctd" in ds:
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
        + f"with a maxgap of {pp['maxgap']} seconds. "
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
    new_start[2:2] = sorted([i for i in ds.keys() if "depth" in i])  # type: ignore
    ds = utils.data_var_reorder(ds, new_start)

    _log.debug("end sci postproc: ds has %s values", len(ds.time))

    return ds


def generate_gridded(
    glider_paths: dict,
    sci_timeseries_pyglider: bool = True,
):
    outname_tssci = glider_paths["tsscipath"]
    outname_gr1m  = glider_paths["gr1path"]
    outname_gr5m  = glider_paths["gr5path"]

    if not os.path.isfile(outname_tssci):
        raise FileNotFoundError(f"Could not find {outname_tssci}")
    utils.remove_file(outname_gr1m)
    utils.remove_file(outname_gr5m)
    # TODO: change to delete all files in glider_paths["griddir"]

    if sci_timeseries_pyglider: #use_m_depth #use_depth="ctd" | "m"
        # depth_touse = "vt" | "m"
        _log.info("Gridding science data using CTD-calculated depth")
        outnames = make_gridfiles_esd(outname_tssci, glider_paths=glider_paths)
    else:
        outnames = make_gridfiles_depth_measured(glider_paths)
    _log.debug("gridded outnames %s", "; ".join(outnames))

    # --------------------------------------------
    return {
        "outname_gr1m": outname_gr1m,
        "outname_gr5m": outname_gr5m,
    }


def make_gridfiles_depth_measured(glider_paths):
    """
    Make gridfiles using the measured depth. This function will be used
    if for instance the CTD was turned off during parts of a deployment, and
    thus the depth calculated from the CTD does not span the full timeseries.

    A temporary nc file is written, to pass to glider.grid_esd (and thus to 
    pyglider.ncprocess.make_gridfiles). The science dataset is not altered.

    Parameters
    ----------
    glider_paths : dict
        A dictionary of file/directory paths for various processing steps.
        Intended to be the output of get_path_glider()
        See this function for the expected key/value pairs

    Returns
    -------
    The output of grid_esd
    """

    _log.info("Gridding science data using glider measured depth (m_depth)")
    outname_tssci = glider_paths["tsscipath"]

    # Leave these checks, in case this function is called directly
    utils.remove_file(glider_paths["gr1path"])
    utils.remove_file(glider_paths["gr5path"])
    if not os.path.isfile(outname_tssci):
        raise FileNotFoundError(f"Could not find {outname_tssci}")

    # _log.debug("Excluded vars: %s", ", ".join(gridded_exclude_vars))

    with tempfile.TemporaryDirectory() as temp_dir:
        # Creating temporary science dataset with measured depth as depth
        temp_file = os.path.join(temp_dir, os.path.basename(outname_tssci))
        _log.debug("temp_file %s", temp_file)
        ds_sci_tmp = xr.load_dataset(outname_tssci)
        ds_sci_tmp = (
            ds_sci_tmp.drop_vars(["depth"])
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

        outnames = make_gridfiles_esd(temp_file, glider_paths=glider_paths)
    return outnames



def make_gridfiles_esd(inname, glider_paths):
    """
    A consistent way of creating gridded datafiles for ESD. 
    Note that this function uses the module-level variables: 
    bin_size, depth_max, and gridded_exclude_vars. 

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
    list of strings
        A list of the generated gridded datasets, i.e.
        a list of the output(s) from pyglider.ncprocess.make_gridfiles
    """

    outnames = []
    # _log.debug("Excluded vars: %s", ", ".join(gridded_exclude_vars))

    for i in bin_size:
        _log.info("Generating %sm gridded data", i)
        outname_gr = pgncprocess.make_gridfiles(
            inname,
            glider_paths["griddir"],
            glider_paths["deploymentyaml"],
            depth_bins=np.arange(0, depth_max, i),
            fnamesuffix=f"-{glider_paths['mode']}-{i}m",
            # exclude_vars=gridded_exclude_vars,
        )
        outnames.append(outname_gr)

    return outnames


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
    kwargs
        Passed to profile functions

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