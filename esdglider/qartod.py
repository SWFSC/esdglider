"""
QARTOD quality control utilities for esdglider.

Author
------
Madison Richardson, NOAA CoastWatch West Coast Node

Overview
--------
This module provides an operational implementation of the
IOOS QARTOD (Quality Assurance of Real-Time Oceanographic
Data) framework for ESD Glider science datasets.

The workflow is designed to operate on fully processed
science NetCDF files and generate DAC-compliant quality
control variables using the ``ioos_qc`` package. Quality
control tests are configured through a YAML-based
configuration file and applied to eligible variables
containing a time dimension.

Variables not explicitly defined in the YAML configuration
are automatically assigned default QARTOD configurations
using available variable metadata. Deployment-specific
spike and rate-of-change thresholds are then calculated
from the observations in the dataset and applied to the
in-memory configuration without modifying the YAML
configuration file.

QARTOD tests are executed one variable and test at a time
to reduce peak memory usage for large glider datasets.
Individual test results are immediately aggregated into
a single QARTOD flag array and released before the next
test is processed. The memory-intensive flat-line test is
processed in overlapping chunks to further limit memory
use while preserving the required preceding time window.

The resulting aggregate QC flags are written as
DAC-compliant ``*_qc`` variables, linked to their parent
variables through the ``ancillary_variables`` attribute,
and saved to a QC-enhanced NetCDF file suitable for
GliderDAC submission and downstream scientific analysis.
The module also provides utilities for creating
deployment-level QC summary tables. Visualization of QC
results is implemented separately in the companion
``plots.py`` module.

Workflow
--------
The operational QARTOD workflow performs the following
steps:

1. Open the input science NetCDF dataset.
2. Identify variables eligible for QARTOD testing.
3. Load the YAML QARTOD configuration.
4. Automatically generate default configurations for
   eligible variables not defined in the YAML.
5. Compute deployment-specific spike and rate-of-change
   thresholds and update the in-memory configuration.
6. Process each configured variable independently.
7. Run each configured QARTOD test independently.
8. Process flat-line tests in overlapping chunks.
9. Immediately aggregate each test result using QARTOD
   flag precedence.
10. Generate DAC-compliant ``*_qc`` variables from the
    aggregate results.
11. Create placeholder QC variables for metadata time
    variables that are not evaluated by QARTOD.
12. Add ``ioos_qc`` version provenance.
13. Write the QC-enhanced dataset to NetCDF using
    DAC-compliant QC encodings.


Key Features
------------
- IOOS QARTOD integration through ``ioos_qc``
- YAML-based QC configuration
- Automatic detection of time-varying variables
- Support for both ``latitude``/``longitude`` and
  ``lat``/``lon`` coordinate naming conventions
- Automatic generation of default QC configurations
  for unconfigured variables
- Metadata-derived gross range thresholds using
  ``valid_min`` and ``valid_max`` attributes
- Deployment-specific spike and rate-of-change
  threshold calculations
- In-memory threshold updates without modifying the
  YAML configuration file
- Variable- and test-level QARTOD processing to reduce
  peak memory usage
- Overlapping chunk-based processing for the QARTOD
  flat-line test
- Immediate aggregation and release of individual
  QARTOD test results
- Configurable flat-line chunk size
- Aggregate QC flag generation following DAC
  conventions
- Placeholder QC variables for required metadata
  fields
- DAC-compliant ``standard_name`` generation
- Preservation of existing
  ``ancillary_variables`` metadata
- QC configuration provenance stored with generated
  QC variables
- Provenance tracking through
  ``ioos_qc_version`` metadata
- DAC-compliant NetCDF encoding using:
    * int8 QC variables
    * _FillValue = -127
    * zlib compression
- Deployment-level QC summary table generation
- Plotting utilities implemented separately in the
  companion ``plots.py`` module

Generated QC Variables
----------------------
Science variables that undergo QARTOD testing receive
aggregate QC variables in the form:

    temperature      -> temperature_qc
    conductivity     -> conductivity_qc
    salinity         -> salinity_qc
    oxygen_concentration -> oxygen_concentration_qc

Metadata time variables receive placeholder QC
variables populated with the DAC fill value (-127):

    time         -> time_qc
    profile_time -> profile_time_qc
    time_uv      -> time_uv_qc

Dependencies
------------
- numpy
- xarray
- PyYAML
- ioos_qc

References
----------
IOOS QARTOD:
https://ioos.noaa.gov/project/qartod/

ioos_qc Documentation:
https://ioos.github.io/ioos_qc/

GliderDAC:
https://gliderdac.ioos.us/

Notes
-----
This module is intended for operational processing within
the ESD Glider workflow and should be executed after
science variables and metadata have been finalized. The
companion plots.py module may be used after QARTOD processing
to generate deployment-level QC summary figures, profile
summary tables, and QC flag time-series visualizations.

The generated QC variables follow IOOS DAC conventions
for flag values, standard names, ancillary variable
relationships, and NetCDF encodings.
"""

import json
import logging

import ioos_qc
import numpy as np
import pandas as pd
import xarray as xr
import yaml
from ioos_qc.config import Config
from ioos_qc.qartod import qartod_compare
from ioos_qc.results import collect_results
from ioos_qc.streams import XarrayStream

from esdglider import paths

# =========================================================
# LOGGER
# =========================================================

_log = logging.getLogger(__name__)

# =========================================================
# QC STANDARD NAME
# =========================================================


def get_qc_standard_name(ds, var_name):
    """
    Generate a DAC-compliant QC standard_name attribute for a
    quality control variable.

    This function retrieves the parent variable's
    ``standard_name`` attribute and appends
    ``" status_flag"`` to create a CF- and IOOS
    DAC-compliant QC standard name.

    If the parent variable does not contain a
    ``standard_name`` attribute, the variable name
    itself is used as the base name.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset containing the parent variable.

    var_name : str
        Name of the parent variable for which a QC
        standard_name will be generated.

    Returns
    -------
    str
        DAC-compliant QC standard_name in the form:

        ``"<standard_name> status_flag"``

    This approach automatically preserves consistency
    between the parent variable and its associated
    QARTOD QC variable while avoiding the need for
    a hard-coded variable mapping table.
    """

    # GET PARENT VARIABLE STANDARD NAME
    standard_name = ds[var_name].attrs.get("standard_name", var_name)
    # BUILD DAC-COMPLIANT QC STANDARD NAME
    return f"{standard_name} status_flag"


# =========================================================
# FIND VARIABLES WITH TIME DIMENSION
# =========================================================


def find_time_variables(ds):
    """
    Identify dataset variables that are eligible for QARTOD
    quality control testing.

    This function scans all data variables in the dataset and
    returns those that contain a time dimension and should
    participate in the QARTOD workflow. Existing QC variables,
    metadata variables, engineering variables, and redundant
    coordinate variables are excluded.

    Parameters
    ----------
    ds : xarray.Dataset
        Input glider dataset.

    Returns
    -------
    list
        List of variables eligible for QARTOD testing.

    Notes
    -----
    The following variable types are excluded:

    - Existing QC variables (``*_qc``)
    - Metadata variables that are not science observations
    - Redundant coordinate variables (``latitude``,
      ``longitude``)
    - Variables without a time dimension
    """

    # INITIALIZE OUTPUT VARIABLE LIST
    time_variables = []
    # VARIABLES TO EXCLUDE FROM QARTOD PROCESSING
    skip_variables = {
        "trajectory",
        "profile_id",
        "profile_index",
        "profile_direction",
        "profile_time",
        "time_uv",
        "heading",
        "pitch",
        "roll",
        "distance_over_ground", 
    }
    # EVALUATE ALL DATA VARIABLES
    for var in ds.data_vars:
        # REQUIRE TIME DIMENSION
        if "time" not in ds[var].dims:
            continue

        # SKIP EXISTING QC VARIABLES & EXCLUDED VARIABLES
        if var.endswith("_qc") or var in skip_variables:
            continue

        time_variables.append(var)

    _log.info("Found %d time variables for QC", len(time_variables))

    return time_variables


# =========================================================
# LOAD YAML CONFIG
# =========================================================


def load_qartod_config(config_file):
    """
    Load and parse a YAML-based QARTOD configuration file.

    This function reads a QARTOD configuration file from disk
    and converts the YAML contents into a Python dictionary
    that can be used to construct an ``ioos_qc.config.Config``
    object.

    The configuration file defines the variables to be
    evaluated, the QARTOD tests to apply, and the associated
    threshold values used during quality control processing.

    Parameters
    ----------
    config_file : str
        Path to the YAML QARTOD configuration file.

    Returns
    -------
    dict
        Parsed QARTOD configuration dictionary containing
        all contexts, streams, tests, and threshold values
        defined in the YAML file.

    Notes
    -----
    The returned dictionary is later used to construct an
    ``ioos_qc.config.Config`` object that drives execution
    of the QARTOD workflow.
    """

    # READ YAML CONFIGURATION FILE
    with open(config_file, "r") as f:
        qc_config = f.read()

    # PARSE YAML CONTENTS
    config_dict = yaml.safe_load(qc_config)

    _log.info("Loaded QARTOD config: %s", config_file)

    return config_dict


# =========================================================
# DYNAMIC THRESHOLD CALCULATIONS
# =========================================================


def get_spike_thresholds(values):
    """
    Calculate deployment-specific spike test thresholds from the
    observations being quality controlled.

    This function computes the standard deviation of the valid
    observations for a variable and derives the QARTOD spike test
    thresholds from that statistic. The suspect threshold is defined
    as one standard deviation of the deployment, while the fail
    threshold is defined as two standard deviations.

    The resulting thresholds are intended to be inserted into the
    in-memory QARTOD configuration prior to constructing the
    ``ioos_qc.config.Config`` object. This allows spike thresholds
    to be computed dynamically for each deployment while leaving the
    YAML configuration file unchanged.

    Parameters
    ----------
    values : numpy.ndarray
        One-dimensional array of observations for the variable being
        evaluated. Missing values (NaNs) are ignored when calculating
        the standard deviation.

    Returns
    -------
    tuple
        Tuple containing:

        - suspect_threshold (numpy.float64)
        - fail_threshold (numpy.float64)
        - report string describing any issues encountered during
        threshold calculation

        If fewer than two valid observations are available, the
        threshold values are returned as ``None``.

    Notes
    -----
    - Thresholds are computed from the deployment currently being
    quality controlled rather than from a historical reference
    dataset.
    - NaN values are excluded from the standard deviation
    calculation.
    - The threshold definitions follow the methodology used by the
    IOOS Glider DAC threshold generation utilities:

        suspect_threshold = 1 × std
        fail_threshold = 2 × std

    References
    ----------
    This function is based on the spike threshold calculation
    implemented by the IOOS Glider DAC:

    https://github.com/ioos/glider-dac/blob/main/glider_qc/glider_qc.py#L289-L318

    IOOS Glider DAC:
    https://ioos.noaa.gov/project/underwater-gliders/

    IOOS QARTOD:
    https://ioos.github.io/ioos_qc/api/ioos_qc.html
    """
    report_list = []
    # IF VALUES IS NOT A NUMPY ARRAY, CONVERT IT TO ONE
    if not isinstance(values, np.ndarray):
        values = np.asarray(values)

    # CHECK IF THERE ARE AT LEAST 2 VALID VALUES
    # REMOVE NaN
    valid_values = [x for x in values if not np.isnan(x)]

    if len(valid_values) < 2:
        _log.info("Not enough valid data for variance calculation.")
        report_list.append("Not enough valid data for std calculation.")
        return None, None, " ".join(report_list)
    else:
        std = np.nanstd(valid_values)

    # DEFINE THE SUSPECT AND FAIL THRESHOLDS
    suspect_threshold = np.float64(1.0 * std)
    fail_threshold = np.float64(2.0 * std)

    return suspect_threshold, fail_threshold, " ".join(report_list)


def get_rate_of_change_threshold(values, times):
    """
    Calculate a deployment-specific rate-of-change threshold from the
    observations being quality controlled.

    This function computes the maximum rate of change between
    consecutive observations after excluding values that lie outside
    one standard deviation of the deployment mean. Restricting the
    calculation to observations within one standard deviation reduces
    the influence of anomalous values when estimating the threshold.

    The resulting threshold is intended to be inserted into the
    in-memory QARTOD configuration prior to constructing the
    ``ioos_qc.config.Config`` object. This allows rate-of-change
    thresholds to be computed dynamically for each deployment while
    leaving the YAML configuration file unchanged.

    To ensure consistency with the IOOS QARTOD
    ``rate_of_change_test()``, rates of change are calculated in
    observation units per second by converting timestamp differences
    to elapsed seconds before computing the rate.

    Parameters
    ----------
    values : numpy.ndarray
        One-dimensional array of observations for the variable being
        evaluated.

    times : numpy.ndarray
        One-dimensional array of timestamps corresponding to
        ``values``. The time array is used to calculate the rate of
        change between consecutive observations.

    Returns
    -------
    tuple
        Tuple containing:

        - threshold (numpy.float64)
        - report string describing any issues encountered during
        threshold calculation

        If insufficient valid observations are available, the
        threshold value is returned as ``None``.

    Notes
    -----
    - Thresholds are computed from the deployment currently being
    quality controlled rather than from a historical reference
    dataset.
    - Observations outside one standard deviation of the deployment
    mean are excluded prior to calculating the rate of change.
    - The returned threshold is the maximum absolute rate of change
    observed between consecutive filtered observations.
    - Time differences are converted to elapsed seconds before
    calculating rates of change so that the computed thresholds are
    expressed in observation units per second, matching the
    implementation used by ``ioos_qc.qartod.rate_of_change_test()``.
    - This implementation differs slightly from the original IOOS
    Glider DAC helper function to ensure the dynamically computed
    thresholds are directly compatible with the IOOS QC test used by
    this workflow.

    References
    ----------
    This function is adapted from the rate-of-change threshold
    calculation implemented by the IOOS Glider DAC:

    https://github.com/ioos/glider-dac/blob/main/glider_qc/glider_qc.py#L233C5-L287C48

    The rate-of-change calculation has been modified to express rates
    in observation units per second, matching the implementation used
    by ``ioos_qc.qartod.rate_of_change_test()``.

    IOOS Glider DAC:
    https://ioos.noaa.gov/project/underwater-gliders/

    IOOS QARTOD:
    https://ioos.github.io/ioos_qc/api/ioos_qc.html
    """
    report_list = []
    message = (
        "Insufficient data: both 'values' and 'times' "
        "must have at least two elements."
    )

    if len(values) < 2 or len(times) < 2:
        _log.info(message)
        report_list.append(message)
        return None, " ".join(report_list)

    if (
        np.sum(~np.isnan(values)) > 1
    ):  # CHECK IF THERE ARE AT LEAST 2 VALID VALUES
        std = np.nanstd(values)
        mean = np.nanmean(values)
    else:
        report_list.append(
            "Not enough valid data points for std and mean calculations."
        )
        return None, " ".join(report_list)

    list_values = []
    list_times = []
    for nn, xx in enumerate(values):
        if (xx > (mean - std)) and (xx < (mean + std)):
            list_values.append(xx)
            list_times.append(times[nn])

    # ENSURE THERE ARE ENOUGH DATA POINTS TO COMPUTE THE RATE OF CHANGE
    if len(list_values) < 2:
        _log.info(message)
        report_list.append(message)
        return None, " ".join(report_list)

    # CALCULATE RATE OF CHANGE IN OBSERVATION UNITS PER SECOND,
    # MATCHING THE IMPLEMENATION USED BY ioos_qc.qartod.rate_of_change_test()
    roc = np.abs(
        np.diff(list_values)
        / np.diff(list_times).astype("timedelta64[s]").astype(float)
    )

    # RETURN MAX RATE OF CHANGE
    threshold = np.max(roc)

    return threshold, " ".join(report_list)


# =========================================================
# AUTO-ADD VARIABLES TO CONFIG
# =========================================================


def add_missing_variables_to_config(
    ds,
    config_dict,
    time_variables,
):
    """
    Automatically create default QARTOD configurations for variables
    that are present in the dataset but not explicitly defined in the
    QARTOD YAML configuration.

    This function ensures that all time-varying variables are eligible
    for quality control processing, even if no variable-specific
    thresholds have been provided. For variables containing
    ``valid_min`` and ``valid_max`` attributes, these metadata values
    are used to construct a default gross range test.

    If ``potential_density`` or ``potential_temperature`` do not
    contain these attributes, the corresponding attributes from
    ``density`` or ``temperature`` are used when available.

    If valid range attributes are unavailable, a permissive fallback
    gross range test is assigned to prevent workflow failures while
    still allowing the variable to participate in the QARTOD workflow.

    A default spike test and rate-of-change test are also added for all
    automatically configured variables.

    Parameters
    ----------
    ds : xarray.Dataset
        Input glider dataset containing the variables that will be
        evaluated by QARTOD.

    config_dict : dict
        Parsed QARTOD YAML configuration dictionary.

    time_variables : list
        List of dataset variables that contain a time dimension and are
        eligible for QARTOD processing.

    Returns
    -------
    dict
        Updated configuration dictionary containing both the original
        YAML-defined QARTOD settings and any automatically generated
        configurations for previously undefined variables.

    Notes
    -----
    - Variables already present in the YAML configuration are left
      unchanged.
    - When available, ``valid_min`` and ``valid_max`` attributes are
      used to construct a default gross range test.
    - If ``potential_density`` lacks valid range attributes, the
      attributes from ``density`` are used when available.
    - If ``potential_temperature`` lacks valid range attributes, the
      attributes from ``temperature`` are used when available.
    - The default fail range is expanded by one full data span beyond
      the valid range on both sides.
    - Variables lacking usable ``valid_min`` and ``valid_max``
      attributes are assigned permissive placeholder thresholds and
      a warning is logged.
    - Automatically generated thresholds should be reviewed and
      replaced with scientifically appropriate values when possible.
    """

    # ACCESS STREAM CONFIGURATION SECTION
    streams = config_dict["contexts"][0]["streams"]
    fail_span_placeholder = [-1e10, 1e10]

    # PROCESS ALL TIME-DEPENDENT VARIABLES
    for var in time_variables:

        # SKIP VARIABLES ALREADY DEFINED IN YAML
        if var in streams:
            continue

        # BUILD GROSS RANGE TEST FROM
        # VARIABLE METADATA
        valid_min = ds[var].attrs.get("valid_min")
        valid_max = ds[var].attrs.get("valid_max")
        range_source = var

        # USE DENSITY RANGE FOR POTENTIAL DENSITY IF NEEDED
        if (
            var == "potential_density"
            and (valid_min is None or valid_max is None)
            and "density" in ds
        ):
            valid_min = ds["density"].attrs.get("valid_min")
            valid_max = ds["density"].attrs.get("valid_max")
            range_source = "density"

        # USE TEMPERATURE RANGE FOR POTENTIAL TEMPERATURE IF NEEDED
        elif (
            var == "potential_temperature"
            and (valid_min is None or valid_max is None)
            and "temperature" in ds
        ):
            valid_min = ds["temperature"].attrs.get("valid_min")
            valid_max = ds["temperature"].attrs.get("valid_max")
            range_source = "temperature"

        # BUILD GROSS RANGE THRESHOLDS
        if valid_min is not None and valid_max is not None:
            # CALCULATE DEFAULT FAIL RANGE
            span = valid_max - valid_min
            fail_min = valid_min - span
            fail_max = valid_max + span

            _log.info(
                "Variable '%s' is not defined in the QARTOD configuration. "
                "Using valid_min/valid_max from '%s' to build default "
                "gross range thresholds.",
                var,
                range_source,
            )

            gross_range_config = {
                "suspect_span": [
                    float(valid_min),
                    float(valid_max),
                ],
                "fail_span": [
                    float(fail_min),
                    float(fail_max),
                ],
            }

        elif valid_min is not None:
            _log.warning(
                "Variable '%s' is not defined in the QARTOD configuration "
                "and does not contain usable valid_max attributes. "
                "Using placeholder thresholds, for all except suspect min.",
                var,
            )

            gross_range_config = {
                "suspect_span": [valid_min, 9999],
                "fail_span": fail_span_placeholder,
            }

        else:
            # FALL BACK TO PERMISSIVE THRESHOLDS
            _log.warning(
                "Variable '%s' is not defined in the QARTOD configuration "
                "and does not contain usable valid_min/valid_max "
                "attributes. Using placeholder thresholds.",
                var,
            )

            gross_range_config = {
                "suspect_span": [-9999, 9999],
                "fail_span": fail_span_placeholder,
            }

        # CREATE DEFAULT QARTOD CONFIGURATION
        streams[var] = {
            "qartod": {
                "gross_range_test": gross_range_config,
                "spike_test": {
                    "suspect_threshold": -9999,
                    "fail_threshold": -9999,
                },
                "rate_of_change_test": {
                    "threshold": -9999,
                },
            }
        }

    return config_dict


# =========================================================
# UPDATE DYNAMIC THRESHOLDS
# =========================================================


def update_dynamic_thresholds(
    ds,
    config_dict,
):
    """
    Compute deployment-specific QARTOD thresholds and update the
    in-memory configuration dictionary.

    This function calculates deployment-specific thresholds for
    supported QARTOD tests using the observations contained in the
    current dataset. The computed thresholds replace the placeholder
    values stored in the configuration dictionary prior to
    constructing the ``ioos_qc.config.Config`` object.

    The QARTOD YAML configuration file is treated as a template and
    is never modified. Instead, the calculated thresholds are applied
    only to the in-memory configuration dictionary used during the
    current QC workflow.

    Parameters
    ----------
    ds : xarray.Dataset
        Input glider dataset containing the observations used to
        calculate deployment-specific thresholds.

    config_dict : dict
        Parsed QARTOD configuration dictionary.

    Returns
    -------
    dict
        Updated configuration dictionary containing the
        deployment-specific threshold values used during QARTOD
        processing.

    Notes
    -----
    Currently supported dynamic threshold calculations:

    - spike_test
    - rate_of_change_test
    """

    # ACCESS STREAM CONFIGURATION
    streams = config_dict["contexts"][0]["streams"]

    # PROCESS EACH CONFIGURED VARIABLE
    for var_name, stream in streams.items():

        # SKIP VARIABLES NOT PRESENT IN DATASET
        if var_name not in ds:
            continue

        # SKIP SCALAR VARIABLES
        if ds[var_name].ndim == 0:
            _log.info(
                "Skipping dynamic threshold calculation for "
                "%s because it is scalar.",
                var_name,
            )
            continue

        # SKIP VARIABLES WITHOUT QARTOD CONFIGURATION
        if "qartod" not in stream:
            continue

        qartod = stream["qartod"]

        # SPIKE TEST
        if "spike_test" in qartod:
            suspect, fail, report = get_spike_thresholds(
                ds[var_name].values,
            )

            if suspect is not None and fail is not None:
                qartod["spike_test"]["suspect_threshold"] = float(suspect)
                qartod["spike_test"]["fail_threshold"] = float(fail)
                _log.info(
                    "Updated spike thresholds for %s",
                    var_name,
                )

            if report:
                _log.info("%s: %s", var_name, report)

        # RATE OF CHANGE TEST
        if "rate_of_change_test" in qartod:
            threshold, report = get_rate_of_change_threshold(
                ds[var_name].values,
                ds["time"].values,
            )

            if threshold is not None:
                qartod["rate_of_change_test"]["threshold"] = float(threshold)
                _log.info(
                    "Updated rate_of_change threshold for %s",
                    var_name,
                )

            if report:
                _log.info("%s: %s", var_name, report)

    return config_dict


# =========================================================
# CHUNK FLAT LINE TEST
# =========================================================


def run_flat_line_chunked(
    ds,
    var_name,
    test_config,
    chunk_size=10000,
):
    """
    Run the QARTOD flat-line test in overlapping chunks.

    Processes the dataset in smaller chunks to limit memory use while
    retaining the preceding time window required by the flat-line test.
    Overlapping flags are discarded so each observation is represented
    once in the final result.

    Parameters
    ----------
    ds : xarray.Dataset
        Dataset containing the variable and time coordinate.
    var_name : str
        Name of the variable to test.
    test_config : dict
        QARTOD flat-line test configuration containing the suspect and
        fail thresholds.
    chunk_size : int, optional
        Number of observations to process per chunk. Default is 10000.

    Returns
    -------
    numpy.ndarray
        QARTOD flat-line flags for all observations as an int8 array.
    """
    # GET NUMBER OF OBSERVATIONS
    n_obs = ds.sizes["time"]

    # GET FLAT-LINE THRESHOLDS
    suspect_threshold = test_config["suspect_threshold"]
    fail_threshold = test_config["fail_threshold"]

    # SET REQUIRED OVERLAP
    overlap_seconds = max(
        suspect_threshold,
        fail_threshold,
    )

    # INITIALIZE FINAL FLAGS
    final_flags = np.full(
        n_obs,
        2,
        dtype="int8",
    )

    # START FIRST CHUNK
    start = 0
    while start < n_obs:
        # SET CHUNK END
        stop = min(
            start + chunk_size,
            n_obs,
        )

        # SET OVERLAP START
        if start == 0:
            overlap_start = 0
        else:
            # GET CHUNK START TIME
            chunk_start_time = ds["time"].values[start]

            # GET REQUIRED OVERLAP TIME
            overlap_time = (
                chunk_start_time
                - np.timedelta64(
                    int(np.ceil(overlap_seconds)),
                    "s",
                )
            )
            # FIND OVERLAP INDEX
            overlap_start = np.searchsorted(
                ds["time"].values,
                overlap_time,
            )
        # SELECT CHUNK WITH OVERLAP
        ds_chunk = ds.isel(
            time=slice(overlap_start, stop)
        )
        # BUILD FLAT-LINE CONFIG
        test_config_dict = {
            "contexts": [
                {
                    "streams": {
                        var_name: {
                            "qartod": {
                                "flat_line_test": test_config,
                            }
                        }
                    }
                }
            ]
        }
        # CREATE QARTOD CONFIG
        qc_config = Config(test_config_dict)
        # CREATE CHUNK DATA STREAM
        chunk_stream = XarrayStream(
            ds_chunk,
            time="time",
        )
        # RUN FLAT-LINE TEST
        results = chunk_stream.run(qc_config)
        # COLLECT TEST RESULTS
        collected = collect_results(
            results,
            how="list",
        )
        # CONVERT RESULTS TO FLAGS
        chunk_flags = (
            collected[0]
            .results
            .filled(2)
            .astype("int8")
        )
        # REMOVE OVERLAP FROM RESULTS
        offset = start - overlap_start
        # STORE CURRENT CHUNK FLAGS
        final_flags[start:stop] = chunk_flags[
            offset:
        ]
        # RELEASE CHUNK MEMORY
        del results
        del collected
        del chunk_flags
        del ds_chunk
        # MOVE TO NEXT CHUNK
        start = stop

    return final_flags


# =========================================================
# RUN QARTOD TESTS
# =========================================================


def run_qartod_tests(
    ds,
    config_dict,
    flat_line_chunk_size=10000,
):
    """
    Run configured IOOS QARTOD tests one variable and test at a time.

    Each QARTOD test is run, collected, and immediately aggregated
    before the next test is processed. Flat-line tests are processed
    in overlapping chunks to further reduce memory use for large
    glider datasets.

    Parameters
    ----------
    ds : xarray.Dataset
        Input glider science dataset containing the variables
        to be evaluated by QARTOD. The dataset must contain a
        ``time`` coordinate and may optionally contain
        ``pressure``, ``latitude``/``longitude``, or
        ``lat``/``lon`` coordinate variables.

    config_dict : dict
        Parsed QARTOD configuration dictionary containing
        the tests and thresholds to apply.
        
    flat_line_chunk_size : int, optional
        Number of observations processed per flat-line test
        chunk. Default is 10000.

    Returns
    -------
    dict
        Dictionary containing aggregate QARTOD flags for each
        successfully processed variable.
    """

    # DETERMINE LATITUDE VARIABLE
    if "latitude" in ds:
        lat_var = "latitude"
    elif "lat" in ds:
        lat_var = "lat"
    else:
        lat_var = None

    # DETERMINE LONGITUDE VARIABLE
    if "longitude" in ds:
        lon_var = "longitude"
    elif "lon" in ds:
        lon_var = "lon"
    else:
        lon_var = None

    # CREATE IOOS QC STREAM
    stream = XarrayStream(
        ds,
        time="time",
        z="pressure",
        lat=lat_var,
        lon=lon_var,
    )

    # ACCESS CONFIGURED STREAMS
    streams = config_dict["contexts"][0]["streams"]

    # STORE ONLY FINAL AGGREGATE FLAGS
    aggregate_results = {}

    # PROCESS EACH VARIABLE INDEPENDENTLY
    for var_name, stream_config in streams.items():

        if var_name not in ds:
            _log.info(
                "Skipping QARTOD tests for %s because it "
                "is not present in the dataset.",
                var_name,
            )
            continue

        if "qartod" not in stream_config:
            continue

        _log.info(
            "Running QARTOD tests for %s",
            var_name,
        )

        # ACCESS QARTOD TESTS FOR THIS VARIABLE
        qartod_tests = stream_config["qartod"]

        # INITIALIZE AGGREGATE FLAGS FOR THIS VARIABLE
        final_flags = None

        # PROCESS EACH QARTOD TEST INDEPENDENTLY
        for test_name, test_config in qartod_tests.items():

            _log.debug(
                "Running %s for %s",
                test_name,
                var_name,
            )

            # BUILD CONFIGURATION FOR ONLY THIS TEST
            test_config_dict = {
                "contexts": [
                    {
                        "streams": {
                            var_name: {
                                "qartod": {
                                    test_name: test_config,
                                }
                            }
                        }
                    }
                ]
            }

            test_qc_config = Config(test_config_dict)
            
            if test_name == "flat_line_test":
                test_flags = run_flat_line_chunked(
                    ds,
                    var_name,
                    test_config,
                    chunk_size=flat_line_chunk_size,
                )

            else:
                # RUN ONLY THIS QARTOD TEST
                results = stream.run(test_qc_config)

                # COLLECT THIS TEST'S RESULT
                collected = collect_results(
                    results,
                    how="list",
                )

                if not collected:
                    _log.info(
                        "No result generated for %s on %s",
                        test_name,
                        var_name,
                    )

                    del results
                    del collected

                    continue

                test_flags = (
                    collected[0]
                    .results
                    .filled(2)
                    .astype("int8")
                )

                del results
                del collected

            # INITIALIZE OR UPDATE AGGREGATE FLAGS
            if final_flags is None:
                final_flags = test_flags.copy()
            else:
                final_flags = qartod_compare(
                    [final_flags, test_flags]
                ).astype("int8")

            _log.debug(
                "Finished %s for %s",
                test_name,
                var_name,
            )

            # RELEASE THIS TEST'S FLAG ARRAY
            del test_flags

        # STORE FINAL AGGREGATE FLAGS FOR THIS VARIABLE
        if final_flags is not None:
            aggregate_results[var_name] = final_flags

        _log.info(
            "Finished QARTOD tests for %s",
            var_name,
        )

    return aggregate_results


# =========================================================
# BUILD FLAG CONFIGURATION
# =========================================================


def build_flag_configuration(config_dict, var_name):
    """
    Build the QARTOD configuration provenance stored in the
    ``flag_configuration`` attribute.

    Parameters
    ----------
    config_dict : dict
        Parsed QARTOD configuration dictionary containing the
        deployment-specific thresholds used during the current
        QC workflow.

    var_name : str
        Variable name.

    Returns
    -------
    str
        JSON-formatted string containing the QARTOD configuration
        used to generate the aggregate QC variable.
    """

    streams = config_dict["contexts"][0]["streams"]

    flag_configuration = {
        "configuration_source": "template_modified_in_memory",
        "configuration_template": "qartod-config.yml",
        "threshold_source": ("computed_from_current_deployment_statistics"),
    }

    if var_name in streams and "qartod" in streams[var_name]:
        flag_configuration.update(streams[var_name]["qartod"])

    return json.dumps(flag_configuration, separators=(",", ":"))


# =========================================================
# CREATE AGGREGATE QC VARIABLES
# =========================================================


def create_qc_variables(
    ds,
    aggregate_results,
    config_dict,
    overwrite_qc=True,
):
    """
    Create aggregate DAC-compliant QARTOD QC variables.

    This function adds the aggregate QARTOD flags generated by
    ``run_qartod_tests()`` to the input dataset. Each variable's
    individual QARTOD test results have already been combined using
    QARTOD flag precedence before being passed to this function.

    The aggregate flags are written to new ``*_qc`` variables and
    linked to their parent variables through the
    ``ancillary_variables`` attribute. Each QC variable also includes
    a serialized ``flag_configuration`` attribute documenting the
    QARTOD configuration used to generate the flags.

    Existing QC variables may optionally be replaced when
    ``overwrite_qc=True``.

    Parameters
    ----------
    ds : xarray.Dataset
        Input dataset containing the original science
        variables.

    aggregate_results : dict
        Dictionary of aggregate QARTOD flags generated by
        ``run_qartod_tests()``.

        Expected structure:

        .. code-block:: python

            {
                "temperature": temperature_flags,
                "conductivity": conductivity_flags,
                "salinity": salinity_flags,
            }

        Each value is a single ``int8`` array containing the
        aggregate QARTOD flags for that variable.

    config_dict : dict
        Parsed QARTOD configuration dictionary containing the
        deployment-specific threshold values used during the current
        QC workflow. The configuration is used to record QC
        provenance in the output ``*_qc`` variables.

    overwrite_qc : bool, optional
        If True, existing QC variables are removed and replaced with
        newly generated aggregate QARTOD variables. If False,
        existing QC variables are preserved and skipped.

    Returns
    -------
    xarray.Dataset
        Copy of the input dataset containing the newly generated
        aggregate QARTOD QC variables.
    """

    # CREATE WORKING COPY OF DATASET
    ds_qc = ds.copy()

    # PROCESS EACH VARIABLE WITH AGGREGATE QARTOD RESULTS
    for var_name, final_flags in aggregate_results.items():

        _log.info("Creating QC for %s", var_name)
        
        qc_var = f"{var_name}_qc"
        
        # HANDLE EXISTING QC VARIABLES
        if qc_var in ds_qc.variables:
            
            if overwrite_qc:
                ds_qc = ds_qc.drop_vars(qc_var)
                _log.info(
                    "Overwriting %s",
                    qc_var,
                )
            else:
                continue
            
        # CREATE AGGREGATE QC VARIABLE
        ds_qc[qc_var] = xr.DataArray(
            final_flags,
            dims=ds[var_name].dims,
            coords=ds[var_name].coords,
            attrs={
                "long_name": (
                    "QARTOD aggregate quality flag for "
                    f"{var_name}"
                ),
                "standard_name": get_qc_standard_name(
                    ds,
                    var_name,
                ),
                "flag_values": np.array(
                    [1, 2, 3, 4, 9],
                    dtype="int8",
                ),
                "flag_meanings": (
                    "GOOD "
                    "UNKNOWN "
                    "SUSPECT "
                    "FAIL "
                    "MISSING"
                ),
                "valid_min": np.int8(1),
                "valid_max": np.int8(9),
                "comment": (
                    "Aggregate QARTOD flag "
                    "generated using "
                    "ioos_qc package."
                ),
                "flag_configuration": build_flag_configuration(
                    config_dict,
                    var_name,
                ),
                "average_method": "QC_protocol",
            },
        )
        
        # UPDATE ANCILLARY VARIABLE LINKS
        existing = ds_qc[var_name].attrs.get(
            "ancillary_variables",
            "",
        )
        
        ancillary_vars = existing.split()
        
        # REMOVE OLD REFERENCE TO THIS QC VARIABLE
        ancillary_vars = [
            var
            for var in ancillary_vars
            if var != qc_var
        ]
        
        # ADD NEW QC VARIABLE REFERENCE
        ancillary_vars.append(qc_var)
        
        ds_qc[var_name].attrs[
            "ancillary_variables"
        ] = " ".join(ancillary_vars)

    return ds_qc


# =========================================================
# CREATE PLACEHOLDER QC VARIABLES
# =========================================================


def create_placeholder_qc_variables(ds_qc):
    """
    Create DAC-compliant placeholder QC variables for
    metadata time variables that are required by the
    IOOS DAC but are not evaluated using QARTOD tests.

    This function creates QC variables for metadata
    variables such as ``time``, ``profile_time``, and
    ``time_uv``. These variables do not undergo QARTOD
    quality control testing but are still expected to
    have corresponding QC variables in DAC-compliant
    files.

    The generated QC variables are populated entirely
    with the DAC fill value (-127) to indicate that
    no quality control evaluation has been performed.

    Parameters
    ----------
    ds_qc : xarray.Dataset
        Dataset containing previously generated QARTOD
        QC variables and the original science variables.

    Returns
    -------
    xarray.Dataset
        Updated dataset containing placeholder QC
        variables for metadata time variables.

    Notes
    -----
    These variables are intentionally excluded from
    QARTOD processing because they represent metadata
    rather than measured environmental observations.

    Placeholder QC variables are currently created for:

    - time
    - profile_time
    - time_uv

    The resulting QC variables:

    - Use the DAC fill value (-127)
    - Are stored as int8
    - Include DAC-compliant QC metadata
    - Are linked to the parent variable through the
      ancillary_variables attribute

    No QARTOD tests are performed on these variables.
    """

    # VARIABLES REQUIRING PLACEHOLDER QC FLAGS
    placeholder_qc_vars = ["time", "profile_time", "time_uv"]

    # PROCESS EACH PLACEHOLDER VARIABLE
    for var_name in placeholder_qc_vars:

        # VERIFY VARIABLE EXISTS
        if var_name not in ds_qc.variables:
            continue

        qc_var = f"{var_name}_qc"

        _log.info("Creating placeholder QC %s", qc_var)

        # CREATE EMPTY QC FLAGS
        flags = xr.full_like(ds_qc[var_name], fill_value=-127, dtype="int8")

        # CREATE PLACEHOLDER QC VARIABLE
        ds_qc[qc_var] = xr.DataArray(
            flags,
            dims=ds_qc[var_name].dims,
            coords=ds_qc[var_name].coords,
            attrs={
                "long_name": f"{var_name} Quality Flag",
                "standard_name": get_qc_standard_name(ds_qc, var_name),
                "flag_values": np.array([1, 2, 3, 4, 9], dtype="int8"),
                "flag_meanings": (
                    "GOOD " "UNKNOWN " "SUSPECT " "FAIL " "MISSING"
                ),
                "valid_min": np.int8(1),
                "valid_max": np.int8(9),
            },
        )

        # UPDATE ANCILLARY VARIABLE LINK
        ds_qc[var_name].attrs["ancillary_variables"] = qc_var

    return ds_qc


# =========================================================
# SAVE QC DATASET
# =========================================================


def save_qc_dataset(
    ds_qc,
    output_file,
):
    """
    Save a QC-enhanced dataset to a NetCDF file using
    DAC-compliant QC variable encodings.

    This function prepares a QC-enhanced dataset for
    NetCDF export by removing inherited encodings and
    applying standardized encodings to all generated
    QC variables.

    QC variables are written using the IOOS DAC
    conventions for quality control flags, including
    an ``int8`` data type and a fill value of ``-127``.

    Parameters
    ----------
    ds_qc : xarray.Dataset
        Dataset containing the original variables and
        newly generated QARTOD QC variables.

    output_file : str
        Full path to the output NetCDF file.

    Returns
    -------
    None

    Notes
    -----
    Prior to writing the file, all inherited encodings
    are removed to prevent conflicts with the custom QC
    encodings applied during export.

    QC variables are written with:

    - int8 data type
    - _FillValue = -127
    - zlib compression enabled

    Example QC variable encoding:

    .. code-block:: python

        {
            "dtype": "int8",
            "_FillValue": np.int8(-127),
            "zlib": True
        }

    The resulting file is written using:

    - NETCDF4 format
    - netcdf4 engine

    This ensures compatibility with IOOS DAC
    requirements while minimizing file size through
    compression.
    """

    # REMOVE INHERITED ENCODINGS
    for var in ds_qc.variables:
        ds_qc[var].encoding = {}

    # BUILD QC VARIABLE ENCODINGS
    encoding = {}

    for var in ds_qc.data_vars:

        # APPLY QC-SPECIFIC ENCODINGS
        if var.endswith("_qc"):
            encoding[var] = {
                "dtype": "int8",
                "_FillValue": np.int8(-127),
                "zlib": True,
            }

    # WRITE NETCDF FILE
    ds_qc.to_netcdf(
        output_file,
        engine="netcdf4",
        format="NETCDF4",
        encoding=encoding,
    )

    _log.info(
        "Saved QC dataset: %s",
        output_file,
    )


# =========================================================
# CREATE QC SUMMARY TABLE
# =========================================================


def create_qc_summary_table(ds_qc):
    """
    Create a profile-level summary table of QARTOD
    quality-control flags.

    Parameters
    ----------
    ds_qc : xarray.Dataset
        Combined deployment dataset containing
        QARTOD QC variables.

    Returns
    -------
    pandas.DataFrame
        One row per profile containing the profile
        filename, profile index, and the total number
        of GOOD, UNKNOWN, SUSPECT, FAIL, and MISSING
        flags.
    """

    # VARIABLES THAT ARE EXCLUDED
    skip = {
        "time_qc",
        "profile_time_qc",
        "time_uv_qc",
    }

    # IDENTIFY QC VARIABLES
    qc_variables = [
        var
        for var in ds_qc.data_vars
        if (
            var.endswith("_qc")
            and ds_qc[var].ndim > 0
            and var not in skip
        )
    ]

    profile_summary = []

    # PROCESS EACH UNIQUE PROFILE IN THE DEPLOYMENT
    for profile_name in np.unique(
        ds_qc["profile"].values
    ):

        # SELECT ONLY OBS FROM THE CURRENT PROFILE
        profile_ds = ds_qc.where(
            ds_qc["profile"] == profile_name,
            drop=True,
        )
        # GET THE PROFILE INDEX
        profile_index = int(
            np.ravel(
                profile_ds["profile_index"].values
            )[0]
        )

        # INITIALIZE QC FLAG COUNTS FOR THIS PROFILE
        good = unknown = suspect = fail = missing = 0

        # COUNT QC FLAGS ACROSS ALL VARIABLES IN THIS PROFILE
        for var in qc_variables:
            flags = profile_ds[var].values.ravel()
            flags = flags[flags != -127]

            # ACCUMULATE FLAG COUNTS
            good += np.sum(flags == 1)
            unknown += np.sum(flags == 2)
            suspect += np.sum(flags == 3)
            fail += np.sum(flags == 4)
            missing += np.sum(flags == 9)

        # SAVE THE PROFILE SUMMARY
        profile_summary.append(
            {
                "profile": profile_name,
                "profile_index": profile_index,
                "GOOD": good,
                "UNKNOWN": unknown,
                "SUSPECT": suspect,
                "FAIL": fail,
                "MISSING": missing,
            }
        )

    return pd.DataFrame(profile_summary)


# =========================================================
# MAIN DRIVER FUNCTION
# =========================================================


def run_qartod_qc(
    input_file,
    output_file,
    config_file=None,
    overwrite_qc=True,
    flat_line_chunk_size=10000,
):
    """
    Execute the complete operational QARTOD quality control
    workflow for a science NetCDF dataset.

    This function serves as the primary entry point for the
    QARTOD processing pipeline. It coordinates all workflow
    steps required to generate DAC-compliant QC variables,
    create placeholder QC variables for metadata fields,
    add QC provenance metadata, and write a QC-enhanced
    NetCDF output file.

    The workflow performs the following operations:

    1. Open the input science dataset
    2. Identify variables eligible for QARTOD testing
    3. Load the QARTOD YAML configuration
    4. Automatically add missing variable configurations
    5. Compute deployment-specific thresholds
    6. Execute QARTOD tests one variable and test at a time
    7. Process flat-line tests in overlapping chunks
    8. Immediately aggregate individual test results
    9. Add QC provenance metadata
    10. Create placeholder QC variables
    11. Save the QC-enhanced dataset

    Parameters
    ----------
    input_file : str
        Path to the input science NetCDF file.

    output_file : str
        Path to the output QC-enhanced NetCDF file.

    config_file : str
        Path to the YAML QARTOD configuration file.

    overwrite_qc : bool, optional
        If True, existing QC variables are removed and
        replaced with newly generated aggregate QARTOD
        variables. If False, existing QC variables are
        preserved.
        
    flat_line_chunk_size : int, optional
        Number of observations processed per flat-line test
        chunk. Default is 10000.

    Returns
    -------
    output_file path : str
        Path to the QC-enhanced NetCDF file

    Notes
    -----
    This function is designed to operate on fully
    processed science datasets and should be executed
    after all science variable calculations and metadata
    updates have been completed.

    The output dataset contains:

    - Original science variables
    - Aggregate QARTOD QC variables
    - Placeholder QC variables for metadata fields
    - Updated ancillary_variables attributes
    - QARTOD provenance metadata

    QC provenance currently includes:

    - ioos_qc_version

    The generated QC variables follow IOOS DAC
    conventions including:

    - DAC-compliant standard_name attributes
    - Standardized flag values
    - int8 data types
    - _FillValue = -127

    Example
    -------
    .. code-block:: python

        run_qartod_qc(
            input_file="deployment-sci.nc",
            output_file="deployment-sci_qc.nc",
            config_file="qartod-config.yml"
        )

    Result:

    .. code-block:: text

        deployment-sci_qc.nc

    containing DAC-compliant aggregate QARTOD quality
    control variables.
    """

    if config_file is None:
        config_file = paths.get_path_qartod_config()

    # LOAD INPUT DATASET INTO MEMORY
    ds = xr.load_dataset(input_file)

    # IDENTIFY VARIABLES FOR QARTOD PROCESSING
    time_variables = find_time_variables(ds)

    # LOAD QARTOD CONFIGURATION
    config_dict = load_qartod_config(config_file)

    # AUTO-GENERATE MISSING CONFIGURATIONS
    config_dict = add_missing_variables_to_config(
        ds,
        config_dict,
        time_variables,
    )

    # UPDATE DEPLOYMENT-SPECIFIC THRESHOLDS
    config_dict = update_dynamic_thresholds(
        ds,
        config_dict,
    )

    # EXECUTE QARTOD TESTS ONE VARIABLE AT A TIME
    aggregate_results = run_qartod_tests(
        ds,
        config_dict,
        flat_line_chunk_size=flat_line_chunk_size
    )

    # CREATE AGGREGATE QC VARIABLES
    ds_qc = create_qc_variables(
        ds,
        aggregate_results,
        config_dict,
        overwrite_qc=overwrite_qc,
    )

    # ADD QC PROVENANCE METADATA
    ds_qc.attrs["ioos_qc_version"] = ioos_qc.__version__

    # CREATE PLACEHOLDER QC VARIABLES
    ds_qc = create_placeholder_qc_variables(ds_qc)

    # SAVE QC-ENHANCED DATASET
    save_qc_dataset(ds_qc, output_file)

    _log.info("Completed QARTOD QC workflow")
