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
``*_qc`` variables, linked to their parent variables
through the ``ancillary_variables`` attribute, and written
to a new NetCDF file suitable for GliderDAC submission and
downstream scientific analysis.

Workflow
--------
The operational QARTOD workflow performs the following
steps:

1. Open the input science NetCDF dataset.
2. Identify variables eligible for QARTOD testing.
3. Load the YAML QARTOD configuration file.
4. Automatically generate default QC configurations for
   variables not explicitly defined in the YAML file.
5. Construct an ``ioos_qc.config.Config`` object.
6. Execute configured QARTOD tests using ``ioos_qc``.
7. Collect and group test results by variable.
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
science variables and metadata have been finalized.

The generated QC variables follow IOOS DAC conventions
for flag values, standard names, ancillary variable
relationships, and NetCDF encodings.
"""

import logging

import numpy as np
import xarray as xr
import yaml
import ioos_qc

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
    #
    # Use the variable's standard_name attribute
    # when available. If no standard_name exists,
    # fall back to the variable name itself.

    standard_name = ds[var_name].attrs.get(
        "standard_name",
        var_name
    )

    # BUILD DAC-COMPLIANT QC STANDARD NAME
    #
    # IOOS DAC QC variables use the parent
    # standard_name followed by "status_flag".

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
        "roll"
    }

    # EVALUATE ALL DATA VARIABLES

    for var in ds.data_vars:

        # REQUIRE TIME DIMENSION
        
        if "time" not in ds[var].dims:
            continue

        # SKIP EXISTING QC VARIABLES
        
        if var.endswith("_qc"):
            continue

        # SKIP EXCLUDED VARIABLES
        #
        # Exclude metadata variables and redundant
        # coordinate variables that should not
        # receive QARTOD testing.

        if var in skip_variables:
            continue

        time_variables.append(var)

    # LOG SUMMARY
    _log.info(
        f"Found {len(time_variables)} "
        f"time variables for QC"
    )

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
    #
    # Read the complete contents of the QARTOD
    # configuration file into memory.

    with open(config_file, "r") as f:

        qc_config = f.read()

    # PARSE YAML CONTENTS
    #
    # Convert the YAML text into a Python
    # dictionary that can be used by the
    # QARTOD processing workflow.

    config_dict = yaml.safe_load(qc_config)

    # LOG CONFIGURATION FILE
    #
    # Record which configuration file was
    # loaded for traceability and debugging.

    _log.info(
        f"Loaded QARTOD config: {config_file}"
    )

    return config_dict


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
    #
    # All variable-specific QARTOD settings are stored
    # under the "streams" section of the configuration.
    
    streams = config_dict["contexts"][0]["streams"]

    # PROCESS ALL TIME-DEPENDENT VARIABLES
    #
    # Only variables with a time dimension are eligible
    # for QARTOD evaluation.

    for var in time_variables:

        # SKIP VARIABLES ALREADY DEFINED IN YAML
        #
        # Explicit user-defined configurations always take
        # precedence over automatically generated defaults.

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
                f"Variable '{var}' is not defined in the "
                f"QARTOD configuration. Using valid_min/"
                f"valid_max attributes to build default "
                f"gross range thresholds."
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
            #
            # If no valid_min/valid_max metadata are
            # available, use placeholder ranges to
            # avoid workflow failures while clearly
            # notifying the user.
            
            _log.warning(
                f"Variable '{var}' is not defined in the "
                f"QARTOD configuration and does not contain "
                f"valid_min/valid_max attributes. Using "
                f"placeholder thresholds."
            )

            gross_range_config = {

                "suspect_span": [-9999, 9999],

                "fail_span": [-1e10, 1e10],
            }

        # CREATE DEFAULT QARTOD CONFIGURATION
        #
        # Add a default gross range test and spike
        # test for variables that do not have an
        # explicit YAML configuration.

        streams[var] = {

            "qartod": {

                "gross_range_test":
                    gross_range_config,

                "spike_test": {

                    "suspect_threshold": 5,

                    "fail_threshold": 10,
                },
            }
        }

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

    # BUILD IOOS QC CONFIGURATION OBJECT

    config = Config(config_dict)

    # LOG SUCCESSFUL CONFIG CREATION

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
    #
    # Support both CF-compliant coordinate names
    # ("latitude"/"longitude") and shortened
    # coordinate names ("lat"/"lon").
    #
    # If neither exists, pass None to ioos_qc.

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
    #
    # Build an XarrayStream object that provides
    # the coordinate information required by
    # QARTOD tests.

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
    #
    # The "list" format is easier to group and
    # aggregate into final QC variables later.
    
    collected = collect_results(
        results,
        how="list"
    )

    # LOG SUMMARY

    _log.info(
        f"Collected {len(collected)} "
        f"QARTOD test results"
    )

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
        
        var_name = str(
            result.stream_id
        ).split(":")[0]

        # VERIFY VARIABLE EXISTS IN DATASET

        if var_name not in ds.variables:
            continue

        # INITIALIZE VARIABLE ENTRY

        if var_name not in grouped_results:

            grouped_results[var_name] = []

        # CONVERT FLAGS TO INT8
        #
        # Replace masked values with the IOOS
        # NOT_EVALUATED flag (2) and convert
        # the resulting array to int8.

        flags = result.results.filled(
            2
        ).astype("int8")

        # STORE TEST FLAGS
        #
        # Append this QARTOD test result to the
        # list of results associated with the
        # current variable.

        grouped_results[var_name].append(
            flags
        )

    return grouped_results


# =========================================================
# CREATE AGGREGATE QC VARIABLES
# =========================================================

def create_qc_variables(
    ds,
    grouped_results,
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
    aggregate flags are written to a new ``*_qc`` variable and
    linked back to the parent variable through the
    ``ancillary_variables`` attribute.

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

        _log.info(
            f"Creating QC for: {var_name}"
        )

        # AGGREGATE QARTOD FLAGS
        #
        # Combine all QARTOD test results into a
        # single aggregate QC variable by selecting
        # the most severe flag at each observation.

        final_flags = np.maximum.reduce(

            test_results

        ).astype("int8")

        qc_var = f"{var_name}_qc"

        # HANDLE EXISTING QC VARIABLES
        #
        # Remove and replace existing QC variables
        # when overwrite_qc=True. Otherwise skip
        # processing and retain the original QC
        # variable.

        if qc_var in ds_qc.variables:

            if overwrite_qc:

                ds_qc = ds_qc.drop_vars(qc_var)

                _log.info(
                    f"Overwriting {qc_var}"
                )

            else:

                continue

        # CREATE AGGREGATE QC VARIABLE
        #
        # Create a DAC-compliant QC variable using
        # the aggregated QARTOD flags and inherit
        # dimensions and coordinates from the
        # parent variable.

        ds_qc[qc_var] = xr.DataArray(

            final_flags,

            dims=ds[var_name].dims,

            coords=ds[var_name].coords,

            attrs={

                "long_name":
                    (
                        f"QARTOD aggregate "
                        f"quality flag for "
                        f"{var_name}"
                    ),

                "standard_name":
                    get_qc_standard_name(
                        ds,
                        var_name
                    ),

                "flag_values":
                    np.array(
                        [1,2,3,4,9],
                        dtype="int8"
                    ),

                "flag_meanings":
                    (
                        "GOOD "
                        "UNKNOWN "
                        "SUSPECT "
                        "FAIL "
                        "MISSING"
                    ),

                "valid_min": 1,

                "valid_max": 9,

                "comment":
                    (
                        "Aggregate QARTOD flag "
                        "generated using "
                        "ioos_qc package."
                    ),
            },
        )

        # UPDATE ANCILLARY VARIABLE LINKS
        #
        # Preserve any existing ancillary_variables
        # references and append the newly created
        # QC variable.

        existing = ds_qc[var_name].attrs.get(
            "ancillary_variables",
            ""
        )

        if existing:

            ds_qc[var_name].attrs[
                "ancillary_variables"
            ] = f"{existing} {qc_var}"

        else:

            ds_qc[var_name].attrs[
                "ancillary_variables"
            ] = qc_var

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
    #
    # These metadata time variables are required
    # to have associated QC variables but are not
    # evaluated using QARTOD tests.

    placeholder_qc_vars = [

        "time",
        "profile_time",
        "time_uv"
    ]

    # PROCESS EACH PLACEHOLDER VARIABLE
    #
    # Create a companion QC variable for each
    # metadata variable present in the dataset.

    for var_name in placeholder_qc_vars:

        # VERIFY VARIABLE EXISTS

        if var_name not in ds_qc.variables:
            continue

        qc_var = f"{var_name}_qc"

        _log.info(
            f"Creating placeholder QC: {qc_var}"
        )

        # CREATE EMPTY QC FLAGS
        #
        # Populate the QC variable entirely with
        # the DAC fill value (-127) to indicate
        # that no QC evaluation has been performed.

        flags = xr.full_like(
            ds_qc[var_name],
            fill_value=-127,
            dtype="int8"
        )

        # CREATE PLACEHOLDER QC VARIABLE

        ds_qc[qc_var] = xr.DataArray(
            flags,
            dims=ds_qc[var_name].dims,
            coords=ds_qc[var_name].coords,
            attrs={

                "long_name":
                    f"{var_name} Quality Flag",

                "standard_name":
                    get_qc_standard_name(
                        ds_qc,
                        var_name
                    ),

                "flag_values":
                np.array([1, 2, 3, 4, 9], dtype="int8"),

                "flag_meanings":
                    (
                        "GOOD "
                        "UNKNOWN "
                        "SUSPECT "
                        "FAIL "
                        "MISSING"
                    ),

                "valid_min": 1,

                "valid_max": 9,
            },
        )

        # UPDATE ANCILLARY VARIABLE LINK

        ds_qc[var_name].attrs[
            "ancillary_variables"
        ] = qc_var

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
                "zlib": True
            }

    # WRITE NETCDF FILE
    #
    # Save the QC-enhanced dataset using the
    # NETCDF4 format and the netcdf4 backend.

    ds_qc.to_netcdf(
        output_file,
        engine="netcdf4",
        format="NETCDF4",
        encoding=encoding
    )

    # LOG OUTPUT FILE

    _log.info(
        f"Saved QC dataset: {output_file}"
    )


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

    # OPEN INPUT DATASET
    #
    # Load the science dataset into memory and
    # immediately close the underlying file handle
    # to prevent file-locking issues during
    # subsequent processing and output writing.

    ds = xr.open_dataset(input_file)

    ds.load()

    ds.close()

    # IDENTIFY VARIABLES FOR QARTOD PROCESSING
    #
    # Determine which dataset variables contain
    # a time dimension and should participate in
    # QARTOD quality control testing.

    time_variables = find_time_variables(ds)

    # LOAD QARTOD CONFIGURATION
    #
    # Read the YAML configuration file and
    # convert it into a Python dictionary.
    config_dict = load_qartod_config(
        config_file
    )

    # AUTO-GENERATE MISSING CONFIGURATIONS
    #
    # Create default QARTOD configurations for
    # variables that are present in the dataset
    # but not explicitly defined in the YAML file.

    config_dict = add_missing_variables_to_config(
        
        ds,

        config_dict,

        time_variables
    )

    # BUILD IOOS QC CONFIGURATION OBJECT
    #
    # Convert the configuration dictionary into
    # an ioos_qc Config object that can be used
    # by the QARTOD processing framework.

    config = build_ioos_qc_config(
        config_dict
    )

    # EXECUTE QARTOD TESTS
    #
    # Run all configured QARTOD tests against
    # the selected dataset variables.

    collected = run_qartod_tests(
        ds,
        config
    )

    # GROUP RESULTS BY VARIABLE
    #
    # Reorganize individual QARTOD test results
    # into a variable-centric structure that can
    # be used to generate aggregate QC variables.

    grouped_results = group_qartod_results(
        ds,
        collected
    )

    # CREATE AGGREGATE QC VARIABLES
    #
    # Generate DAC-compliant aggregate QC
    # variables by combining the individual
    # QARTOD test results for each variable.

    print(list(grouped_results.keys()))

    ds_qc = create_qc_variables(

        ds,

        grouped_results,

        overwrite_qc=overwrite_qc
    )

    # ADD QC PROVENANCE METADATA
    #
    # Record the installed ioos_qc package
    # version to support reproducibility and
    # future QC audits.

    ds_qc.attrs["ioos_qc_version"] = ioos_qc.__version__

    # CREATE PLACEHOLDER QC VARIABLES
    #
    # Generate DAC-required placeholder QC
    # variables for metadata time fields that
    # are not evaluated using QARTOD tests.
    ds_qc = create_placeholder_qc_variables(
        ds_qc
    )

    # SAVE QC-ENHANCED DATASET
    #
    # Write the final dataset containing all
    # science variables, aggregate QC variables,
    # placeholder QC variables, and provenance
    # metadata to NetCDF.
    #
    save_qc_dataset(

        ds_qc,

        output_file
    )

    # LOG WORKFLOW COMPLETION

    _log.info(
        "Completed QARTOD QC workflow"
    )