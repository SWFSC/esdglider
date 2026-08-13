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
configuration file and applied to all eligible variables
containing a time dimension.

The resulting QC flags are aggregated into DAC-compliant
*_qc variables, linked to their parent variables through
the ancillary_variables attribute, and written to a QC-enhanced
NetCDF file suitable for GliderDAC submission and downstream
scientific analysis. In addition to QC generation, this
module provides utilities for creating deployment-level QC
summary tables from combined QARTOD datasets. Visualization
of QC results is implemented separately in the companion
plots.py module.

Workflow
--------
The operational QARTOD workflow performs the following
steps:

1. Open the input science NetCDF dataset.
2. Identify variables eligible for QARTOD testing.
3. Load the YAML QARTOD configuration.
4. Automatically generate default QC configurations.
5. Compute deployment-specific thresholds.
6. Construct the ioos_qc.config.Config object.
7. Execute configured QARTOD tests.
8. Generate aggregate DAC-compliant ``*_qc`` variables.
9. Create placeholder QC variables for metadata time
   variables that are not evaluated by QARTOD.
10. Update ``ancillary_variables`` attributes.
11. Add QC provenance metadata.
12. Write the QC-enhanced dataset to NetCDF.

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
- Aggregate QC flag generation following DAC
  conventions
- Placeholder QC variables for required metadata
  fields
- DAC-compliant ``standard_name`` generation
- Preservation of existing
  ``ancillary_variables`` metadata
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

import logging
import numpy as np
import xarray as xr
import pandas as pd
import yaml
import ioos_qc
import json
from ioos_qc.config import Config
from ioos_qc.streams import XarrayStream
from ioos_qc.results import collect_results

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
            list_values.append(values[nn])
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
    are used to construct a default gross range test. If those
    attributes are unavailable, a permissive fallback gross range test
    is assigned to prevent workflow failures while still allowing the
    variable to participate in the QARTOD workflow.

    A default spike test is also added for all automatically configured
    variables.

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
    - The default fail range is expanded by one full data span beyond
      the valid range on both sides.
    - Variables lacking ``valid_min`` and ``valid_max`` attributes are
      assigned permissive placeholder thresholds and a warning is
      logged.
    - Automatically generated thresholds should be reviewed and
      replaced with scientifically appropriate values when possible.
    """

    # ACCESS STREAM CONFIGURATION SECTION
    streams = config_dict["contexts"][0]["streams"]

    # PROCESS ALL TIME-DEPENDENT VARIABLES
    for var in time_variables:

        # SKIP VARIABLES ALREADY DEFINED IN YAML
        if var in streams:
            continue

        # BUILD GROSS RANGE TEST FROM
        # VARIABLE METADATA
        valid_min = ds[var].attrs.get("valid_min")
        valid_max = ds[var].attrs.get("valid_max")
        if valid_min is not None and valid_max is not None:

            # CALCULATE DEFAULT FAIL RANGE
            span = valid_max - valid_min
            fail_min = valid_min - span
            fail_max = valid_max + span

            _log.warning(
                "Variable '%s' is not defined in the QARTOD configuration. "
                "Using valid_min/valid_max attributes to build default "
                "gross range thresholds.",
                var,
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
        else:

            # FALL BACK TO PERMISSIVE THRESHOLDS
            _log.warning(
                "Variable '%s' is not defined in the QARTOD configuration "
                "and does not contain valid_min/valid_max attributes. "
                "Using placeholder thresholds.",
                var,
            )

            gross_range_config = {
                "suspect_span": [-9999, 9999],
                "fail_span": [-1e10, 1e10],
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
# BUILD IOOS_QC CONFIG
# =========================================================


def build_ioos_qc_config(config_dict):
    """
    Build an IOOS QC configuration object from a parsed
    QARTOD configuration dictionary.

    This function converts a Python dictionary containing
    QARTOD configuration settings into an
    ``ioos_qc.config.Config`` object that can be consumed
    directly by the IOOS QC processing framework.

    The resulting configuration object defines the variables,
    tests, thresholds, and processing contexts that will be
    evaluated when QARTOD quality control tests are executed.

    Parameters
    ----------
    config_dict : dict
        Parsed QARTOD configuration dictionary, typically
        generated by ``load_qartod_config()``.

    Returns
    -------
    ioos_qc.config.Config
        Fully constructed IOOS QC configuration object used
        to execute QARTOD tests through the ``ioos_qc``
        framework.

    Notes
    -----
    The configuration dictionary is converted into an
    ``ioos_qc.config.Config`` object because the
    ``ioos_qc`` processing engine expects a Config
    instance rather than a raw Python dictionary.
    """

    config = Config(config_dict)

    _log.info("Built ioos_qc Config")

    return config


# =========================================================
# RUN QARTOD TESTS
# =========================================================


def run_qartod_tests(ds, config):
    """
    Run configured IOOS QARTOD tests on a glider dataset.

    This function creates an ``ioos_qc.XarrayStream`` from the
    input dataset and executes all QARTOD tests defined in the
    provided configuration. The function automatically detects
    whether the dataset uses ``latitude``/``longitude`` or
    ``lat``/``lon`` coordinate names and passes the appropriate
    variables to ``ioos_qc``.

    The resulting QARTOD outputs are collected into a flat list
    of test result objects which can subsequently be grouped and
    aggregated into DAC-compatible ``*_qc`` variables.

    Parameters
    ----------
    ds : xarray.Dataset
        Input glider science dataset containing the variables
        to be evaluated by QARTOD. The dataset must contain a
        ``time`` coordinate and may optionally contain
        ``pressure``, ``latitude``/``longitude``, or
        ``lat``/``lon`` coordinate variables.

    config : ioos_qc.config.Config
        Fully constructed IOOS QC configuration object
        containing the QARTOD tests and thresholds to apply.

    Returns
    -------
    list
        List of collected QARTOD test result objects returned
        by ``ioos_qc.results.collect_results()``. Each result
        contains the variable name, test name, and associated
        quality flags.
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

    # RUN CONFIGURED QARTOD TESTS
    results = stream.run(config)

    # COLLECT RESULTS INTO A FLAT LIST
    collected = collect_results(results, how="list")

    _log.info("Collected %d QARTOD test results", len(collected))

    return collected


# =========================================================
# GROUP RESULTS
# =========================================================


def group_qartod_results(ds, collected):
    """
    Group individual QARTOD test results by variable name.

    This function reorganizes the raw QARTOD test results
    returned by ``ioos_qc`` into a dictionary structure
    where all test outputs associated with a given variable
    are grouped together.

    The grouped results are subsequently used to create
    aggregate QC variables by combining the individual
    QARTOD test flags into a single DAC-compliant
    ``*_qc`` variable for each dataset variable.

    Parameters
    ----------
    ds : xarray.Dataset
        Input glider dataset containing the variables
        that were evaluated during QARTOD processing.

    collected : list
        List of collected QARTOD test results generated
        by ``ioos_qc.results.collect_results()``.

    Returns
    -------
    dict
        Dictionary containing grouped QARTOD test flags.

        Dictionary structure:

        .. code-block:: python

            {
                "temperature": [
                    gross_range_flags,
                    spike_flags,
                    flat_line_flags
                ],
                "conductivity": [
                    gross_range_flags,
                    spike_flags
                ]
            }

        Each dictionary key represents a dataset variable
        and each value contains a list of flag arrays
        generated by the individual QARTOD tests applied
        to that variable.

    Notes
    -----
    The ``ioos_qc`` framework returns results as individual
    test objects. This function consolidates those objects
    into a variable-centric structure that is easier to
    process when generating aggregate QC variables.

    Missing values within QARTOD result arrays are replaced
    with the IOOS QARTOD flag value:

    - 2 = NOT_EVALUATED

    All flags are converted to ``int8`` to maintain
    consistency with IOOS DAC QC variable requirements.

    Variables that are not present in the dataset are
    ignored to prevent creation of orphan QC results.
    """

    # INITIALIZE OUTPUT DICTIONARY
    grouped_results = {}

    # PROCESS COLLECTED QARTOD RESULTS
    for result in collected:

        # EXTRACT VARIABLE NAME
        var_name = str(result.stream_id).split(":")[0]

        # VERIFY VARIABLE EXISTS IN DATASET
        if var_name not in ds.variables:
            continue

        # INITIALIZE VARIABLE ENTRY
        if var_name not in grouped_results:
            grouped_results[var_name] = []

        # CONVERT FLAGS TO INT8
        flags = result.results.filled(2).astype("int8")

        # STORE TEST FLAGS
        grouped_results[var_name].append(flags)

    return grouped_results


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
    grouped_results,
    config_dict,
    overwrite_qc=True,
):
    """
    Create aggregate DAC-compliant QARTOD QC variables.

    This function combines the individual QARTOD test
    results associated with each variable into a single
    aggregate QC variable following IOOS DAC conventions.

    For each variable, the most severe flag value from all
    configured QARTOD tests is retained at each observation
    using a maximum-value aggregation approach. The resulting
    aggregate flags are written to a new *_qc variable, linked
    back to the parent variable through the ancillary_variables
    attribute, and include a serialized flag_configuration
    attribute documenting the QARTOD configuration used to
    generate the aggregate flags.

    Existing QC variables may optionally be replaced when
    ``overwrite_qc=True``.

    Parameters
    ----------
    ds : xarray.Dataset
        Input dataset containing the original science
        variables.

    grouped_results : dict
        Dictionary of grouped QARTOD results generated by
        ``group_qartod_results()``.

        Expected structure:

        .. code-block:: python

            {
                "temperature": [
                    gross_range_flags,
                    spike_flags,
                    flat_line_flags
                ],
                "conductivity": [
                    gross_range_flags,
                    spike_flags
                ]
            }

    config_dict : dict
            Parsed QARTOD configuration dictionary containing the
            deployment-specific threshold values used during the
            current QC workflow. The configuration is used to record
            QC provenance in the output ``*_qc`` variables.

    overwrite_qc : bool, optional
        If True, existing QC variables are removed and
        replaced with newly generated aggregate QARTOD
        variables. If False, existing QC variables are
        preserved and skipped.

    Returns
    -------
    xarray.Dataset
        Copy of the input dataset containing the newly
        generated aggregate QARTOD QC variables.
    """

    # CREATE WORKING COPY OF DATASET
    ds_qc = ds.copy()

    # PROCESS EACH VARIABLE WITH QARTOD RESULTS
    for var_name, test_results in grouped_results.items():

        _log.info("Creating QC for %s", var_name)

        # AGGREGATE QARTOD FLAGS
        final_flags = np.maximum.reduce(test_results).astype("int8")
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
                    "QARTOD aggregate quality flag for " f"{var_name}"
                ),
                "standard_name": get_qc_standard_name(ds, var_name),
                "flag_values": np.array([1, 2, 3, 4, 9], dtype="int8"),
                "flag_meanings": (
                    "GOOD " "UNKNOWN " "SUSPECT " "FAIL " "MISSING"
                ),
                "valid_min": 1,
                "valid_max": 9,
                "comment": (
                    "Aggregate QARTOD flag "
                    "generated using "
                    "ioos_qc package."
                ),
                "flag_configuration": build_flag_configuration(
                    config_dict,
                    var_name,
                ),
            },
        )

        # UPDATE ANCILLARY VARIABLE LINKS
        existing = ds_qc[var_name].attrs.get("ancillary_variables", "")

        if existing:
            ds_qc[var_name].attrs[
                "ancillary_variables"
            ] = f"{existing} {qc_var}"
        else:
            ds_qc[var_name].attrs["ancillary_variables"] = qc_var

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
                "valid_min": 1,
                "valid_max": 9,
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
    config_file,
    overwrite_qc=True,
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
    5. Build the IOOS QC Config object
    6. Execute QARTOD tests
    7. Group test results by variable
    8. Generate aggregate DAC-compliant QC variables
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

    Returns
    -------
    None

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

    # LOAD INPUT DATASET
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

    # BUILD IOOS QC CONFIGURATION OBJECT
    config = build_ioos_qc_config(config_dict)

    # EXECUTE QARTOD TESTS
    collected = run_qartod_tests(ds, config)

    # GROUP RESULTS BY VARIABLE
    grouped_results = group_qartod_results(ds, collected)

    # CREATE AGGREGATE QC VARIABLES
    ds_qc = create_qc_variables(
        ds, grouped_results, config_dict, overwrite_qc=overwrite_qc
    )

    # ADD QC PROVENANCE METADATA
    ds_qc.attrs["ioos_qc_version"] = ioos_qc.__version__

    # CREATE PLACEHOLDER QC VARIABLES
    ds_qc = create_placeholder_qc_variables(ds_qc)

    # SAVE QC-ENHANCED DATASET
    save_qc_dataset(ds_qc, output_file)

    _log.info("Completed QARTOD QC workflow")
