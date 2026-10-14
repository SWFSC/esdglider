import collections
import json
import logging

import numpy as np
import pandas as pd
import xarray as xr

_log = logging.getLogger(__name__)


_profile_idx_comment = (
    "N = inside profile N, N + 0.5 = between profiles N and N + 1. "
    + "Parameters listed as attributes"
)

prof_optionsList = {
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

    The only edits are:
        a) Pre-commit formatting and
        b) Updating the default kwargs optional argument values, and
        c) Returning the optionsList dictionary (options used for profile detection). 
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
            optionsList (dict): A dictionary of the options used for profile detection.
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

    # Filter for relevant kwargs, in case any others got passed in
    # Added because optionsList is now returned
    optionsList = prof_optionsList.copy()
    kwargs = {key: value for key, value in kwargs.items() if key in optionsList}
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
    profileDirection = np.empty(len(depth))
    profileDirection[:] = np.nan

    for i in range(len(validIndex) - 1):
        iStart = validIndex[i]
        iEnd = validIndex[i + 1]
        profileDirection[iStart:iEnd] = sdy[i]

    return profileIndex, profileDirection, optionsList


def get_fill_profiles(
        ds: xr.Dataset, 
        time_var: str, 
        depth_var: str, 
        prof_args: dict | None = None
    ) -> xr.Dataset:
    """
    Calculate profile index and direction values,
    and fill both the values and attributes into ds

    ds : `xarray.Dataset`
    time_var, depth_var: Variable names of time and depth in ds
        Values from these variables passed directly to findProfiles function
    prof_args : dict | None
        Optional named arguments for findProfiles function
        If None, default values are used

    returns Dataset
    """
    # if np.any(np.isnan(ds.depth.values)):
    #     num_nan = sum(np.isnan(ds.depth.values))
    #     _log.warning(f"There are {num_nan} nan depth values")

    time_vals = ds[time_var].values
    depth_vals = ds[depth_var].values
    if prof_args is None:
        prof_args = {}
    prof_idx, prof_dir, prof_opt = findProfiles(
        time_vals, depth_vals, **prof_args
    )


    idx_attrs = collections.OrderedDict(
        [
            ("long_name", "profile index"),
            ("units", "1"),
            ("comment", _profile_idx_comment),
            ("sources", f"{time_var} {depth_var}"),
            ("method", "esdglider.utils.findProfiles"),
            ("method_configuration", json.dumps(prof_opt))
        ], 
    )
    ds["profile_index"] = (time_var, prof_idx, idx_attrs)

    dir_attrs = collections.OrderedDict(
        [
            ("long_name", "glider vertical speed direction"),
            ("units", "1"),
            ("comment", "-1 = ascending, 0 = inflecting or stalled, 1 = descending"),
            ("sources", f"{time_var} {depth_var}"),
            ("method", "esdglider.utils.findProfiles"),
        ],
    )
    ds["profile_direction"] = (time_var, prof_dir, dir_attrs)

    _log.debug(f"There are {np.max(ds.profile_index.values)} profiles")

    return ds


def join_profiles(
        ds: xr.Dataset, 
        df: pd.DataFrame,  
        # prof_depth_var: str,
        # prof_args: dict | None = None, 
        prof_index_attrs: dict,
    ) -> xr.Dataset:
    """
    'Join' profile indexes to a dataset by time windows,
    using a summary dataframe with profile start and end times

    Parameters
    ----------
    ds : xarray.Dataset
        Timeseries dataset, onto which to join the profiles from `df`
    df : pandas.DataFrame
        Profile summary dataframe; output of `calc_profile_summary()`
        Defines the desired profiles
    prof_depth_var : str
        Variable name of depth used to calculate profile vars
    prof_args : dict
        findProfile arguments, included here for metadata
    prof_index_attrs : dict
        Attributes for the profile_index variable.

    Returns
    -------
    xarray.dataset
        Dataset ds, with new profile_index column
    """

    # if prof_args is None or prof_args == {}:
    #     prof_args = prof_optionsList.copy()

    # Determine profile windows
    time_values = ds["time"].values
    idx_values = np.full(time_values.shape, np.nan, dtype=np.float64)
    for _, row in df.iterrows():
        # time start/ends are mutually exclusive, so use >= and <=
        mask = (time_values >= row["start_time"]) & (time_values <= row["end_time"])
        idx_values[mask] = row["profile_index"]

    # Sanity checks, if relevant
    if "profile_index" in ds:
        abs_idx_diff = abs(ds.profile_index.values - idx_values).max()
        if abs_idx_diff > 1:
            _log.warning(
                "The absolute value of the old minus new index values is %d",
                abs_idx_diff,
            )

    if any(np.isnan(idx_values)):
        _log.warning(
            "There are %d nan profile index values",
            np.count_nonzero(np.isnan(idx_values)),
        )

    # Add attributes to dataset
    # if prof_index_attrs is None:
    #     prof_index_attrs = collections.OrderedDict(
    #         [
    #             ("long_name", "profile index"),
    #             ("units", "1"),
    #             ("comment", _profile_idx_comment),
    #             ("sources", f"time {prof_depth_var}"),
    #             ("method", "esdglider.utils.findProfiles"),
    #             ("method_configuration", json.dumps(prof_args))
    #         ], 
    #     )
    ds["profile_index"] = ("time", idx_values, prof_index_attrs)

    return ds


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
    depth_nona = group["depth_p"].dropna().values
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
    surface_pts = group["time"][group["depth_p"] <= tas_depth]
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
    A surfacing only occurs if profile_direction=0 and profile min_depth<1

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
    ds = ds.rename({depth_var: "depth_p"})
    grouped_columns = [
        "time",
        "depth_p",
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

    return df # type: ignore


def check_profiles(
    df: pd.DataFrame,     
    pair_tol : float = 3.0, 
    min_swing: float = 10.0
) -> pd.DataFrame:
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
    pair_tol : float, optional
        Maximum allowed difference between paired start depths, by default 3.0
        Passed directly to check_start_depth_pairs
    min_swing : float, optional
        Minimum required swing between consecutive pairs, by default 10.0
        Passed directly to check_start_depth_pairs

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

    # Additional checks
    check_start_depth_pairs(df, "start_depth", pair_tol=pair_tol, min_swing=min_swing)
    validate_glider_profiles(df)

    _log.info("Completed profile checks")
    return df


def check_start_depth_pairs(
    df: pd.DataFrame, 
    depth_col: str = "start_depth", 
    pair_tol : float = 3.0, 
    min_swing: float = 10.0
) -> None:
    """
    Check for start depth pair mismatches and alternation issues in glider profile data.
    Code generated by Gemini, and reviewed by Sam Woodman

    Parameters
    ----------
    df : pandas.DataFrame
        DataFrame containing glider profile data.
    depth_col : str, optional
        Column name for start depths, by default "start_depth"
    pair_tol : float, optional
        Maximum allowed difference between paired start depths, by default 3.0
    min_swing : float, optional
        Minimum required swing between consecutive pairs, by default 10.0

    Returns
    -------
    None
    """
    _log.debug("Starting check of profile start depth pairs")

    # .to_numpy(dtype=float) explicitly returns a float np.ndarray, clearing the type error
    depths: np.ndarray = df[depth_col].to_numpy(dtype=float)

    # If odd number of rows, hold out the final row (typically final surfacing)
    n_pairs = len(depths) // 2
    r1 = depths[0 : 2 * n_pairs : 2]  # Even indices: 0, 2, 4, ...
    r2 = depths[1 : 2 * n_pairs : 2]  # Odd indices:  1, 3, 5, ...

    # 1. Check pair difference
    pair_diff = np.abs(r1 - r2)
    pair_failures = np.where(pair_diff > pair_tol)[0]

    # 2. Check alternation between consecutive pairs (shallow vs deep)
    pair_means = (r1 + r2) / 2.0
    swings = np.abs(np.diff(pair_means))
    alternation_failures = np.where(swings < min_swing)[0]

    # Report results
    _log.debug(
        "Evaluated %d pairs (%d rows, %d trailing row).",
        n_pairs,
        len(depths),
        len(depths) % 2,
    )

    if len(pair_failures) == 0:
        _log.debug(
            "All pairs match within %f m (max diff: %f m).",
            pair_tol,
            pair_diff.max(),
        )
    else:
        for idx in pair_failures:
            _log.warning(
                f"[PAIR MISMATCH] Pair {idx} (Rows {2*idx}, {2*idx+1}): "
                f"depths = ({r1[idx]:.2f} m, {r2[idx]:.2f} m), diff = {pair_diff[idx]:.2f} m"
            )

    if len(alternation_failures) == 0:
        _log.debug(
            "All pairs alternate between shallow and deep (min swing: %f m).",
            swings.min(),
        )
    else:
        for idx in alternation_failures:
            _log.warning(
                "[ALTERNATION WARNING] Between Pair %d and Pair %d: "
                "mean depths are (%f m -> %f m), "
                "swing is only %f m (possible mid-water wiggle).",
                idx,
                idx + 1,
                pair_means[idx],
                pair_means[idx + 1],
                swings[idx],
            )


def validate_glider_profiles(
    df: pd.DataFrame, 
    depth_continuity_tol: float = 3.5
):
    """
    Validate glider profiles for depth continuity and phase sequence logic.
    Code generated by Gemini, and reviewed by Sam Woodman

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing glider profile data with columns 'start_depth', 'end_depth', and 'profile_phase'.
    depth_continuity_tol : float, optional
        Maximum allowed difference between the end depth of one segment and the start depth of the next segment (default is 3.5).

    Returns
    -------
    None
    """
    _log.debug("Starting profile continuity and phase sequence validation")
    # Extract typed arrays/lists once up front to avoid pandas-stubs typing issues
    profile_indices: np.ndarray = df["profile_index"].to_numpy(dtype=float)

    # 1. Depth continuity across consecutive profiles
    end_depths: np.ndarray = df["end_depth"].iloc[:-1].to_numpy(dtype=float)
    start_depths: np.ndarray = df["start_depth"].iloc[1:].to_numpy(dtype=float)
    depth_jumps: np.ndarray = np.abs(end_depths - start_depths)

    bad_jump_mask: np.ndarray = depth_jumps > depth_continuity_tol
    bad_jump_count = int(np.count_nonzero(bad_jump_mask))

    if bad_jump_count > 0:
        # Index NumPy array directly: np.ndarray[bool] is fully type-safe
        bad_indices = profile_indices[:-1][bad_jump_mask].tolist()
        _log.warning(
            "Found %s depth discontinuities (> %s m) between consecutive profiles. Profile indices: %s",
            bad_jump_count,
            depth_continuity_tol,
            ", ".join(str(i) for i in bad_indices),
        )

    # 2. Strict phase-to-phase state machine transitions
    valid_transitions: dict[str, set[str]] = {
        "surfacing": {"descent"},
        "inflection": {"descent", "ascent"},
        "descent": {"inflection"},
        "ascent": {"surfacing", "inflection"},
    }

    phases: list[str] = df["profile_phase"].astype(str).tolist()
    bad_phase_indices: list[float] = []

    for i in range(len(phases) - 1):
        allowed_next = valid_transitions.get(phases[i])
        if allowed_next is None or phases[i + 1] not in allowed_next:
            # Append the profile index directly, bypassing .iloc entirely
            bad_phase_indices.append(profile_indices[i])

    if len(bad_phase_indices) > 0:
        _log.warning(
            "Found %s invalid phase transitions. Profile indices: %s",
            len(bad_phase_indices),
            ", ".join(str(i) for i in bad_phase_indices),
        )

    if bad_jump_count == 0 and len(bad_phase_indices) == 0:
        _log.debug(
            "All %s profiles passed continuity and phase sequence checks.",
            len(df),
        )