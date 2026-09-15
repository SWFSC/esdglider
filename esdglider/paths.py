import logging
import os
from importlib import resources
from pathlib import Path

from esdglider import utils

_log = logging.getLogger(__name__)


"""
Production bucket names
"""
logs_bucket_name = "swfscesd-glider-logs"
data_in_bucket_name = "swfscesd-glider-deployments-data-in"
data_out_bucket_name = "swfscesd-glider-deployments-data-out"
imagery_in_bucket_name = "swfscesd-glider-imagery-data-in"
imagery_meta_bucket_name = "swfscesd-glider-imagery-metadata"
aa_in_bucket_name = "swfscesd-glider-active-acoustics-data-in"


def _check_dir_exists(dir_path, description):
    if not os.path.isdir(dir_path):
        _log.debug("The %s path ('%s') does not exist", description, dir_path)

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

def get_path_yaml_slocum_vars(yaml_type: str) -> str:
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
    if yaml_type not in ["raw", "eng"]:
        _log.error("yaml_type %s", yaml_type)
        raise ValueError("yaml_type must be either 'raw' or 'eng'")

    ref = resources.files("esdglider.data") / f"slocum-{yaml_type}-vars.yml"
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


def get_path_glider(
    deployment_name: str, 
    mode: str, 
    *, 
    home_path: str | Path = "",
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
        Must be either 'rt', for real-time, or 'delayed', for delayed mode.
    home_path : str
        The (local) path to the home directory; defaults to '/home/user'.
    cac_path : str
        The (local) path to the folder with the cache files. 
        If not provided, defaults to '<home_path>/standard-glider-files/Cache'.
    config_path : str
        The (local) path to the folder with the deployment yaml files.
        If not provided, defaults to '<home_path>/glider-processing/deployment-configs'.
    data_in_path : str
        The (local) path to the 'data in' folder, with the binary files
        If not provided, defaults to '<home_path>/mnt-gcs/<data_in_bucket_name>'.
    data_out_path : str
        The (local) path to the 'data out' folder
        If not provided, defaults to '<home_path>/mnt-gcs/<data_out_bucket_name>'.
        
    Returns
    -------
        A dictionary with the relevant paths
    """

    # Checks / prep
    year = utils.get_path_year(deployment_name)
    if mode not in ["delayed", "rt"]:
        raise ValueError("mode must be either 'rt' or 'delayed'")

    # Either resolve paths, or generate based on ESD defaults
    home_path = Path(home_path)
    # mnt_path = home_path / "mnt-gcs"
    # data_in_path = str(data_in_path)
    # data_out_path = str(data_out_path)

    # if cac_path == "":
    #     cac_path = str(home_path / "standard-glider-files" / "Cache")
    # else:
    #     cac_path = str(cac_path)    
    cac_path = _resolve_path(cac_path, home_path, "cac")
    if cac_path != "":
        _check_dir_exists(cac_path, "provided cache")
    # _log.info("Using cache path: %s", cac_path)

    if config_path == "":
        config_path = str(home_path / "glider-processing" / "deployment-configs" / year)
    else:
        config_path = str(config_path)
    _log.info("Using config path: %s", config_path)
    # config_path = _resolve_path(config_path, home_path, "config")

    # if data_in_path == "":
    #     data_in_path = str(mnt_path / data_in_bucket_name)
    # else:
    #     data_in_path = str(data_in_path)
    # _log.info("Using data in path: %s", data_in_path)
    data_in_path = _resolve_path(data_in_path, home_path, "data-in")

    # if data_out_path == "":
    #     data_out_path = str(mnt_path / data_out_bucket_name)
    # else:
    #     data_out_path = str(data_out_path)
    # _log.info("Using data out path: %s", data_out_path)
    data_out_path = _resolve_path(data_out_path, home_path, "data-out")

    # Get year

    # Deployment yaml
    deploymentyaml = os.path.join(config_path, f"{deployment_name}.yml")    
    if not os.path.isfile(deploymentyaml):
        _log.warning(
            "The deployment yaml ('%s') does not exist", 
            deploymentyaml
        )

    # cache path

    # Glider data in and data out paths
    glider_data_in_path = os.path.join(data_in_path, year, deployment_name)
    if data_in_path != "":
        _check_dir_exists(glider_data_in_path, "derived glider data in")
    
    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    if data_out_path != "":
        _check_dir_exists(glider_data_out_path, "derived glider data out")

    # glider_paths_data_out = get_path_glider_data_out(
    #     deployment_name = deployment_name,
    #     mode = mode,
    #     glider_data_out_path = glider_data_out_path, 
    # )

    procl0dir = os.path.join(glider_data_out_path, "processed-L0")
    procl1dir = os.path.join(glider_data_out_path, "processed-L1")
    procl2dir = os.path.join(glider_data_out_path, "processed-L2")
    procl3dir = os.path.join(glider_data_out_path, "processed-L3")
    plotdir = os.path.join(glider_data_out_path, "plots", mode)
    ancillarydir = _get_path_ancillary(deployment_name, data_out_path)

    # Separate, in case in the future they end up in their own directories
    rawdir = procl0dir
    tsdir = procl1dir
    griddir = procl3dir
    profdir = os.path.join(procl1dir, "ngdac", mode)

    # ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")

    # Create common file names
    path_prof_summ = os.path.join(ancillarydir, f"{deployment_name}-{mode}-profiles.csv")
    path_raw = os.path.join(rawdir, f"{deployment_name}-{mode}-raw.nc")
    path_sci = os.path.join(tsdir, f"{deployment_name}-{mode}-sci.nc")
    path_eng = os.path.join(tsdir, f"{deployment_name}-{mode}-eng.nc")
    # path_sci_qc = os.path.join(tsdir, f"{deployment_name}-{mode}-sci-qc.nc")

    # These must follow pyglider convention with the "_grid"
    path_gr1 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-1m.nc")
    path_gr5 = os.path.join(griddir, f"{deployment_name}_grid-{mode}-5m.nc")

    # return out | glider_paths_data_out
    return {
        "deploymentyaml": deploymentyaml,
        "mode": mode,
        "cacdir": cac_path,
        "data_in_path": data_in_path,
        "data_out_path": data_out_path,
        # "rawyaml": get_path_yaml("raw"),
        # "engyaml": get_path_yaml("eng"),
        "binarydir": os.path.join(glider_data_in_path, "binary", mode), 
        "outdir": glider_data_out_path,
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
        # "tssciqcpath": path_sci_qc,
        "gr1path": path_gr1,
        "gr5path": path_gr5,
        "profsummpath": path_prof_summ,
    } 


def get_path_aa(
    deployment_name: str, 
    mode: str, 
    *, 
    home_path: str | Path = "",
    aa_in_path: str | Path = "", 
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
        Must be either 'rt', for real-time, or 'delayed', for delayed mode.
    home_path : str
        The (local) path to the home directory; defaults to '/home/user'.
    aa_in_path : str
        The (local) path to the folder with the 'data in' (i.e., raw) acoustic data
        If not provided, defaults to '<home_path>/mnt-gcs/<aa_in_bucket_name>'.
    data_out_path : str
        The (local) path to the glider 'data out' folder
        If not provided, defaults to '<home_path>/mnt-gcs/<data_out_bucket_name>'.

    Returns
    -------
    dict
        A dictionary with the relevant acoustic paths
    """
        
    # # Temporary, until going full pathlib
    # aa_in_path = str(aa_in_path)
    # data_out_path = str(data_out_path)    

    # Either resolve paths, or generate based on ESD defaults
    home_path = Path(home_path)
    # mnt_path = home_path / "mnt-gcs"

    # if aa_in_path == "":
    #     aa_in_path = str(mnt_path / aa_in_bucket_name)
    # else:
    #     aa_in_path = str(aa_in_path)
    # _log.info("Using data out path: %s", aa_in_path)
    aa_in_path = _resolve_path(aa_in_path, home_path, "aa-in")

    # if data_out_path == "":
    #     data_out_path = str(mnt_path / data_out_bucket_name)
    # else:
    #     data_out_path = str(data_out_path)
    # _log.info("Using data out path: %s", data_out_path)
    data_out_path = _resolve_path(data_out_path, home_path, "data-out")


    # Get year from deployment name
    year = utils.get_path_year(deployment_name)

    # Check that relevant deployment path exists
    aa_glider_in_path = os.path.join(
        aa_in_path,
        year,
        deployment_name,
    )
    if aa_in_path != "":
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
    # glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    # if data_out_path != "":
    #     _check_dir_exists(glider_data_out_path, "derived glider data out")

    # ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    ancillarydir = _get_path_ancillary(deployment_name, data_out_path)
    echoviewdir = os.path.join(ancillarydir, "echoview")

    regionspath = os.path.join(echoviewdir, f"{deployment_name}-regions.csv")
    pitchpath = os.path.join(echoviewdir, f"{deployment_name}.pitch.csv")
    rollpath = os.path.join(echoviewdir, f"{deployment_name}.roll.csv")
    gpspath = os.path.join(echoviewdir, f"{deployment_name}.gps.csv")
    depthpath = os.path.join(echoviewdir, f"{deployment_name}.depth.evl")
    evrpathprefix = os.path.join(echoviewdir, deployment_name)

    return {
        "aa_in_path": aa_in_path,
        "data_out_path": data_out_path,
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


def get_path_imagery(
    deployment_name: str, 
    *, 
    home_path: str | Path = "",
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
        If not provided, defaults to '<home_path>/mnt-gcs/<data_out_bucket_name>'.

    Returns
    -------
    dict
        A dictionary with the relevant imagery-related paths,
        for a given glider deployment
    """

    # # Temporary, until going full pathlib
    # imagery_in_path = str(imagery_in_path)
    # imagery_meta_path = str(imagery_meta_path)
    # data_out_path = str(data_out_path)

    # Either resolve paths, or generate based on ESD defaults
    home_path = Path(home_path)
    # mnt_path = home_path / "mnt-gcs"
    # data_in_path = str(data_in_path)
    # data_out_path = str(data_out_path)

    # if imagery_in_path == "":
    #     imagery_in_path = str(mnt_path / imagery_in_bucket_name)
    # else:
    #     imagery_in_path = str(imagery_in_path)
    # _log.info("Using imagery in path: %s", imagery_in_path)
    imagery_in_path = _resolve_path(imagery_in_path, home_path, "img-in")

    # if imagery_meta_path == "":
    #     imagery_meta_path = str(mnt_path / imagery_meta_bucket_name)
    # else:
    #     imagery_meta_path = str(imagery_meta_path)
    # _log.info("Using imagery metadata path: %s", imagery_meta_path)
    imagery_meta_path = _resolve_path(imagery_meta_path, home_path, "img-meta")

    # if data_out_path == "":
    #     data_out_path = str(mnt_path / data_out_bucket_name)
    # else:
    #     data_out_path = str(data_out_path)
    # _log.info("Using data out path: %s", data_out_path)
    data_out_path = _resolve_path(data_out_path, home_path, "data-out")

    year = utils.get_path_year(deployment_name)

    imagery_glider_in_path = os.path.join(
        imagery_in_path,
        year,
        deployment_name,
    )
    if imagery_in_path != "":
        _check_dir_exists(imagery_glider_in_path, "imagery data in")
    # if not os.path.isdir(imagery_glider_in_path):
    #     _log.warning("%s does not exist", imagery_glider_in_path)

    imagery_glider_meta_path = os.path.join(
        imagery_meta_path,
        year,
        deployment_name,
    )
    if imagery_meta_path != "":
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

    # glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    # if data_out_path != "":
    #     _check_dir_exists(glider_data_out_path, "derived glider data out")

    # ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    ancillarydir = _get_path_ancillary(deployment_name, data_out_path)
    imgcsv = os.path.join(ancillarydir, f"{deployment_name}-imagery-ancillary.csv")

    return {
        "imagery_in_path": imagery_in_path,
        "imagery_meta_path": imagery_meta_path,
        "data_out_path": data_out_path,
        "imagedir": os.path.join(imagery_glider_in_path, "images"),
        # "configdir": os.path.join(imagery_glider_in_path, "config"), 
        "metadir": imagery_glider_meta_path, 
        "deplmetapath": depl_meta_path, 
        "imgmetapath": img_meta_path, 
        "ancdir": ancillarydir,
        "imgcsv": imgcsv,
    }


def _get_path_ancillary(deployment_name, data_out_path):
    """
    Return the path to the ancillary products directory for a given deployment.

    Parameters
    ----------
    deployment_name : str
        The name of the deployment, e.g. amlr08-20220513
    data_out_path : str
        The (local) path to the glider 'data out' folder

    Returns
    -------
    str
        The path to the ancillary products directory
    """
    year = utils.get_path_year(deployment_name)
    glider_data_out_path = os.path.join(data_out_path, year, deployment_name)
    ancillarydir = os.path.join(glider_data_out_path, "ancillary-products")
    return ancillarydir


def _resolve_path(p: str | Path, home: Path, type: str):  
    """
    Return the path to a specific bucket, using a default location if not provided.

    Parameters
    ----------
    p : str | Path
        The initial path
    home : Path
        The home directory path
    type : str
        The type of path, which dictates which path to resolve

    Returns
    -------
    str
        The resolved path
    """

    mnt_path = Path("mnt-gcs")

    if type == "data-in":
        end_path = mnt_path / data_in_bucket_name
    elif type == "data-out":
        end_path = mnt_path / data_out_bucket_name
    elif type == "cac":
        end_path = Path("standard-glider-files") / "Cache"
    # elif type == "config":
    #     end_path = Path("glider-processing") / "deployment-configs"
    elif type == "aa-in":
        end_path = mnt_path / aa_in_bucket_name
    elif type == "img-in":
        end_path = mnt_path / imagery_in_bucket_name
    elif type == "img-meta":
        end_path = mnt_path / imagery_meta_bucket_name
    else:
        raise ValueError("Unknown path type: %s", type)

    if p == "":
        p = str(home / end_path)
    else:
        p = str(p)
    _log.info("Using %s path: %s", type, p)

    return p


def get_file_info(file: Path) -> tuple[str, str]:
    """
    Return the file information and log file name for a given file.

    Parameters
    ----------
    file : Path
        The path to the file

    Returns
    -------
    tuple[str, str]
        A tuple containing the file information URL and the log file name
    """
    file_info = f"https://github.com/SWFSC/glider-processing: {file.name}"
    log_file_name = f"{file.stem}.log"

    return file_info, log_file_name