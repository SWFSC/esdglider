import logging
import os
from importlib import resources
from pathlib import Path

from esdglider import utils

_log = logging.getLogger(__name__)

def _check_dir_exists(dir_path, description):
    if not os.path.isdir(dir_path):
        _log.warning(f"The {description} path ('{dir_path}') does not exist")

def get_path_flbbcd_calibrations() -> str:
    """
    Get the path to the flbbcd calibration yaml.
    The yaml is included as part of the package data,
    and contains the relevant calibration constants for the flbbcd sensors
    
    Parameters
    ----------
    None
    
    Returns
    -------
    str
        the path of the yaml
    """
    ref = resources.files("esdglider.data") / "flbbcd-calibrations.yml"
    with resources.as_file(ref) as path:
        return str(path)

def get_path_yaml_deployment_vars(yaml_type: str) -> str:
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
        the path of the specified yaml
    """
    if yaml_type not in ["raw", "eng", "raw-solocam"]:
        _log.error("yaml_type %s", yaml_type)
        raise ValueError("yaml_type must be either 'raw', 'eng', or 'raw-solocam'")

    ref = resources.files("esdglider.data") / f"deployment-{yaml_type}-vars.yml"
    with resources.as_file(ref) as path:
        return str(path)

def get_path_qartod_config() -> str:
    """
    Get the path to the packaged QARTOD configuration file.
    The configuration file is distributed with the package and
    contains the default IOOS QARTOD test configuration used when
    generating quality-control flags.

    Returns
    -------
    str
        Path to the packaged ``qartod-config.yml`` file.
    """
    ref = resources.files("esdglider.data") / "qartod-config.yml"

    with resources.as_file(ref) as path:
        return str(path)

def get_path_glider_data_out(
    deployment_name: str,
    mode: str,
    glider_data_out_path: str, 
) -> dict:
    """
    Get (i.e., generate) the glider 'data out' paths.
    TODO: does this need to stay separate?    
    
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    This function is typically called by get_path_glider()

    Parameters
    ----------
    deployment_name, mode: see get_path_glider   
    glider_data_out_path : str
        The path to the glider-specifc 'data out' folder. 
        E.g., "swfscesd-glider-deployments-data-out/2022/amlr08-20220513"

    Returns
    -------
    A dictionary of strings that represent the relevant 
    glider-specific directory and file paths:
        list TODO
    """

    procl0dir = os.path.join(glider_data_out_path, "processed-L0")
    procl1dir = os.path.join(glider_data_out_path, "processed-L1")
    procl2dir = os.path.join(glider_data_out_path, "processed-L2")
    procl3dir = os.path.join(glider_data_out_path, "processed-L3")
    plotdir = os.path.join(glider_data_out_path, "plots", mode)

    # Separate, in case in the future they end up in their own directories
    rawdir = procl0dir
    tsdir = procl1dir
    griddir = procl3dir
    profdir = os.path.join(procl1dir, "ngdac", mode)

    ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    path_prof_summ = os.path.join(ancillarydir, f"{deployment_name}-{mode}-profiles.csv")

    # Create common file names
    path_raw = os.path.join(rawdir, f"{deployment_name}-{mode}-raw.nc")
    path_sci = os.path.join(tsdir, f"{deployment_name}-{mode}-sci.nc")
    path_eng = os.path.join(tsdir, f"{deployment_name}-{mode}-eng.nc")
    path_sci_qc = os.path.join(tsdir, f"{deployment_name}-{mode}-sci-qc.nc")

    # These must follow pyglider convention with the "_grid"
    path_gr1 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-1m.nc")
    path_gr5 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-5m.nc")

    return {
        "rawdir": rawdir,
        "tsdir": tsdir,
        "griddir": griddir,
        "profdir": profdir,
        "plotdir": plotdir,
        "ancillarydir": ancillarydir,
        "procl0dir": procl0dir,
        "procl1dir": procl1dir,
        "procl2dir": procl2dir,
        "procl3dir": procl3dir,
        "tsrawpath": path_raw,
        "tsscipath": path_sci,
        "tsengpath": path_eng,
        "tssciqcpath": path_sci_qc,
        "gr1path": path_gr1,
        "gr5path": path_gr5,
        "profsummpath": path_prof_summ,
    }


def get_path_glider(
    deployment_name: str, 
    mode: str, 
    cac_path: str | Path = "",
    config_path: str | Path = "", 
    data_in_path: str | Path = "", 
    data_out_path: str | Path = "", 
) -> dict:
    """
    Return a dictionary of paths needed to process glider data.
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    Parameters
    ----------
    deployment_name : str
        The name of the deployment, e.g. amlr08-20220513
    mode : str
        Mode of the glider data being processed.
        Must be either 'rt', for real-time, or 'delayed
    cac_path : str
        The (local) path to the folder with the cache files
    config_path : str
        The (local) path to the folder with the deployment yaml files
    data_in_path : str
        The (local) path to the 'data in' folder, with the binmary files
    data_out_path : str
        The (local) path to the 'data out' folder
        
    Returns
    -------
        A dictionary with the relevant paths
    """

    # Temporary, until going full pathlib
    cac_path = str(cac_path)
    config_path = str(config_path)
    data_in_path = str(data_in_path)
    data_out_path = str(data_out_path)
    
    # Mode
    if mode not in ["delayed", "rt"]:
        raise ValueError("mode must be either 'rt' or 'delayed'")

    # Deployment yaml
    deploymentyaml = os.path.join(config_path, f"{deployment_name}.yml")    
    if not os.path.isfile(deploymentyaml):
        _log.warning("The deployment yaml ('%s') does not exist", 
                     deploymentyaml)

    # cache path
    _check_dir_exists(cac_path, "provided cac_path")

    # Glider data in and data out paths
    year = utils.year_path(deployment_name)
    glider_data_in_path = os.path.join(data_in_path, year, deployment_name)
    _check_dir_exists(glider_data_in_path, "derived glider data in path")
    # if not os.path.isdir(glider_data_in_path):
    #     _log.warning(f"The derived glider data in path ({glider_data_in_path} does not exist")
    
    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    _check_dir_exists(glider_data_out_path, "derived glider data out path")
    # if not os.path.isdir(glider_data_out_path):
    #     _log.warning(f"The derived glider data out path ({glider_data_out_path} does not exist")

    glider_paths_data_out = get_path_glider_data_out(
        deployment_name = deployment_name,
        mode = mode,
        glider_data_out_path = glider_data_out_path, 
    )

    out = {
        "deploymentyaml": deploymentyaml,
        "mode": mode,
        "cacdir": cac_path,
        # "rawyaml": get_path_yaml("raw"),
        # "engyaml": get_path_yaml("eng"),
        "binarydir": os.path.join(glider_data_in_path, "binary", mode), 
        "outdir": glider_data_out_path,
    } 

    return out | glider_paths_data_out


# def get_path_acoustics_deployment(
#     deployment_path: str,
#     deployment_name: str,
#     mode: str,
# ) -> dict:
#     """
#     Get deployment-specific acoustics paths.
#     Specifically, get all acoutics paths that are within
#     the given deployment folder (deployment_path)

#     This function is typically called by get_path_acoustics()
#     """

#     metadir = os.path.join(deployment_path, "metadata")
#     echoviewdir = os.path.join(metadir, "echoview")

#     regionspath = os.path.join(echoviewdir, f"{deployment_name}-regions.csv")
#     pitchpath = os.path.join(echoviewdir, f"{deployment_name}.pitch.csv")
#     rollpath = os.path.join(echoviewdir, f"{deployment_name}.roll.csv")
#     gpspath = os.path.join(echoviewdir, f"{deployment_name}.gps.csv")
#     depthpath = os.path.join(echoviewdir, f"{deployment_name}.depth.evl")
#     evrpathprefix = os.path.join(echoviewdir, deployment_name)

#     return {
#         "rawdatadir": os.path.join(deployment_path, "data", mode),
#         "configdir": os.path.join(deployment_path, "config"),
#         "metadir": metadir,
#         "echoviewdir": echoviewdir,
#         "regionspath": regionspath,
#         "pitchpath": pitchpath,
#         "rollpath": rollpath,
#         "gpspath": gpspath,
#         "depthpath": depthpath,
#         "evrpathprefix": evrpathprefix,
#     }


def get_path_aa(
        deployment_name: str, 
        mode: str, 
        aa_in_path: str | Path, 
        data_out_path: str | Path = "", 
    ) -> dict:
    """
    Return a dictionary of acoustic-related paths
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    Parameters
    ----------
    deployment_name : str
        The name of the deployment, e.g. amlr08-20220513
    mode : str
        Mode of the glider data being processed.
        Must be either 'rt', for real-time, or 'delayed
    aa_in_path : str
        The (local) path to the folder with the 'data in' (i.e., raw) acoustic data
    data_out_path : str
        The (local) path to the glider 'data out' folder

    Returns
    -------
    dict
        A dictionary with the relevant acoustic paths
    """
        
    # Temporary, until going full pathlib
    aa_in_path = str(aa_in_path)
    data_out_path = str(data_out_path)

    year = utils.year_path(deployment_name)

    # Check that relevant deployment path exists
    aa_glider_in_path = os.path.join(
        aa_in_path,
        year,
        deployment_name,
    )
    _check_dir_exists(aa_glider_in_path, "derived acoustic deployment")
    # if not os.path.isdir(acoustic_deployment_path):
    #     _log.warning(f"The derived acoustic path ({acoustic_deployment_path}) does not exist")

    # Return dictionary of file paths
    # deployment_paths_out = get_path_acoustics_deployment(
    #     acoustic_deployment_path,
    #     deployment_name,
    #     mode,
    # )
    # metadir = os.path.join(acoustic_deployment_path, "metadata")
    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)

    ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    echoviewdir = os.path.join(ancillarydir, "echoview")

    regionspath = os.path.join(echoviewdir, f"{deployment_name}-regions.csv")
    pitchpath = os.path.join(echoviewdir, f"{deployment_name}.pitch.csv")
    rollpath = os.path.join(echoviewdir, f"{deployment_name}.roll.csv")
    gpspath = os.path.join(echoviewdir, f"{deployment_name}.gps.csv")
    depthpath = os.path.join(echoviewdir, f"{deployment_name}.depth.evl")
    evrpathprefix = os.path.join(echoviewdir, deployment_name)

    return {
        "rawdatadir": os.path.join(aa_glider_in_path, "data", mode),
        "configdir": os.path.join(aa_glider_in_path, "config"),
        "ancdir": ancillarydir,
        "echoviewdir": echoviewdir,
        "regionspath": regionspath,
        "pitchpath": pitchpath,
        "rollpath": rollpath,
        "gpspath": gpspath,
        "depthpath": depthpath,
        "evrpathprefix": evrpathprefix,
    }


# def get_path_imagery_deployment(
#     deployment_name: str,
#     glider_data_out_path: str,
# ) -> dict:
#     """
#     Get deployment-specific imagery paths.
#     Specifically, get all imagery paths that are within
#     the given deployment folder (deployment_path)

#     This function is typically called by get_path_imagery()

#     Parameters
#     ----------
#     deployment_name, mode: see get_path_glider
#     glider_data_out_path : str
#         The path to the glider-specifc 'data out' folder. 
#         E.g., "swfscesd-glider-deployments-data-out/2022/amlr08-20220513"

#     Returns
#     -------
#     A dictionary of strings that represent the relevant 
#     glider-specific directory and file paths: 
#         list TODO
#     """

#     ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
#     imgcsv = os.path.join(ancillarydir, f"{deployment_name}-imagery-ancillary.csv")

#     return {
#         "ancillarydir": ancillarydir,
#         "imgcsv": imgcsv,
#     }


def get_path_imagery(
        deployment_name: str, 
        imagery_in_path: str | Path = "", 
        imagery_meta_path: str | Path = "", 
        data_out_path: str | Path = "", 
    ) -> dict:
    """
    Return a dictionary of imagery-related paths
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

   Parameters
    ----------
    deployment_name : str
        The name of the deployment, e.g. amlr08-20220513
    imagery_in_path : str
        The (local) path to the folder with the 'data in' (i.e., raw) imagery
    imagery_meta_path : str
        The (local) path to the folder with the imagery metadata files
    data_out_path : str
        The (local) path to the glider 'data out' folder

    Returns
    -------
    dict
        A dictionary with the relevant imagery-related paths,
        for a given glider deployment
    """

    # Temporary, until going full pathlib
    imagery_in_path = str(imagery_in_path)
    imagery_meta_path = str(imagery_meta_path)
    data_out_path = str(data_out_path)
    
    year = utils.year_path(deployment_name)

    imagery_glider_in_path = os.path.join(
        imagery_in_path,
        year,
        deployment_name,
    )
    _check_dir_exists(imagery_glider_in_path, "imagery data in")
    # if not os.path.isdir(imagery_glider_in_path):
    #     _log.warning("%s does not exist", imagery_glider_in_path)

    imagery_glider_meta_path = os.path.join(
        imagery_meta_path,
        year,
        deployment_name,
    )
    _check_dir_exists(imagery_glider_meta_path, "imagery metadata")
    # if not os.path.isdir(imagery_glider_in_path):
    #     _log.warning("%s does not exist", imagery_glider_in_path)

    depl_meta_path = os.path.join(
        imagery_glider_meta_path, 
        f"{deployment_name}-deployment-metadata.json"
    )
    img_meta_path = os.path.join(
        imagery_glider_meta_path, 
        f"{deployment_name}-image-metadata.jsonl"
    )

    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    _check_dir_exists(glider_data_out_path, "derived glider data out")


    ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    imgcsv = os.path.join(ancillarydir, f"{deployment_name}-imagery-ancillary.csv")

    return {
        "imagedir": os.path.join(imagery_glider_in_path, "images"),
        # "configdir": os.path.join(imagery_glider_in_path, "config"), 
        "metadir": imagery_glider_meta_path, 
        "deplmetapath": depl_meta_path, 
        "imgmetapath": img_meta_path, 
        "ancdir": ancillarydir,
        "imgcsv": imgcsv,
    }