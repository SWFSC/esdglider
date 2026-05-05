# Changelog

All notable changes to the esdglider package will be documented in this file. See the [ESD glider lab manual](https://swfsc.github.io/glider-lab-manual/content/glider-data.html) for descriptions for processing functionality, data products, etc.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Changed the name of the module `glider` to `slocum`, to refect that this module is specific to slocum data
- Added a `paths` module for all functions involved in generating file or directory paths. Moved all such path functions from other modules into `paths`, and updated these path functions to use the new ESD prod directory structure. 
- Changed assorted functions to take in the deployment name and mode directly, rather than a `deployment_info` dictionary
- Changed to extracting the date extracting EXIF metadata from the imagery files, rather than deriving image datetimes from the filenames. This included writing relevant metadata to a metadata-specific bucket, and reading the datetimes from the metadata files. Specifically:
    - Added functions to the `imagery` module and a script (`extract-image-metadata.py`) for extracting EXIF metadata from the imagery files, and writing these to the metadata bucket
    - Removed `solocam_img_meta`, and added `solocam_dt_from_meta` to the `imagery` module for formatting the image datatimes
    - Added `utils.check_string_length`, for checking that all strings in a list (e.g., solocam image file names) are the same length, and returning useful warning logs if not
    - Changed `imagery.imagery_timeseries` to use `solocam_dt_from_meta` and `check_string_length`
- Changed `gcp` mount and unmount functions to capture gcsfuse output and send to the logger

## [0.4.0] - 2026-04-17

- `esdglider` currently relies on the [smwoodman pyglider fork](https://github.com/smwoodman/pyglider). A PR is currently open [here](https://github.com/c-proof/pyglider/pull/227)
- Fixed the gridding code to use proper methods for different input boolean flags
- Changed the gridding code to use the new pyglider gridding function
- Changed config module:
    - Functions that scrape info from the database to all expect a `sqlalchemy.engine.Connectable` object as the first input
    - Changed `instrument_attrs` to `_get_instrument_attrs`,  and `make_deployment_table` to `get_deployment_table`, for consistency with functions from other modules
    - Changed `get_deployment_table` to handle Location column, after updating the database
    - `make_website_yaml` now takes a dataframe (the output of `get_deployment_table`) as an input, rather than a database connection
    - The `make...yaml` functions now return the dictionary rather than the file path
    - `_read_esdglider_yaml` is now a full internal function
    - Added ability to use new Location table columns (Region_Name and Sea_Name)
- Changed plots to use a set legend position when possible, and to remove the 'inflections' tvt plot as it is functionally a duplicate of the 'diveEnergy' tvt plot
- Changed `glider.binary_to_nc` to make the gridded data files using `glider.make_gridfiles_depth_measured` if the argument 'sci_timeseries_pyglider' is True
- Changed `glider` so that science post-processing happens consistently in `glider.binary_to_nc`, rather than `glider.timeseries_raw_to_sci`
- Changed `binary_to_raw_timeseries` to only keep distance_over_ground values where latitude and longitude are not nan, to be consistent with depth_ctd
- Changed the name of the function `glider.raw_to_sci_timeseries` to `glider.raw_to_sci_timeseries`, to be more consistent with `esdglider` and `pyglider`
- Added a 'generate fleet status' python script, for updating the various tabs on the Fleet Status Google sheet with summaries from the database. 
- Updating config website yaml


## [0.3.0] - 2025-07-22

- Changed package config fully to pyproject.toml
- Changed get_path_ functions to be both consistent across modules, and more useful outside of GCP. These changes include:
    - Renamed `glider.get_path_deployment` to `glider.get_path_glider`
    - Added new helper functions in the acoustics, glider, and imagery modules to handle the generation of paths strictly within each acoustics/glider/imagery deployment folder. These functions have a '_deployment' suffix, e.g. `glider.get_path_glider_deployment`
    - Added paths for files written in the acoustics and imagery modules to `acoustics.get_path_acoustics` and `imagery.get_path_imagery`
- Changed all config module functions to accept an engine argument, rather than a db_url


## [0.2.0] - 2025-07-22

- Changed repo name from glider-utils to esdglider (#35)
- Changed `glider.binary_to_nc` to pass 'sci_water_pressure' to `pyglider.slocum.binary_to_timeseries`, rather than 'sci_water_temp'
- Changed `glider.binary_to_nc` so that it uses `pyglider.slocum.binary_to_timeseries` to generate the engineering timeseries for all deployments
- Added a new package data file 'deployment-raw-vars.yml'. This file contains contents of the 'deployment-eng-vars.yml' that should not be interpolated (e.g., commanded parameters). Changed `glider.binary_to_nc` to use this new file, and generalized `get_path_engyaml` to `get_path_yaml(yaml_type)` to get the path for either the eng or raw yaml.
- Changed tvt plots to use the raw dataset, after moving some variables from the eng yaml to the raw yaml
- Removed waypoint_latitude and waypoint_longitude from the default glider config files. As commanded variables, they will remain in the 'deployment-raw-vars.yml' file, and thus as part of the raw dataset
- Fixed 'processing_level' attribute to be representative across raw, engineering, and science timeseries
- Changed the name of the measured depth in the raw data file to 'depth_measured'. This is consistent with 'depth_ctd', and will hopefully minimize confusion if for instance 'depth_measured' is included in the science timeseries because the CTD was occasionally turned off.
- Changed `utils.calc_profile_summary` so that the user must provide the name of the depth variable to use for the summary
- Changed `utils.check_profiles` so that it now takes a DataFrame (the output of `utils.calc_profile_summary`) as it's input, rather than the Dataset
- Fixed spatialgrid and spatialsection plots by sorting by either latitude and longitude (as relevant) before calling pcolormesh. This fixed the issue flagged by the warning "UserWarning: The input coordinates to pcolormesh are interpreted as cell centers, but are not monotonically increasing or decreasing. This may lead to incorrectly calculated cell edges, in which case, please supply explicit cell edges to pcolormesh."
- Fixed grid plots so that depth bins with nan values across the whole deployment are not plotted.
- Changed gridding wrapper function to ignore certain variables (note: at the time of release, this change requires https://github.com/smwoodman/pyglider)
- Added the function `glider.make_gridfiles_depth_measured` to make gridded NetCDF files using the glider measured depth.
- Added the function `glider.grid_esd` to ensure that ESD data are gridded consistently across different ESD processing pathways. This function uses new module-level variables to define the variables to exclude from gridding, maximum depth value, and depth bin spacing.
- Changed the esdglider function `decompress` to `decompress_dir`, to be explicit and consistent with dbdreader's `decompress_file`
- Changed skyfield and timezonefinder to optional dependencies, since they are only used by a single function
- Fixed `plots.sci_surface_map` to plot 'profile_index', even though `pyglider` renames this variable to 'profile' (note: at the time of release, this change requires https://github.com/smwoodman/pyglider)
- Changed acoustics path function name to proper spelling `acoustics.get_path_acoustics` (previously 'get_path_acoutics')


## [0.1.0] - 2025-07-17

- Initial release of esdglider, for basic processing of ESD glider data
