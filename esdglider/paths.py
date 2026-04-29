import logging
import os
from importlib import resources

import esdglider.utils as utils

_log = logging.getLogger(__name__)


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
    if yaml_type not in ["raw", "eng"]:
        _log.error("yaml_type %s", yaml_type)
        raise ValueError("yaml_type must be either 'raw' or 'eng'")

    ref = resources.files("esdglider.data") / f"deployment-{yaml_type}-vars.yml"
    with resources.as_file(ref) as path:
        return str(path)


def get_path_glider_deployment(
    deployment_name: str,
    mode: str,
    glider_data_in_path: str, 
    glider_data_out_path: str, 
) -> dict:
    """
    Get (i.e., generate) deployment-specific paths.
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    This function is typically called by get_path_glider()

    Parameters
    ----------
    deployment_name, mode: see get_path_glider
    glider_data_in_path : str
        The path to the glider-specifc 'data in' folder. 
        E.g., "swfscesd-glider-deployments-data-in/2022/amlr08-20220513"    
    glider_data_out_path : str
        The path to the glider-specifc 'data out' folder. 
        E.g., "swfscesd-glider-deployments-data-out/2022/amlr08-20220513"

    Returns
    -------
    A dictionary of strings that represent the relevant 
    glider-specific directory and file paths:
        list TODO
    """

    binarydir = os.path.join(glider_data_in_path, "binary", mode)
    # rawyaml = get_path_yaml("raw")
    # engyaml = get_path_yaml("eng")

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

    # Create common file names
    path_raw = os.path.join(rawdir, f"{deployment_name}-{mode}-raw.nc")
    path_prof_summ = os.path.join(rawdir, f"{deployment_name}-{mode}-profiles.csv")
    path_sci = os.path.join(tsdir, f"{deployment_name}-{mode}-sci.nc")
    path_eng = os.path.join(tsdir, f"{deployment_name}-{mode}-eng.nc")

    # These must follow pyglider convention with the "_grid"
    path_gr1 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-1m.nc")
    path_gr5 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-5m.nc")

    return {
        "binarydir": binarydir,
        # "rawyaml": rawyaml,
        # "engyaml": engyaml,
        "rawdir": rawdir,
        "tsdir": tsdir,
        "griddir": griddir,
        "profdir": profdir,
        "plotdir": plotdir,
        "procl0dir": procl0dir,
        "procl1dir": procl1dir,
        "procl2dir": procl2dir,
        "procl3dir": procl3dir,
        "tsrawpath": path_raw,
        "tsscipath": path_sci,
        "tsengpath": path_eng,
        "gr1path": path_gr1,
        "gr5path": path_gr5,
        "profsummpath": path_prof_summ,
    }


def get_path_glider(
    deployment_name: str, 
    mode: str, 
    cac_path: str,
    config_path: str, 
    data_in_path: str, 
    data_out_path: str, 
) -> dict:
    """
    Return a dictionary of paths for use by other esdglider functions.
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

    # Deployment yaml
    if not os.path.isdir(config_path):
        raise FileNotFoundError(f"{config_path} does not exist")
    deploymentyaml = os.path.join(config_path, f"{deployment_name}.yml")


    # Glider data in and data out paths
    year = utils.year_path(deployment_name)
    glider_data_in_path = os.path.join(data_in_path, year, deployment_name)
    if not os.path.isdir(glider_data_in_path):
        raise FileNotFoundError(f"{glider_data_in_path} does not exist")
    
    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    # if not os.path.isdir(glider_data_out_path):
    #     raise FileNotFoundError(f"{glider_data_out_path} does not exist")

    deployment_paths_out = get_path_glider_deployment(
        deployment_name,
        mode,
        glider_data_in_path, 
        glider_data_out_path, 
    )

    # cache path
    if not os.path.isdir(cac_path):
        raise FileNotFoundError(f"{cac_path} does not exist")
    
    # Mode
    if mode not in ["delayed", "rt"]:
        raise ValueError("mode must be either 'rt' or 'delayed'")

    # package yamls
    rawyaml = get_path_yaml("raw")
    engyaml = get_path_yaml("eng")

    out = {
        "deploymentyaml": deploymentyaml,
        "mode": mode,
        "cacdir": cac_path,
        "rawyaml": rawyaml,
        "engyaml": engyaml,
    } 

    return out | deployment_paths_out


def get_path_acoustics_deployment(
    deployment_path: str,
    deployment_name: str,
    mode: str,
) -> dict:
    """
    Get deployment-specific acoustics paths.
    Specifically, get all acoutics paths that are within
    the given deployment folder (deployment_path)

    This function is typically called by get_path_acoustics()
    """

    metadir = os.path.join(deployment_path, "metadata")
    echoviewdir = os.path.join(metadir, "echoview")

    regionspath = os.path.join(echoviewdir, f"{deployment_name}-regions.csv")
    pitchpath = os.path.join(echoviewdir, f"{deployment_name}.pitch.csv")
    rollpath = os.path.join(echoviewdir, f"{deployment_name}.roll.csv")
    gpspath = os.path.join(echoviewdir, f"{deployment_name}.gps.csv")
    depthpath = os.path.join(echoviewdir, f"{deployment_name}.depth.evl")
    evrpathprefix = os.path.join(echoviewdir, deployment_name)

    return {
        "rawdatadir": os.path.join(deployment_path, "data", mode),
        "configdir": os.path.join(deployment_path, "config"),
        "metadir": metadir,
        "echoviewdir": echoviewdir,
        "regionspath": regionspath,
        "pitchpath": pitchpath,
        "rollpath": rollpath,
        "gpspath": gpspath,
        "depthpath": depthpath,
        "evrpathprefix": evrpathprefix,
    }


def get_path_acoustics(deployment_name: str, mode: str, acoustic_path: str):
    """
    Return a dictionary of acoustic-related paths
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

    Parameters
    ----------
    deployment_info : dict
        A dictionary with the relevant deployment info. Specifically:
        deploymentyaml : str
            The filepath of the glider deployment yaml.
            This file will have relevant info,
            including deployment name (eg, amlr01-20210101) and project
        mode : str
            Mode of the glider data being processed.
            Must be either 'rt', for real-time, or 'delayed
    acoustic_path : str
        The path to the top-level folder of the acoustic data.
        This is intended to be the path to the mounted acoustic bucket

    Returns
    -------
    dict
        A dictionary with the relevant acoustic paths
    """

    # # Extract or calculate relevant info
    # deploymentyaml = deployment_info["deploymentyaml"]
    # mode = deployment_info["mode"]
    # deployment = utils.read_deploymentyaml(deploymentyaml)

    # deployment_name = deployment["metadata"]["deployment_name"]
    # project = deployment["metadata"]["project"]
    year = utils.year_path(deployment_name)

    # Check that relevant deployment path exists
    acoustic_deployment_path = os.path.join(
        acoustic_path,
        year,
        deployment_name,
    )
    if not os.path.isdir(acoustic_deployment_path):
        raise FileNotFoundError(f"{acoustic_deployment_path} does not exist")

    # Return dictionary of file paths
    deployment_paths_out = get_path_acoustics_deployment(
        acoustic_deployment_path,
        deployment_name,
        mode,
    )
    return deployment_paths_out



# TODO: update
def get_path_imagery_deployment(
    deployment_name: str,
    imagery_glider_in_path: str,
    glider_data_out_path: str,
) -> dict:
    """
    Get deployment-specific imagery paths.
    Specifically, get all imagery paths that are within
    the given deployment folder (deployment_path)

    This function is typically called by get_path_imagery()

    Parameters
    ----------
    deployment_name, mode: see get_path_glider
    glider_data_in_path : str
        The path to the glider-specifc 'data in' folder. 
        E.g., "swfscesd-glider-deployments-data-in/2022/amlr08-20220513"    
    glider_data_out_path : str
        The path to the glider-specifc 'data out' folder. 
        E.g., "swfscesd-glider-deployments-data-out/2022/amlr08-20220513"

    Returns
    -------
    A dictionary of strings that represent the relevant 
    glider-specific directory and file paths: 
        list TODO
    """

    ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    imgcsv = os.path.join(ancillarydir, f"{deployment_name}-imagery-ancillary.csv")

    return {
        "imagedir": os.path.join(imagery_glider_in_path, "images"),
        # "configdir": os.path.join(imagery_glider_in_path, "config"), #TODO - tbd
        "ancillarydir": ancillarydir,
        "imgcsv": imgcsv,
    }


def get_path_imagery(
        deployment_name: str, 
        imagery_in_path: str, 
        data_out_path: str, 
        ):
    """
    Return a dictionary of imagery-related paths
    These paths follow the directory structure outlined here:
    https://swfsc.github.io/glider-lab-manual/content/data-management.html

   Parameters
    ----------
    deployment_name : str
        The name of the deployment, e.g. amlr08-20220513
   imagery_in_path : str
        The (local) path to the folder with the 'data in' (i.e., raw) imagery data
    data_out_path : str
        The (local) path to the 'data out' folder

    Returns
    -------
    dict
        A dictionary with the relevant paths, generated by
        get_path_imagery_deployment
    """

    # # Extract or calculate relevant info
    # deploymentyaml = deployment_info["deploymentyaml"]
    # # mode = deployment_info["mode"]
    # deployment = utils.read_deploymentyaml(deploymentyaml)

    # deployment_name = deployment["metadata"]["deployment_name"]
    # project = deployment["metadata"]["project"]
    year = utils.year_path(deployment_name)

    # Check that relevant deployment path exists
    imagery_glider_in_path = os.path.join(
        imagery_in_path,
        year,
        deployment_name,
    )
    if not os.path.isdir(imagery_glider_in_path):
        raise FileNotFoundError(f"{imagery_glider_in_path} does not exist")

    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)

    # Return dictionary of file paths
    deployment_paths_out = get_path_imagery_deployment(
        deployment_name,
        imagery_glider_in_path,
        glider_data_out_path, 
    )
    return deployment_paths_out
