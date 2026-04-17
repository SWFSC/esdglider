import concurrent.futures
import functools
import logging
import os
import typing

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cmocean.cm as cmo
import matplotlib
import matplotlib.dates as mdates
import matplotlib.figure
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr
from cartopy.mpl.geoaxes import GeoAxes
from matplotlib.gridspec import GridSpec
from matplotlib.patches import Patch
from numpy.typing import NDArray

from esdglider import utils

_log = logging.getLogger(__name__)

"""Constants used throughout plotting functions
"""
label_size = 11
title_size = 13

# Folder names
scatter_path = "pointMaps"
tvt_path = "thisVsThat-eng"
spatialsection_path = "spatialSections-sci"
spatialgrid_path = "spatialGrids-sci"
timesection_path = "timeSections-sci"
timesection_gt_path = "timeSections-sci-gt"
timeseries_eng_path = "timeSeries-eng"
timeseries_sci_path = "timeSeries-sci"
ts_path = "TS-sci"
surfacemap_sci_path = "maps-sci"

"""Helper functions and dictionaries for plot formats/display

Parameters
----------
ds: xarray Dataset
    Requisite dataset
var: str
    Variable name as a string
"""


# def return_var(x):
#     """Simply return a variable"""
#     return x


def adj_var(ds, var):
    """Get the adjusted var values for the plot. Eg, take the log"""
    if var not in adjustments.keys():
        return ds[var]
    if adjustments[var] == np.log10:
        return adjustments[var](ds[var])
    else:
        _log.warning("Unknown adjustments option")
        return ds[var]


def adj_var_label(ds, var):
    """Get the formatted label for the plot"""
    u = ds[var].attrs["units"]
    # if var not in list(units.keys()):
    #     u2 = "missing"
    # else:
    #     u2 = units[var]
    # print(f"var: {var}; ds: {u}; dict: {u2}")

    # if var in adjustments_labels.keys():
    #     return f"{adjustments_labels[var]}({var} [{u}])"
    # else:
    #     return f"{var} [{u}]"

    if var not in adjustments.keys():
        return f"{var} [{u}]"
    elif adjustments[var] == np.log10:
        return "$log_{10}$" + f"({var} [{u}])"
    # elif adjustments[var] == return_var:
    #     return f"{var} [{u}]"
    else:
        raise ValueError(f"var {var}: not an expected adjustments value")


"""Dictionary of adjustments to make to variables to plot.
Functionally, this is variables that should be plotted with log10
"""
adjustments = {
    # "chlorophyll": np.log10,
    # "cdom": np.log10,
    # "backscatter_700": np.log10,
    # "temperature": return_var,
    # "oxygen_concentration": return_var,
    # "salinity": return_var,
    # "density": return_var,
}

# """ Dictionary of label adjustments; corresponds to adjustments dict
# """
# adjustments_labels = {
#     "chlorophyll": "$log_{10}$",
#     "cdom": "$log_{10}$",
#     "backscatter_700": "$log_{10}$",
# }

# """ Dictionary of variable units
# SMW note: This should likely be changed to use units from variable attributes
# """
# units = {
#     "latitude": "deg",
#     "longitude": "deg",
#     "depth": "m",
#     "heading": "deg",
#     "pitch": "rad",
#     "roll": "rad",
#     "waypoint_latitude": "deg",
#     "waypoint_longitude": "deg",
#     "conductivity": "S$\\bullet m^{-1}$",
#     "temperature": "°C",
#     "pressure": "dbar",
#     "chlorophyll": "µg•$L^{-l}$",
#     "cdom": "ppb",
#     "backscatter_700": "$m^{-1}$",
#     "oxygen_concentration": "µmol•$L^{-1}$",
#     "depth_ctd": "m",
#     "distance_over_ground": "km",
#     "salinity": "PSU",
#     "potential_density": "kg $\\bullet m^{-3}$",
#     "density": "kg $\\bullet m^{-3}$",
#     "potential_temperature": "°C",
#     "profile_index": "1",
#     "profile_direction": "1",
#     "total_num_inflections": "1",
# }

"""
Dictionary of the sci variables to plot, and the colors to use

These should be all possible variables,
as plotting functions have checks to skip vars not in the dataset

These colors were chosen by choosing the cmocean colormap
described in https://tos.org/oceanography/assets/docs/29-3_thyng.pdf,
or that most closely matched variables colors in the R package oce
(eg, https://dankelley.github.io/oce/reference/oceColorsOxygen.html)
"""
sci_vars = {
    "temperature": cmo.thermal,  # type: ignore
    "potential_temperature": cmo.thermal,  # type: ignore
    "conductivity": cmo.tempo,  # type: ignore
    "salinity": cmo.haline,  # type: ignore
    "density": cmo.dense,  # colormaps["cividis"], # type: ignore
    "potential_density": cmo.dense,  # type: ignore
    "oxygen_concentration": cmo.oxy,  # cmo.tempo, # type: ignore
    "oxygen_saturation": cmo.oxy,  # type: ignore
    "chlorophyll": cmo.algae,  # type: ignore
    "cdom": cmo.matter,  # type: ignore
    "backscatter_700": cmo.delta,  # colormaps["terrain"], # type: ignore
    "par": cmo.turbid,  # type: ignore
    "profile_index": cmo.gray,  # type: ignore
}

"""
List of engineering variables to plot
Plotting functions loop through this list to plot engineering vars
"""
eng_vars = [
    "heading",
    "pitch",
    "roll",
    "total_num_inflections",
    "commanded_oil_volume",
    "measured_oil_volume",
    "total_amphr",
    "amphr",
    "battery_voltage",
    "vacuum",
    "leak_detect",
    "leak_detect_forward",
    "leak_detect_science",
    "battpos",
    "target_depth",
    "altitude",
    "distance_over_ground",
    "profile_index",
    "profile_direction",
]


def esd_all_plots(
    ds_paths: dict,
    crs=None,
    ds_sci_depth_var: str = "depth",
    base_path: str | None = None,
    bar_file: str | None = None,
    max_workers: int | None = 1,
):
    """
    Wrapper to run all of the plotting loop functions

    Parameters
    ----------
    ds_paths : dict
        A dictionary with the nc file paths for the various plots. Required:
        - 'outname_tseng': path to the engineering timeseries dataset
        - 'outname_tssci': path to the science timeseries dataset
        - 'outname_gr5m': path to the 5m gridded dataset
        - 'outname_tsraw': path to the raw timeseries dataset
    crs : a class from cartopy.crs or None (default None)
        An instantiated cartopy projection, such as cartopy.crs.PlateCarree()
        or cartopy.crs.Mercator().
        If None, surface maps are not created
    ds_sci_depth_var : str
        Name of the depth variable in the science dataset.
        See 'sci_timeseries_loop' for more details
    base_path : str or None (default None)
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    bar_file : str or None (default None)
        Path to the ETOPO nc file to use for contour lines.
        If None (default), then contour lines will not be drawn
    max_workers : int | None
        Number of workers with which to make the plots.
        If 1, a normal for loop is used.
        If None, all cores are used, as determined by os.cpu_count()
        Else, max_workers is the number of cores used

    Returns
    -------
        Nothing
    """

    _log.info("Doing all of the plots")

    # Load datasets
    _log.info("Loading datasets")
    ds_eng = xr.load_dataset(ds_paths["outname_tseng"])
    ds_sci = xr.load_dataset(ds_paths["outname_tssci"])
    ds_gr5m = xr.load_dataset(ds_paths["outname_gr5m"])
    ds_raw = xr.load_dataset(ds_paths["outname_tsraw"])

    # Delete old plots
    if base_path is not None:
        utils.rmtree(os.path.join(base_path))

    # Scatter plots
    scatter_plot(ds_eng, "eng", base_path)
    scatter_plot(ds_sci, "sci", base_path)
    ll_good = ~(np.isnan(ds_raw.longitude) | np.isnan(ds_raw.latitude))
    ds_raw = ds_raw.where(ll_good, drop=True)
    scatter_plot(ds_raw, "raw", base_path)

    # Sci/eng loops
    sci_gridded_loop(ds_gr5m, base_path, max_workers=max_workers)
    sci_timeseries_loop(
        ds_sci,
        depth_var=ds_sci_depth_var,
        base_path=base_path,
        max_workers=max_workers,
    )
    eng_timeseries_loop(ds_eng, base_path, max_workers=max_workers)
    eng_tvt_loop(ds_raw, base_path, max_workers=max_workers)
    # sci_ts_loop(ds_sci, base_path, max_workers=max_workers)

    if bar_file is not None:
        _log.info(f"Loading bar file from {bar_file}")
        bar = xr.load_dataset(bar_file).rename({"latitude": "lat", "longitude": "lon"})
        bar = bar.where(bar.z <= 0, drop=True)
    else:
        _log.info("No bar file path")
        bar = None

    if crs is not None:
        sci_surface_map_loop(
            ds_gr5m,
            crs=crs,
            base_path=base_path,
            bar=bar,
            max_workers=max_workers,
        )
    else:
        _log.info("No crs provided, and thus skipping surface maps")


def sci_gridded_loop_helper(
    var,
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
):
    """
    See sci_gridded_loop for variables
    In short, a small wrapper function that can be passed to
    concurrent.futures.ProcessPoolExecutor
    """
    _log.debug(f"var {var}")
    sci_timesection_plot(var, ds, base_path=base_path, show=show)
    sci_spatialsection_plot(var, ds, base_path=base_path, show=show)
    sci_spatialgrid_plot(var, ds, base_path=base_path, show=show)


def sci_gridded_loop(
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
    max_workers: int | None = 1,
):
    """
    A loop/wrapper function to use a gridded science dataset to make plots
    of all sci_vars keys.
    Specifically, for each sci_var present in the dataset,
    create timesection, spatialsection, and spatialgrid plots

    Arguments let the user specify if these plots should be saved, and/or shown

    Parameters
    ----------
    ds : xarray Dataset
        Gridded science dataset
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    max_workers : int | None
        Number of workers with which to make the plots.
        If 1, a normal for loop is used.
        If None, all cores are used, as determined by os.cpu_count()
        Else, max_workers is the number of cores used

    Returns
    -------
        Nothing
    """

    _log.info("LOOP: making gridded science plots")
    # If doing the loop, remove past plots
    if base_path is not None:
        utils.rmtree(os.path.join(base_path, timesection_path))
        utils.rmtree(os.path.join(base_path, spatialsection_path))
        utils.rmtree(os.path.join(base_path, spatialgrid_path))

    vars_toloop = sci_vars
    if max_workers == 1:
        _log.info("Plotting with one worker, not in parallel")
        for var in vars_toloop:
            _log.debug(f"var {var}")
            sci_timesection_plot(var, ds, base_path=base_path, show=show)
            sci_spatialsection_plot(var, ds, base_path=base_path, show=show)
            sci_spatialgrid_plot(var, ds, base_path=base_path, show=show)
    else:
        if max_workers is None:
            max_workers = max(1, os.cpu_count())  # type: ignore
        _log.info("Starting parallel plotting with %s workers", max_workers)
        task_function = functools.partial(
            sci_gridded_loop_helper,
            ds=ds,
            base_path=base_path,
            show=show,
        )
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
        ) as executor:
            executor.map(task_function, vars_toloop)

    _log.info("Completed gridded science plots")


def eng_tvt_loop(
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
    max_workers: int | None = 1,
):
    """
    A loop/wrapper function to:
    a) create the dictionary used for engineering thisvsthat plots and
    b) create said plots using the tvt function

    Arguments let the user specify if these plots should be saved, and/or shown

    Parameters
    ----------
    ds : xarray Dataset
        Raw dataset
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    max_workers : int | None
        Number of workers with which to make the plots.
        If 1, a normal for loop is used.
        If None, all cores are used, as determined by os.cpu_count()
        Else, max_workers is the number of cores used

    Returns
    -------
        Nothing
    """

    _log.info("LOOP: making engineering tvt plots")
    # If doing the loop, remove past plots
    if base_path is not None:
        utils.rmtree(os.path.join(base_path, tvt_path))

    eng_dict = eng_plots_to_make(ds)
    vars_toloop = eng_dict.keys()
    if max_workers == 1:
        _log.info("Plotting with one worker, not in parallel")
        for key in vars_toloop:
            eng_tvt_plot(key, ds, eng_dict, base_path=base_path, show=show)
    else:
        if max_workers is None:
            max_workers = max(1, os.cpu_count())  # type: ignore
        _log.info("Starting parallel plotting with %s workers", max_workers)
        task_function = functools.partial(
            eng_tvt_plot,
            ds=ds,
            eng_dict=eng_dict,
            base_path=base_path,
            show=show,
        )
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
        ) as executor:
            executor.map(task_function, vars_toloop)

    _log.info("Completed engineering tvt plots")


def sci_timeseries_loop_helper(
    var: str,
    ds: xr.Dataset,
    depth_var: str,
    base_path: str | None = None,
    show: bool = False,
):
    """
    See sci_timeseries_loop for variables
    In short, a small wrapper function that can be passed to
    concurrent.futures.ProcessPoolExecutor
    """
    _log.debug(f"var {var}")
    sci_timeseries_plot(var, ds, depth_var=depth_var, base_path=base_path, show=show)
    sci_timesection_gt_plot(
        var,
        ds,
        depth_var=depth_var,
        base_path=base_path,
        show=show,
    )
    ts_plot(var, ds, base_path=base_path, show=show)


def sci_timeseries_loop(
    ds: xr.Dataset,
    *,
    depth_var: str = "depth",
    base_path: str | None = None,
    show: bool = False,
    max_workers: int | None = 1,
):
    """
    A loop/wrapper function to use a timeseries science dataset to make plots
    of all sci_vars keys.
    Specifically, for each sci_var present in the dataset, create:
    a raw timeseries plot, a timesection plot using glidertools,
    and a ts plot

    Arguments let the user specify if these plots should be saved, and/or shown

    Parameters
    ----------
    ds : xarray Dataset
        Timeseries science dataset
    depth_var : str
        The name of the depth variable to use in the plots.
        Default is depth; other common option will be 'depth_measured', if
        the CTD was turned off during a deployment
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    max_workers : int | None
        Number of workers with which to make the plots.
        If 1, a normal for loop is used.
        If None, all cores are used, as determined by os.cpu_count()
        Else, max_workers is the number of cores used

    Returns
    -------
        Nothing
    """

    _log.info("LOOP: making science timeseries plots")
    # If doing the loop, remove past plots
    if base_path is not None:
        utils.rmtree(os.path.join(base_path, timeseries_sci_path))
        utils.rmtree(os.path.join(base_path, timesection_gt_path))
        utils.rmtree(os.path.join(base_path, ts_path))

    vars_toloop = sci_vars
    if max_workers == 1:
        _log.info("Plotting with one worker, not in parallel")
        for var in vars_toloop:
            _log.debug(f"var {var}")
            sci_timeseries_plot(
                var,
                ds,
                depth_var=depth_var,
                base_path=base_path,
                show=show,
            )
            sci_timesection_gt_plot(
                var,
                ds,
                depth_var=depth_var,
                base_path=base_path,
                show=show,
            )
            ts_plot(var, ds, base_path=base_path, show=show)
    else:
        if max_workers is None:
            max_workers = max(1, os.cpu_count())  # type: ignore
        _log.info("Starting parallel plotting with %s workers", max_workers)
        task_function = functools.partial(
            sci_timeseries_loop_helper,
            ds=ds,
            depth_var=depth_var,
            base_path=base_path,
            show=show,
        )
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
        ) as executor:
            executor.map(task_function, vars_toloop)

    _log.info("Completed science timeseries plots")


def eng_timeseries_loop(
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
    max_workers: int | None = 1,
):
    """
    A loop/wrapper function to use a timeseries engineering dataset to make plots
    of all eng_vars variables.
    Specifically, for each eng_vars present in the dataset,
    create a timeseries plot

    Arguments let the user specify if these plots should be saved, and/or shown

    Parameters
    ----------
    ds : xarray Dataset
        Timeseries science dataset
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    max_workers : int | None
        Number of workers with which to make the plots.
        If 1, a normal for loop is used.
        If None, all cores are used, as determined by os.cpu_count()
        Else, max_workers is the number of cores used

    Returns
    -------
        Nothing
    """

    _log.info("LOOP: making engineering timeseries plots")
    # If doing the loop, remove past plots
    if base_path is not None:
        utils.rmtree(os.path.join(base_path, timeseries_eng_path))

    vars_toloop = eng_vars
    if max_workers == 1:
        _log.info("Plotting with one worker, not in parallel")
        for var in vars_toloop:
            _log.debug(f"var {var}")
            eng_timeseries_plot(var, ds, base_path=base_path, show=show)
    else:
        if max_workers is None:
            max_workers = max(1, os.cpu_count())  # type: ignore
        _log.info("Starting parallel plotting with %s workers", max_workers)
        task_function = functools.partial(
            eng_timeseries_plot,
            ds=ds,
            base_path=base_path,
            show=show,
        )
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
        ) as executor:
            executor.map(task_function, vars_toloop)

    _log.info("Completed engineering timeseries plots")


# def sci_ts_loop(
#     ds: xr.Dataset,
#     base_path: str | None = None,
#     show: bool = False,
#     max_workers=None,
# ):
#     """
#     A loop/wrapper function to use a timeseries science dataset to make plots
#     of all sci_vars kyes.
#     Specifically, for each sci_vars key present in the dataset,
#     create a ts plot

#     Arguments let the user specify if these plots should be saved, and/or shown

#     Parameters
#     ----------
#     ds : xarray Dataset
#         Timeseries science dataset
#     base_path : str
#         The 'base' of the plot path. If None, then the plot will not be saved
#         Intended to be the 'plotdir' output of glider.get_path_glider
#     show : bool
#         Boolean indicating if the plots should be shown before being closed
#     max_workers : int | None
#         Number of workers with which to make the plots.
#         If 1, a normal for loop is used.
#         If None, all cores are used, as determiend by os.cpu_count()
#         Else, max_workers is the number of cores used

#     Returns
#     -------
#         Nothing
#     """

#     _log.info("LOOP: making ts plots")
#     # If doing the loop, remove past plots
#     if base_path is not None:
#         utils.rmtree(os.path.join(base_path, ts_path))

#     vars_toloop = sci_vars
#     if max_workers == 1:
#         _log.info("Plotting with one worker, not in parallel")
#         for var in vars_toloop:
#             _log.debug(f"var {var}")
#             ts_plot(var, ds, base_path=base_path, show=show)
#     else:
#         if max_workers is None:
#             max_workers = max(1, os.cpu_count()) # type: ignore
#         _log.info("Starting parallel plotting with %s workers", max_workers)
#         task_function = functools.partial(
#             ts_plot,
#             ds=ds,
#             base_path=base_path,
#             show=show,
#         )
#         with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
#             executor.map(task_function, vars_toloop)

#     _log.info("Completed ts plots")


def sci_surface_map_loop(
    ds: xr.Dataset,
    crs,
    base_path: str | None = None,
    show: bool = False,
    bar: xr.Dataset | None = None,
    figsize_x: float = 8.5,
    figsize_y: float = 11,
    max_workers: int | None = 1,
):
    """
    A loop/wrapper function to use a timeseries science dataset to make plots
    of all sci_vars keys.
    Specifically, for each sci_vars key present in the dataset,
    create a surface map using the bar dataset

    Arguments let the user specify if these plots should be saved, and/or shown

    Parameters
    ----------
    ds : xarray Dataset
        Gridded science dataset
    crs:
        cartopy.crs projection to be used for the map
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    bar : xarray Dataset
        ETOPO dataset with which to make contour lines
    max_workers : int | None
        Number of workers with which to make the plots.
        If 1, a normal for loop is used.
        If None, all cores are used, as determined by os.cpu_count()
        Else, max_workers is the number of cores used

    Returns
    -------
        Nothing
    """

    _log.info("LOOP: making surface maps")
    # If doing the loop, remove past plots
    if base_path is not None:
        utils.rmtree(os.path.join(base_path, surfacemap_sci_path))

    vars_toloop = sci_vars
    if max_workers == 1:
        _log.info("Plotting with one worker, not in parallel")
        for var in vars_toloop:
            _log.debug(f"var {var}")
            sci_surface_map(
                var=var,
                ds=ds,
                crs=crs,
                base_path=base_path,
                show=show,
                bar=bar,
                figsize_x=figsize_x,
                figsize_y=figsize_y,
            )
    else:
        if max_workers is None:
            max_workers = max(1, os.cpu_count())  # type: ignore
        _log.info("Starting parallel plotting with %s workers", max_workers)
        task_function = functools.partial(
            sci_surface_map,
            ds=ds,
            crs=crs,
            base_path=base_path,
            show=show,
            bar=bar,
            figsize_x=figsize_x,
            figsize_y=figsize_y,
        )
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
        ) as executor:
            executor.map(task_function, vars_toloop)

    _log.info("Completed surface maps")


def save_plot(fig: matplotlib.figure.Figure, fname: str):
    """
    Wrapper function to save the matplotlib 'figure' object to 'fname'.
    Create directory to 'fname' if necessary.

    Parameters
    ----------
    fig : matplotlib.figure.Figure
        figure to save
    fname : str
        See https://matplotlib.org/stable/api/_as_gen/matplotlib.figure.Figure.savefig.html

    Returns
    -------
    Nothing
    """
    file_dir = os.path.dirname(fname)
    if not os.path.isdir(file_dir):
        os.makedirs(file_dir)

    _log.debug(f"Saving {fname}")
    fig.savefig(fname)


def scatter_plot(
    ds: xr.Dataset,
    ds_type: str,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Saves the plot to 'scatter_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Science, engineering, or raw timeseries dataset.
    ds_type : str
        dataset (timeseries) type: one of 'sci', 'eng', or 'raw'
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object

    """

    # Prep
    _log.info(f"Making basic scatter plot for {ds_type} timeseries")
    deployment = ds.deployment_name
    title_str = f"{deployment}: All points for {ds_type} timeseries"

    # Plot
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.scatter(ds.longitude, ds.latitude, s=3)

    # Labels and title
    ax.set_xlabel("Longitude", size=label_size)
    ax.set_ylabel("Latitude", size=label_size)
    ax.set_title(title_str, size=title_size)
    ax.grid(True)

    if base_path is not None:
        fname = os.path.join(
            base_path,
            scatter_path,
            f"{deployment}_{ds_type}_scatter.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def scatter_drop_plot(
    ds: xr.Dataset,
    todrop: NDArray[np.bool_],
    ds_type: str,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Saves the plot to 'scatter_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Science or engineering timeseries dataset.
    todrop : numpy.array
        Boolean array indicating which points will be dropped,
        and thus should be colored differently.
        Must be smae length as the time dimension of ds
    ds_type : str
        dataset (timeseries) type: one of 'sci' or 'eng'
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    # Prep
    _log.info(f"Making drop scatter plot for {ds_type} timeseries")
    deployment = ds.deployment_name

    color_kept = "#0072B2"  # Blue
    color_dropped = "#D55E00"  # Orange
    colors = np.where(todrop, color_dropped, color_kept)

    # Plot
    fig, ax = plt.subplots(figsize=(8, 6))

    ax.scatter(ds.longitude, ds.latitude, c=colors, s=3)

    # Labels and title
    ax.set_xlabel("Longitude", size=label_size)
    ax.set_ylabel("Latitude", size=label_size)
    ax.set_title(
        f"{deployment}: Dropped points for {ds_type} timeseries",
        size=title_size,
    )
    ax.grid(True)

    legend_elements = [
        Patch(facecolor=color_dropped, label="Dropped"),
        Patch(facecolor=color_kept, label="Kept"),
    ]
    ax.legend(handles=legend_elements)

    # Save and show
    if base_path is not None:
        fname = os.path.join(
            base_path,
            scatter_path,
            f"{deployment}_{ds_type}_dropped.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def sci_timesection_plot(
    var: str,
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Create timesection plots: variable plotted on time and depth
    Saves the plot to 'timesection_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Gridded glider science dataset.
        This is intended to be a gridded dataset produced by slocum.binary_to_nc
    var : str
        The name of the variable to plot
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars):
        _log.info("Variable name %s not present in ds. Skipping plot", var)
        return
    if var in ["profile_index"]:
        _log.info("Skipping %s because it is not relevant", var)
        return

    _log.info(f"Making timesection plot for variable {var}")
    deployment = ds.deployment_name
    # glider = utils.split_deployment(deployment)[0]
    project = ds.project
    ds = ds.dropna(dim="depth", how="all")

    fig, ax = plt.subplots(figsize=(11, 8.5))
    std = np.nanstd(ds[var])
    mean = np.nanmean(ds[var])

    p1 = ax.pcolormesh(ds.time, ds.depth, adj_var(ds, var), cmap=sci_vars[var])
    fig.colorbar(p1).set_label(label=adj_var_label(ds, var), size=label_size)
    ax.invert_yaxis()

    ax.set_title(
        f"Deployment {deployment} for project {project}\n std={std:0.2f} mean={mean:0.2f}",
        size=14,
    )  # use set_title so that title is centered over the plot
    ax.set_xlabel("Time", size=label_size)
    ax.set_ylabel("Depth [m]", size=label_size)
    # t = ax.text(0, -0.18, caption, horizontalalignment='left', verticalalignment='center', transform=ax.transAxes, wrap=True)

    # for label in ax.get_xticklabels(which='major'):
    #     label.set(rotation=15, horizontalalignment='center')
    fig.autofmt_xdate()
    # fig_cnt += 1

    if base_path is not None:
        fname = os.path.join(
            base_path,
            timesection_path,
            f"{deployment}_{var}_timesection.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def sci_spatialsection_plot(
    var: str,
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Create spatialsection plots: variable plotted on lat or lon, and depth
    Saves the plot to 'spatialsection_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Gridded glider science dataset
    var : str
        The name of the variable to plot
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars):
        _log.info("Variable name %s not present in ds. Skipping plot", var)
        return
    if var in ["profile_index"]:
        _log.info("Skipping %s because it is not relevant", var)
        return

    _log.info(f"Making spatialsection plot for variable {var}")
    deployment = ds.deployment_name
    project = ds.project
    ds = ds.dropna(dim="depth", how="all")

    fig, axs = plt.subplots(1, 2, figsize=(11, 8.5), sharey=True)
    std = np.nanstd(ds[var])
    mean = np.nanmean(ds[var])

    ### Lon
    ds0 = ds.sortby("longitude")
    axs[0].pcolormesh(
        ds0.longitude,
        ds0.depth,
        adj_var(ds0, var),
        cmap=sci_vars[var],
    )
    axs[0].invert_yaxis()
    axs[0].set_xlabel("Longitude [Deg]", size=label_size)
    axs[0].set_ylabel("Depth [m]", size=label_size)
    axs[0].text(
        0.05,
        0.95,
        "A.",
        size=16,
        ha="left",
        fontweight="bold",
        transform=axs[0].transAxes,
        color="white",
        antialiased=True,
    )

    ### Lat
    ds1 = ds.sortby("latitude")
    p2 = axs[1].pcolormesh(
        ds1.latitude,
        ds1.depth,
        adj_var(ds1, var),
        cmap=sci_vars[var],
    )
    fig.colorbar(p2).set_label(label=adj_var_label(ds, var), size=label_size)
    # axs[1].invert_yaxis()

    axs[1].set_xlabel("Latitude [Deg]", size=label_size)
    axs[1].text(
        0.05,
        0.95,
        "B.",
        size=16,
        ha="left",
        fontweight="bold",
        transform=axs[1].transAxes,
        color="white",
        antialiased=True,
    )
    # axs[1].set_ylabel(f"Depth [m]", size=14)

    fig.suptitle(
        f"Deployment {deployment} for project {project}\n std={std:0.2f} mean={mean:0.2f}",
        size=title_size,
    )

    # t = fig.text(0, -0.18, caption, horizontalalignment='left', verticalalignment='center', transform=axs[0].transAxes, wrap=True)

    for label in axs[0].get_xticklabels(which="major"):
        label.set(rotation=15, horizontalalignment="center")
    for label in axs[1].get_xticklabels(which="major"):
        label.set(rotation=15, horizontalalignment="center")
    # fig_cnt += 1

    if base_path is not None:
        fname = os.path.join(
            base_path,
            spatialsection_path,
            f"{deployment}_{var}_spatialSections.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def sci_spatialgrid_plot(
    var: str,
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Create spatial grid plots: plot variable value by lat/lon/depth
    Saves the plot to 'spatialgrid_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Gridded glider science dataset.
        This is intended to be a gridded dataset produced by slocum.binary_to_nc
    var : str
        The name of the variable to plot
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider.
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars):
        _log.info("Variable name %s not present in ds. Skipping plot", var)
        return
    if var in ["profile_index"]:
        _log.info("Skipping %s because it is not relevant", var)
        return

    _log.info(f"Making spatialgrid plot for variable {var}")
    gs = GridSpec(
        5,
        5,
        left=0.1,
        right=0.9,
        bottom=0.1,
        top=0.9,
        wspace=0.05,
        hspace=0.05,
    )
    deployment = ds.deployment_name
    project = ds.project
    ds = ds.dropna(dim="depth", how="all")

    fig = plt.figure(figsize=(11, 8.5))

    ax0 = fig.add_subplot(gs[0:3, 0:3])
    ax1 = fig.add_subplot(gs[3:5, 0:3])
    ax2 = fig.add_subplot(gs[0:3, 3:5])
    # std = np.nanstd(sci_ds_g[var])
    # mean = np.nanmean(sci_ds_g[var])

    # _,_,var_ = np.meshgrid(sci_ds_g.longitude.values, sci_ds_g.latitude.values, sci_ds_g[var].sel(depth=0, method='nearest'))
    # ax0.pcolormesh(sci_ds_g.longitude, sci_ds_g.latitude, var_[:,:,0], cmap=sci_vars[var])

    p = ax0.scatter(
        ds.longitude,
        ds.latitude,
        c=ds[var].sel(depth=0, method="nearest"),
        cmap=sci_vars[var],
    )

    ax0.set_ylabel("Latitude [Deg]", size=label_size)
    ax0.set_xticks([])
    ax0.set_xticklabels([])

    # ax0.scatter(sci_ds.longitude, sci_ds.latitude, c=sci_ds[var], cmap=sci_vars[var])
    ds1 = ds.sortby("longitude")
    ax1.pcolormesh(ds1.longitude, ds1.depth, adj_var(ds1, var), cmap=sci_vars[var])
    ax1.set_ylabel("Depth [m]", size=label_size)
    ax1.set_xlabel("Longitude [Deg]", size=label_size)
    ax1.invert_yaxis()

    ds2 = ds.sortby("latitude")
    ax2.pcolormesh(
        ds2.depth,
        ds2.latitude,
        np.transpose(adj_var(ds2, var).values),
        cmap=sci_vars[var],
        # shading="gouraud",
    )
    ax2.set_xlabel("Depth [m]", size=label_size)
    ax2.set_yticks([])
    ax2.set_yticklabels([])

    fig.colorbar(p, location="top", ax=[ax2, ax0]).set_label(
        label=adj_var_label(ds, var),
        size=label_size,
    )
    fig.suptitle(f"Deployment {deployment} for project {project}", size=title_size)

    if base_path is not None:
        fname = os.path.join(
            base_path,
            spatialgrid_path,
            f"{deployment}_{var}_spatialGrids.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def eng_plots_to_make(ds: xr.Dataset):
    """
    Create dictionary used to make engineering plots.
    This output is intended to be passed to eng_tvt_plot()

    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider raw dataset.
        This is intended to be produced by slocum.binary_to_nc

    Returns
    -------
    Dictionary used by eng_tvt_plot to make plots
    """

    da_c_depth = ds["target_depth"].dropna(dim="time")
    da_m_depth = ds["depth_measured"].interp(time=da_c_depth.time)

    plots_to_make = {
        "oilVol": {
            "X": ds["commanded_oil_volume"],
            "Y": [ds["measured_oil_volume"]],
            "C": ["C0"],
            "cb": None,
        },
        "diveEnergy": {
            "X": ds["total_num_inflections"],
            "Y": [ds["amphr"], ds["total_amphr"]],
            "C": ["C0", "C1"],
            "cb": None,
        },
        "diveDepth": {
            "X": da_c_depth,
            "Y": [da_m_depth],
            "C": ["C0"],
            "cb": None,
        },
        # "inflections": {
        #     "X": ds["total_num_inflections"],
        #     "Y": [ds["total_amphr"]],
        #     "C": ["C0"],
        #     "cb": None,
        # },
        "diveAmpHr": {
            "X": ds["depth_measured"],
            "Y": [ds["amphr"]],
            "C": ["C0"],
            "cb": None,
        },
        "leakDetect": {
            "X": ds["time"],
            "Y": [
                ds["leak_detect"].rolling(time=900, min_periods=10).mean(),
                ds["leak_detect_forward"].rolling(time=900, min_periods=10).mean(),
                ds["leak_detect_science"].rolling(time=900, min_periods=10).mean(),
            ],
            "C": ["C0", "C1", "C2"],
            "cb": None,
        },
        "vacuumDepth": {
            "X": ds["time"],
            "Y": [ds["vacuum"]],
            "C": [ds["depth_measured"]],
            "cb": "depth",
        },
    }

    return plots_to_make


def eng_tvt_plot(
    key: str,
    ds: xr.Dataset,
    eng_dict: dict,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Creates 'this vs that' plots of engineering variables
    Saves the plot to 'tvt_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider raw dataset.
        This is intended to be produced by slocum.binary_to_nc
    eng_dict : dictionary
        Dictionary produced by eng_plots_to_make()
        Used by this function to get
    key : str
        The name of the variable (i.e., key from eng_dict) to plot
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if key not in list(eng_dict.keys()):
        raise ValueError(f"Variable name {key} not present in eng_dict. Skipping plot")

    deployment = ds.deployment_name
    _log.info(f"Making tvt plot for dictionary key {key}")

    fig, ax = plt.subplots(figsize=(8.5, 8.5))


    for i in range(len(eng_dict[key]["Y"])):
        if key == "oilVol":
            plot = ax.scatter(eng_dict[key]["X"], eng_dict[key]["Y"][i])
        else:
            plot = ax.scatter(
                eng_dict[key]["X"],
                eng_dict[key]["Y"][i],
                label=eng_dict[key]["Y"][i].name,
                c=eng_dict[key]["C"][i],
            )

        if eng_dict[key]["cb"] is not None:
            cbar = fig.colorbar(plot)
            cbar.set_label(eng_dict[key]["cb"])

    ax.set_xlabel(eng_dict[key]["X"].name, size=label_size)
    ax.set_ylabel(eng_dict[key]["Y"][0].name, size=label_size)

    if len(eng_dict[key]["C"]) > 1:
        if key == "DiveEnergy":
            ax.legend(loc="upper left")
        else:
            ax.legend(loc="best")

    if eng_dict[key]["X"].name == "time":
        fig.autofmt_xdate()

    if base_path is not None:
        fname = os.path.join(base_path, tvt_path, f"{deployment}_{key}_engtvt.png")
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def eng_timeseries_plot(
    var: str,
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Create timeseries plots of engineering variables
    Saves the plot to 'timeseries_eng_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider engineering dataset.
        This is intended to be produced by slocum.binary_to_nc
    var : str
        The name of the variable to plot
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars):
        _log.info(f"Variable name {var} not present in ds. Skipping plot")
        return

    _log.info(f"Making eng timeseries plot for variable {var}")
    deployment = ds.deployment_name
    project = ds.project

    fig, ax = plt.subplots(figsize=(11, 8.5))

    ax.set_xlabel("Time", size=label_size)
    ax.set_ylabel(adj_var_label(ds, var), size=label_size)
    # ax.invert_yaxis()
    ax.set_title(f"Deployment {deployment} for project {project}", size=title_size)

    ax.scatter(ds.time, ds[var], s=3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    # fig.colorbar(p, location="right").set_label(adj_var_label(ds, var), size=label_size)
    fig.autofmt_xdate()

    if base_path is not None:
        fname = os.path.join(
            base_path,
            timeseries_eng_path,
            f"{deployment}_{var}_timeseries.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def sci_timeseries_plot(
    var: str,
    ds: xr.Dataset,
    *,
    depth_var: str = "depth",
    base_path: str | None = None,
    show: bool = False,
):
    """
    Create timeseries plots of science variables
    Saves the plot to 'timeseries_sci_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider science dataset.
        This is intended to be a gridded dataset produced by slocum.binary_to_nc
    var : str
        The name of the variable to plot
    depth_var : str
        The name of the depth variable to use in the plots.
        Default is depth; other common option will be 'depth_measured', if
        the CTD was turned off during a deployment
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars):
        _log.info("Variable name %s not present in ds. Skipping plot", var)
        return
    if var in ["profile_index"]:
        _log.info("Skipping %s because it is not relevant", var)
        return

    _log.info(f"Making sci timeseries plot for variable {var}")
    deployment = ds.deployment_name
    project = ds.project

    fig, ax = plt.subplots(figsize=(11, 8.5))

    ax.set_xlabel("Time", size=label_size)
    ax.set_ylabel("Depth [m]", size=label_size)
    ax.invert_yaxis()
    ax.set_title(f"Deployment {deployment} for project {project}", size=title_size)

    p = ax.scatter(ds.time, ds[depth_var], c=adj_var(ds, var), cmap=sci_vars[var], s=3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    fig.colorbar(p, location="right").set_label(adj_var_label(ds, var), size=label_size)
    fig.autofmt_xdate()

    if base_path is not None:
        fname = os.path.join(
            base_path,
            timeseries_sci_path,
            f"{deployment}_{var}_timeseries.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def sci_timesection_gt_plot(
    var: str,
    ds: xr.Dataset,
    *,
    depth_var: str = "depth",
    base_path: str | None = None,
    show: bool = False,
    robust: bool = True,
):
    """
    Create 'timesection' plots of 1m gridded science variables
    Saves the plot to 'timesection_gt_path' folder within 'base_path'.

    This function uses glidertools.plot, which makes functinally the same plot
    as sci_timesection_plot. Differences include that this function:
    - expressly grids data into 1m bins
    - uses the 0.5 and 99.5 percentile to set color limits
    - plots profile index  on the x axis, rather than time

    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider science dataset.
        This is intended to be a gridded dataset produced by slocum.binary_to_nc
    var : str
        The name of the variable to plot
    depth_var : str
        The name of the depth variable to use in the plots.
        Default is depth; other common option will be 'depth_measured', if
        the CTD was turned off during a deployment
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    robust : bool
        Default True. Passed to glidertools.plot:
        "robust - use the 0.5 and 99.5 percentile to set color limits"

    Returns
    -------
        matplotlib.Figure.figure object
    """
    import glidertools as gt

    if var not in list(ds.data_vars):
        _log.info("Variable name %s not present in ds. Skipping plot", var)
        return
    if var in ["profile_index"]:
        _log.info("Skipping %s because it is not relevant", var)
        return

    _log.info(
        f"Making sci timesection plot for variable {var}, using glidertools",
    )
    deployment = ds.deployment_name
    project = ds.project

    dat = ds.where(ds["profile_index"] % 1 == 0, drop=True)
    x = dat["profile_index"]
    y = dat[depth_var]

    fig, ax = plt.subplots(figsize=(11, 8.5))
    ax = gt.plot(x, y, adj_var(dat, var), cmap=sci_vars[var], ax=ax, robust=robust)
    ax.set_xlabel("Profile", size=label_size)
    ax.set_ylabel("Depth [m]", size=label_size)
    ax.set_title(f"Deployment {deployment} for project {project}", size=title_size)

    # Sometimes glidertools won't plot label, so guarantee it
    ax.cb.set_label(adj_var_label(ds, var), size=label_size)  # type: ignore

    if base_path is not None:
        fname = os.path.join(
            base_path,
            timesection_gt_path,
            f"{deployment}_{var}_gt.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def ts_plot(
    var: str,
    ds: xr.Dataset,
    base_path: str | None = None,
    show: bool = False,
):
    """
    Create ts plots of science variables
    Saves the plot to 'ts_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Timeseries glider science dataset.
        This is intended to be produced by slocum.binary_to_nc
    var : str
        The name of the variable to plot
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars):
        _log.info(f"Variable name {var} not present in ds. Skipping plot")
        return

    if var in ["potential_temperature", "profile_index"]:
        _log.info(f"Skipping {var} because it is not relevant for TS plots")
        return

    _log.info(f"Making ts plot for variable {var}")
    deployment = ds.deployment_name
    start = ds.deployment_start[0:10]
    end = ds.deployment_end[0:10]

    Sg, Tg, sigma = utils.calc_ts(ds)

    fig, ax = plt.subplots(figsize=(9.5, 8.5))

    C0 = ax.contour(Sg, Tg, sigma, colors="grey", zorder=1)
    plt.clabel(C0, colors="k", fontsize=9)
    p0 = ax.scatter(
        ds.salinity,
        ds.potential_temperature,
        c=adj_var(ds, var),
        cmap=sci_vars[var],
        s=5,
    )
    fig.colorbar(
        p0,
        orientation="vertical",
        location="right",
        shrink=1,
    ).set_label(label=adj_var_label(ds, var), size=label_size)

    ax.set_title(f"{deployment} from {start} to {end}", size=title_size)
    ax.set_xlabel(adj_var_label(ds, "salinity"), size=label_size)
    ax.set_ylabel(adj_var_label(ds, "potential_temperature"), size=label_size)
    # ax.set_xlabel("Salinity [PSU]", size=label_size)
    # ax.set_ylabel("Potential temperature [°C]", size=label_size)

    if base_path is not None:
        fname = os.path.join(base_path, ts_path, f"{deployment}_{var}_tsPlot.png")
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig


def crs_map(crs_str: str):
    """
    Map string crs_str to a Cartopy projection.
    Returns a cartopy.crs class

    This is convenience function, so that a user looking to use
    sci_surface_map does not have to import cartopy in their script

    https://scitools.org.uk/cartopy/docs/latest/reference/projections.html
    """
    if crs_str == "PlateCarree":
        return ccrs.PlateCarree()
    elif crs_str == "Mercator":
        return ccrs.Mercator()
    else:
        raise ValueError(f"invalid crs_str: {crs_str}")


def sci_surface_map(
    var: str,
    ds: xr.Dataset,
    crs,
    base_path: str | None = None,
    show: bool = False,
    bar: xr.Dataset | None = None,
    figsize_x: float = 8.5,
    figsize_y: float = 11,
):
    """
    Create surface maps of science variables,
    plotting the mean/lat/lon for each profile.
    Saves the plot to 'surfacemap_sci_path' folder within 'base_path'

    Parameters
    ----------
    ds : xarray dataset
        Gridded science dataset
    var : str
        The name of the variable to plot
    crs :
        An instantiated cartopy projection, such as cartopy.crs.PlateCarree()
        or cartopy.crs.Mercator()
        If crs is a string, then it is passed to plots.crs_map()
    base_path : str
        The 'base' of the plot path. If None, then the plot will not be saved
        Intended to be the 'plotdir' output of glider.get_path_glider
    show : bool
        Boolean indicating if the plots should be shown before being closed
    bar : xarray dataset
        dataset of ETOPO 15 arc-second global relief model.
        Likely downloaded from ERDDAP - must span glider lat/lon
        A future task is to make a function for pulling this file from ERDDAP, if necessary
        Eg: https://coastwatch.pfeg.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.nc?z%5B(30):1:(45)%5D%5B(-135):1:(-120)%5D

    Returns
    -------
        matplotlib.Figure.figure object
    """

    if var not in list(ds.data_vars) and var != "profile_index":
        _log.info("Variable name %s not present in ds. Skipping plot", var)
        return

    _log.info(f"Making surface map for variable {var}")
    deployment = ds.deployment_name
    start = ds.deployment_start[0:10]
    end = ds.deployment_end[0:10]

    map_lon_border = 0.1
    map_lat_border = 0.2
    glider_lon_min = ds.longitude.min()
    glider_lon_max = ds.longitude.max()
    glider_lat_min = ds.latitude.min()
    glider_lat_max = ds.latitude.max()

    # Using Cartopy
    if isinstance(crs, str):
        crs = crs_map(crs)
    fig, ax = plt.subplots(
        figsize=(figsize_x, figsize_y),
        subplot_kw={"projection": crs},
    )
    ax = typing.cast(GeoAxes, ax)  # Explicitly cast to GeoAxes, for Pylance

    ax.set_xlabel("\n\n\nLongitude [Deg]", size=14)
    ax.set_ylabel("Latitude [Deg]\n\n\n", size=14)

    # Set extent of the map based on the glider data
    ax.set_extent(
        [
            glider_lon_min - map_lon_border,
            glider_lon_max + 3 * map_lon_border,
            glider_lat_min - map_lat_border,
            glider_lat_max + map_lat_border,
        ],
        crs=ccrs.PlateCarree(),
    )

    # Add coastlines, countries, and continents
    # ax.coastlines(resolution="110m", linestyle="-", color="black")
    ax.add_feature(cfeature.LAND, edgecolor="black", facecolor="#e0b479")
    ax.add_feature(cfeature.OCEAN, edgecolor="none", facecolor="#7bcbe3")

    # Add parallels and meridians; no scale bar since no built-in function
    gl = ax.gridlines(draw_labels=["bottom", "left"])  # type: ignore
    gl.xlabel_style = {"rotation": 15}

    # Special considerations for profile_index
    if var == "profile_index":
        c = ds["profile"]
        title = f"{deployment}: {var}\nfrom {start} to {end}"
        cbar_label = "profile_index [1]"
    else:
        c = ds[var].where(ds.depth <= 10, drop=True).mean(dim="depth")
        title = f"{deployment}: 0 - 10m average {var}\nfrom {start} to {end}"
        cbar_label = adj_var_label(ds, var)

    # Scatter plot data given data, colored by values in the top 10m
    p = ax.scatter(
        ds.longitude,
        ds.latitude,
        c=c,
        cmap=sci_vars[var],
        s=10,
        zorder=2.5,
        transform=ccrs.PlateCarree(),
    )

    if bar is not None:
        lon, lat = np.meshgrid(bar.z.lon, bar.z.lat)
        C0 = ax.contour(
            lon,
            lat,
            bar.z,
            levels=4,
            colors="grey",
            transform=ccrs.PlateCarree(),
        )
        ax.clabel(C0, colors="grey", fontsize=9, inline=True)
        # m.contourf(lon, lat, bar.z, cmap="Pastel1")

    # Add colorbar and title
    fig.colorbar(p, ax=ax, shrink=0.6, location="right").set_label(
        label=cbar_label,
        size=label_size,
    )
    ax.set_title(title, size=title_size)

    if base_path is not None:
        fname = os.path.join(
            base_path,
            surfacemap_sci_path,
            f"{deployment}_{var}_map_0-10.png",
        )
        save_plot(fig, fname)

    if show:
        plt.show()
    plt.close(fig)

    return fig
