import logging
import os
from importlib.resources import as_file, files

import numpy as np
import pandas as pd
import sqlalchemy
import yaml
from google.cloud import storage

# from esdglider.acoustics import get_path_acoustics_deployment
from esdglider.gcp import check_gcs_file_exists, check_gcs_directory_exists
from esdglider.glider import get_path_glider_deployment
from esdglider.imagery import get_path_imagery_deployment
from esdglider.utils import split_deployment, year_path

_log = logging.getLogger(__name__)


# Names of Components in the ESD Glider Database (table Device_Type)
# NOTE: if changing a key or value, must adjust code below
db_components = {
    "ctd": "CTD",
    "flbbcd": "flbbcd Fluorometer",
    "oxygen": "Oxygen Optode",
    "shadowgraph": "Shadowgraph camera",
    "glidercam": "Glidercam",
    "azfp": "AZFP",
    "echosounder": "Sig 100 compact echosounder",
    "par": "PAR sensor",
    "dmon": "DMON",
    "wispr": "WISPR",
    "hydrophone": "Hydrophone",
}

camera_models = {
    "glidercam": "Glidercam",
    "shadowgraph-narrow": "Shadowgraph camera wa-solo-sg-11cm",
    "shadowgraph-wide": "Shadowgraph camera wa-solo-sg-14cm",
}

# Calibration type name from Calibration_Type table
db_factory_cal = ["Factory - Initial", "Factory - Recalibration"]


def instrument_attrs(key, devices, dev_df, cal_df):
    """
    component: str
        Name of database component key, eg 'ctd' or 'oxygen'.
        Name must be a key in db_components
    devices: dict
        Devices dictionary, read in from yaml file
    dev_df: DataFrame
        Pandas dataframe of devices, filtered for Deployment ID
    cal_df: DataFrame
        Pandas dataframe of device calibrations, filtered for Deployment ID
    """

    dev_components = dev_df["Component"]
    component = db_components[key]
    instr = devices[key]

    # Get instrument attributes: serial, make/model, etc
    dev_curr = dev_df[dev_components == component]
    instr["serial_number"] = dev_curr["Serial_Num"].values[0]
    instr["description"] = dev_curr["Device_Description"].fillna("").values[0]
    instr["make_model"] = (
        f"{dev_curr['Manufacturer'].values[0]} {dev_curr['Model'].values[0]}"
    )

    # If CTD, add 'pumped' comment
    if key == "ctd":
        if instr["make_model"] == "Sea-Bird GPCTD":
            instr["comment"] = "Pumped"
        else:
            _log.warning("Unknown CTD make/model")

    # TODO: firmware version

    # Get calibration date, and factory calibration if applicable
    cal_curr = cal_df[cal_df["Component"] == component]
    if cal_curr.shape[0] > 1:
        raise ValueError(f"Multiple calibrations for {component}")
    elif cal_curr.shape[0] == 1:
        instr["calibration_date"] = str(cal_curr["Calibration_Date"].values[0])[:10]
        if cal_curr["Calibration_Type"].values[0] in db_factory_cal:
            instr["factory_calibrated"] = instr["calibration_date"]
    else:
        _log.warning(f"No calibration info for component {component}")

    return instr


def make_deployment_yaml(
    deployment_name: str,
    out_path: str,
    db_url: str | sqlalchemy.URL,
):
    """
    Parameters
    ----------
    deployment_name : str
        name of the glider deployment. Eg, amlr01-20200101.
        Only need the name of the deployment,
        because the database contains the project
    out_path : str
        path to which to write the output yaml file
    db_url : str
        The database URL, which is passed to sqlalchemy.create_engine
        to connect to the division database to extract glider info.
        If None (default), no connection attempt will be made

    Returns
    -------
    str
        Full path of the output (written) yaml file
    """

    _log.info("Creating config file for deployment %s", deployment_name)
    _log.debug("Reading template yaml files")

    def esdglider_yaml_read(yaml_name):
        with as_file(files("esdglider.data") / yaml_name) as path:
            with open(str(path), "r") as fin:
                return yaml.safe_load(fin)

    metadata = esdglider_yaml_read("metadata.yml")
    netcdf_vars = esdglider_yaml_read("netcdf-variables-sci.yml")
    prof_vars = esdglider_yaml_read("profile-variables.yml")
    devices = esdglider_yaml_read("glider-devices.yml")

    _log.debug("connecting to database, using the following: %s", db_url)
    engine = sqlalchemy.create_engine(db_url)

    Glider_Deployment = pd.read_sql_table(
        "vGlider_Deployment",
        con=engine,
        schema="dbo",
    )
    Deployment_Device = pd.read_sql_table(
        "vDeployment_Device",
        con=engine,
        schema="dbo",
    )
    Deployment_Device_Calibration = pd.read_sql_table(
        "vDeployment_Device_Calibration",
        con=engine,
        schema="dbo",
    )

    # Filter for the glider deployment, using the deployment name
    db_depl = Glider_Deployment[Glider_Deployment["Deployment_Name"] == deployment_name]
    _log.debug("database connection successful")
    # Confirm that exactly one deployment in the db matched deployment name
    if db_depl.shape[0] != 1:
        _log.error(
            "Exactly one row from the Glider_Deployment table "
            + f"must match the deployment name {deployment_name}. "
            + f"Currently, {db_depl.shape[0]} rows matched",
        )
        raise ValueError("Invalid Glider_Deployment match")

    # Extract various deployment info
    # glider_id = db_depl["Glider_ID"].values[0]
    glider_deployment_id = db_depl["Glider_Deployment_ID"].values[0]
    project = db_depl["Project"].values[0]

    # Get metadata info
    metadata["deployment_id"] = str(glider_deployment_id)

    # Filter the Devices table for this deployment
    db_devices = Deployment_Device[
        Deployment_Device["Glider_Deployment_ID"] == glider_deployment_id
    ]
    db_cals = Deployment_Device_Calibration[
        Deployment_Device_Calibration["Glider_Deployment_ID"] == glider_deployment_id
    ]
    components = db_devices["Component"].values

    # Based on the instruments on the glider:
    # 1. Remove netcdf vars from yamls, if necessary
    # 2. Add instrument_ metadata
    instruments = {}
    for key, value in db_components.items():
        if value in components:
            _log.info("Generating config for component %s", value)
            instruments[f"instrument_{key}"] = instrument_attrs(
                key,
                devices,
                db_devices,
                db_cals,
            )
        else:
            # If we're here, it means this instrument is not on the glider
            _log.debug("No component %s", value)
            if key == "ctd":
                raise ValueError("Glider must have a CTD")
            if key == "flbbcd":
                netcdf_vars.pop("chlorophyll", None)
                netcdf_vars.pop("cdom", None)
                netcdf_vars.pop("backscatter_700", None)
            if key == "oxygen":
                netcdf_vars.pop("oxygen_concentration", None)
                netcdf_vars.pop("oxygen_saturation", None)
            if key == "par":
                netcdf_vars.pop("par", None)

    deployment_split = split_deployment(deployment_name)
    metadata["deployment_name"] = deployment_name
    metadata["os_version"] = db_depl["Software_Version"].values[0]
    metadata["project"] = project
    metadata["glider_name"] = deployment_split[0]
    if not any(db_devices["Device_Type"] == "Teledyne Glider Slocum G3"):
        raise ValueError(
            "No device 'Teledyne Glider Slocum G3'. " + "Please add it to the build",
        )
    metadata["glider_serial"] = db_devices.loc[
        db_devices["Device_Type"] == "Teledyne Glider Slocum G3",
        "Serial_Num",
    ].values[0]

    if project == "FREEBYRD":
        metadata["sea_name"] = "Southern Ocean"
    elif project in ["ECOSWIM", "SANDIEGO", "REFOCUS"]:
        metadata["sea_name"] = "Coastal Waters of California"
    else:
        metadata["sea_name"] = "<sea name>"

    deployment_yaml = {
        "metadata": dict(sorted(metadata.items(), key=lambda v: v[0].upper())),
        "glider_devices": instruments,
        "netcdf_variables": netcdf_vars,
        "profile_variables": prof_vars,
    }

    yaml_file = os.path.join(out_path, f"{deployment_name}.yml")
    _log.info(f"writing {yaml_file}")
    with open(yaml_file, "w") as file:
        yaml.dump(deployment_yaml, file, sort_keys=False)

    return yaml_file


def make_website_yaml(engine, out_path):
    """
    Scrape deployment data from the database,
    and write to a yaml file that will be parsed by the quarto website
    """

    _log.debug("database engine %s", engine)
    # _log.debug("connecting to database, using the following: %s", db_url)
    # engine = sqlalchemy.create_engine(db_url)

    _log.info("Get the info for each deployment from the glider database")
    depl_columns = [
        "Glider",
        "Start",
        "End",
        "Location",
        "Sensors",
        "Deployment_Name",
        "Project",
    ]
    df_deployments = make_deployment_table(engine)
    df_foryaml = df_deployments[depl_columns].copy()
    # df_foryaml["Start"] = df_foryaml["Start"].dt.strftime('%Y-%m-%d')
    # df_foryaml["End"] = df_foryaml["End"].dt.strftime('%Y-%m-%d')

    # Make new columns for data URLs
    df_foryaml["report_link"] = ""
    df_foryaml["ERDDAP_link"] = ""
    df_foryaml["gcp_link_tssci"] = ""
    # df_foryaml["gcp_link_gr5"] = ""
    df_foryaml["gcp_link_plots"] = ""
    df_foryaml["gcp_link_imgcsv"] = ""
    df_foryaml["gcp_dir_link_glider"] = ""
    df_foryaml["gcp_dir_link_acoustics"] = ""
    df_foryaml["gcp_dir_link_imagery"] = ""

    _log.info("Get info about what files exist in GCP")
    glider_bucket_name = "amlr-gliders-deployments-dev"
    acoustics_bucket_name = "amlr-gliders-acoustics-dev"
    imagery_bucket_name = "amlr-gliders-imagery-raw-dev"
    console_url = "https://console.cloud.google.com/storage/browser"

    _log.debug("Creating connections to GCS buckets")
    storage_client = storage.Client()
    glider_bucket = storage_client.bucket(glider_bucket_name)
    acoustics_bucket = storage_client.bucket(acoustics_bucket_name)
    imagery_bucket = storage_client.bucket(imagery_bucket_name)
    # check_gcs_directory_exists(bucket, "SANDIEGO/2022/amlr08-20220513/plots/delayed")
    # check_gcs_file_exists(bucket, "SANDIEGO/2022/amlr08-20220504/data/processed-L1/amlr08-20220513-delayed-sci.nc")

    def href_url(bucket_name, path, text):
        out = f"<a href='{console_url}/{bucket_name}/{path}'>{text}</a>"
        return out

    for i, d in df_foryaml.iterrows():
        # Prep
        deployment_name = d["Deployment_Name"]
        _log.info("Working on deployment %s", deployment_name)
        project = d["Project"]
        year = year_path(project, deployment_name)
        mode = "delayed"
        path_pre = os.path.join(project, year, deployment_name)
        # paths_acoustics = get_path_acoustics_deployment(path_pre, deployment_name, mode)
        paths_glider = get_path_glider_deployment(path_pre, deployment_name, mode)
        paths_imagery = get_path_imagery_deployment(path_pre, deployment_name)
        _log.debug("url/path prefix %s", path_pre)

        ### Files
        # Check for science timeseries
        tssci_file = paths_glider["tsscipath"]
        if check_gcs_file_exists(glider_bucket, tssci_file):
            # url = f"<a href='{console_url}/{glider_bucket_name}/{tssci_file}'>timeseries</a>"
            url = href_url(glider_bucket_name, tssci_file, "timeseries")
            df_foryaml.loc[i, "gcp_link_tssci"] = url  # type: ignore

        # # Check for gr5 dataset
        # gr5_file = paths_glider["gr5path"]
        # if check_gcs_file_exists(glider_bucket, gr5_file):
        #     url = f"<a href='{console_url}/{glider_bucket_name}/{gr5_file}'>plots</a>"
        #     df_foryaml.loc[i, "gcp_link_gr5"] = url  # type: ignore
        # del gr5_file

        # Check for plots
        plots_path = paths_glider["plotdir"]
        if check_gcs_directory_exists(glider_bucket, plots_path):
            # url = f"<a href='{console_url}/{glider_bucket_name}/{plots_path}'>plots</a>"
            url = href_url(glider_bucket_name, plots_path, "plots")
            df_foryaml.loc[i, "gcp_link_plots"] = url  # type: ignore

        # Check for acoustics NetCDF (hold)

        # Check for imagery metadata CSV
        imgcsv_file = paths_imagery["imgcsv"]
        if check_gcs_file_exists(glider_bucket, imgcsv_file):
            # url = f"<a href='{console_url}/{imagery_bucket_name}/{imgcsv_file}'>imagery-csv</a>"
            url = href_url(imagery_bucket_name, imgcsv_file, "imagery-csv")
            df_foryaml.loc[i, "gcp_link_imgcsv"] = url  # type: ignore


        ### Directories
        # Check for NetCDF files timeseries
        procl1_path = paths_glider["procl1dir"]
        if check_gcs_directory_exists(glider_bucket, procl1_path):
            url = href_url(glider_bucket_name, procl1_path, "glider")
            df_foryaml.loc[i, "gcp_dir_link_glider"] = url  # type: ignore

        # Check for acoustics
        if check_gcs_directory_exists(acoustics_bucket, path_pre):
            url = href_url(acoustics_bucket_name, path_pre, "acoustics")
            df_foryaml.loc[i, "gcp_dir_link_acoustics"] = url  # type: ignore

        # Check for imagery
        if check_gcs_directory_exists(imagery_bucket, path_pre):
            url = href_url(imagery_bucket_name, path_pre, "imagery")
            df_foryaml.loc[i, "gcp_dir_link_imagery"] = url  # type: ignore

    # Write to a yaml file
    yaml_data = df_foryaml.to_dict(orient="records")
    yaml_file = os.path.join(out_path, "esd-gliders.yml")
    _log.info(f"writing {yaml_file}")
    with open(yaml_file, "w") as file:
        yaml.dump(yaml_data, file, sort_keys=False, default_flow_style=False)

    # with open(yaml_file) as fin:
    #     z = yaml.safe_load(fin)
    # z_df = pd.DataFrame(z)

    return yaml_file


def make_deployment_table(engine):
    """
    Generate a deployment summary table, to publish on the Fleet Status page
    """

    ### Helper functions
    def _battery_type(components):
        """
        Given a Series of the components on a glider, report the type of batteries
        This function is intended to be used in an groupby('Glider_ID').agg() call
        """
        primary_count = components.str.contains("Lithium Metal battery").sum()
        rechargeable_count = components.str.contains("Rechargeable Battery").sum()
        if primary_count == 3 and rechargeable_count == 0:
            batt_str = "Primaries - Extended"
        elif primary_count == 2 and rechargeable_count == 0:
            batt_str = "Primaries"
        elif rechargeable_count == 3 and primary_count == 0:
            batt_str = "Rechargeables - Extended"
        elif rechargeable_count == 2 and primary_count == 0:
            batt_str = "Rechargeables"
        else:
            batt_str = "Unknown"
        return batt_str

    def _acoustics_type(components):
        """
        Given a Series of the components on a glider, report the type of acoustics
        This function is intended to be used in an groupby('Glider_ID').agg() call
        """
        azfp_count = components.str.contains(db_components["azfp"]).sum()
        nortek_count = components.str.contains(db_components["echosounder"]).sum()
        if azfp_count == 1 and nortek_count == 0:
            acoustics_str = "AZFP"
        elif nortek_count == 1 and azfp_count == 0:
            acoustics_str = "Nortek"
        elif (nortek_count + azfp_count) > 1:
            acoustics_str = "MULTIPLE"
        else:
            acoustics_str = "None"
        return acoustics_str

    def _camera_type(model):
        """
        Given a Series of the model on a glider, report the type of camera
        This function is intended to be used in an groupby('Glider_ID').agg() call
        """
        gc_count = model.str.contains(camera_models["glidercam"]).sum()
        sg_n_count = model.str.contains(camera_models["shadowgraph-narrow"]).sum()
        sg_w_count = model.str.contains(camera_models["shadowgraph-wide"]).sum()
        if gc_count == 1 and sg_n_count == 0 and sg_w_count == 0:
            camera_str = "Glidercam"
        elif gc_count == 0 and sg_n_count == 1 and sg_w_count == 0:
            camera_str = "Shadowgraph-narrow"
        elif gc_count == 0 and sg_n_count == 0 and sg_w_count == 1:
            camera_str = "Shadowgraph-wide"
        elif (gc_count + sg_n_count + sg_w_count) > 1:
            camera_str = "MULTIPLE"
        else:
            camera_str = "None"
        return camera_str

    def _pam_type(component):
        """
        Given a Series of the components on a glider, report the type of PAM sensor
        This function is intended to be used in an groupby('Glider_ID').agg() call
        """
        dmon_count = component.str.contains(db_components["dmon"]).sum()
        wispr_count = component.str.contains(db_components["wispr"]).sum()
        if dmon_count == 1 and wispr_count == 0:
            pam_str = "DMON"
        elif dmon_count == 0 and wispr_count == 1:
            pam_str = "WISPR"
        elif (dmon_count + wispr_count) > 1:
            pam_str = "MULTIPLE"
        else:
            pam_str = "None"
        return pam_str

    def _concatenate_sensors(row):
        """
        Given a Series of summarized devices table, concatenate the sensors
        into a single column.
        This function is intended to be used in an .apply call, after grouping

        """
        sensors = []
        for i in ["CTD", "Ecopuck", "Optode", "PAR"]:
            if row[i] == "Yes":
                sensors.append(i)
        for i in ["Acoustics", "Camera", "PAM"]:
            if row[i] != "None":
                sensors.append(row[i])
        return ", ".join(sensors)

    def _tf_type(val, component):
        """
        Returns Yes/No, depending on if a specific component is present
        val must be a key in db_components
        """
        return str(np.where(db_components[val] in component, "Yes", "No"))

    ### Main function code
    _log.info("Get info from the deployment view")
    vGlider_Deployment = pd.read_sql_table(
        "vGlider_Deployment",
        con=engine,
        schema="dbo",
    )

    columns_tokeep = {
        "Glider_Name": "Glider",
        "Deployment_Start": "Start",
        "Deployment_End": "End",
        "Deployment_Name": "Deployment_Name",
        "Deployment_Dives": "Dives",
        "Deployment_Days": "Days",
        "Project": "Project",
        "Software_Version": "OS_Version",
        "Glider_Deployment_ID": "Glider_Deployment_ID",
        "Glider_ID": "Glider_ID",
    }

    df_depl = (
        vGlider_Deployment[columns_tokeep.keys()]
        .rename(
            columns=columns_tokeep,
        )
        .copy()
    )

    # TODO: add location and notes to the database
    df_depl["Location"] = ""
    df_depl["Notes"] = ""
    df_depl["Start"] = df_depl["Start"].dt.strftime("%Y-%m-%d")
    df_depl["End"] = df_depl["End"].dt.strftime("%Y-%m-%d")
    df_depl["Dates"] = df_depl["Start"] + " - " + df_depl["End"]
    # df_depl["Dates"] = (
    #     df_depl["Start"].dt.strftime('%Y-%m-%d')
    #     + " - " + df_depl["End"].dt.strftime('%Y-%m-%d'))

    _log.info("Get and summarize device info, for each deployment")
    Deployment_Device = pd.read_sql_table(
        "vDeployment_Device",
        con=engine,
        schema="dbo",
    )

    device_summ = (
        Deployment_Device.groupby("Glider_Deployment_ID")
        .agg(
            Batteries=("Component", lambda x: _battery_type(x)),
            CTD=("Component", lambda x: _tf_type("ctd", x.values)),
            Ecopuck=("Component", lambda x: _tf_type("flbbcd", x.values)),
            Optode=("Component", lambda x: _tf_type("oxygen", x.values)),
            PAR=("Component", lambda x: _tf_type("par", x.values)),
            Acoustics=("Component", lambda x: _acoustics_type(x)),
            Camera=("Model", lambda x: _camera_type(x)),
            PAM=("Component", lambda x: _pam_type(x)),
        )
        .reset_index()
    )
    device_summ["Sensors"] = device_summ.apply(
        lambda row: _concatenate_sensors(row),
        axis=1,
    )

    column_order_pre = ["Glider", "Start", "End", "Dates", "Location"]
    column_order = (
        column_order_pre
        + list(device_summ.columns)[1:]
        + [col for col in df_depl.columns if col not in column_order_pre]
    )

    _log.info("Merging deployment and summarized device tables")
    out_table = pd.merge(df_depl, device_summ, on="Glider_Deployment_ID", how="left")
    out_table = out_table[column_order].sort_values(by=["Start", "Glider"])

    return out_table
