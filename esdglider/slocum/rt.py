# functions for real-time processing in GCP

import logging
import os
import re
import subprocess

import esdglider.gcp as gcp
import esdglider.utils as utils

_log = logging.getLogger(__name__)


def scrape_sfmc(deployment_info, bucket, sfmc_path, gcpproject_id, secret_id):
    """
    rsync files from sfmc, and send them to correct bucket directories;
    Returns 0
    """

    deployment = deployment_info["deployment"]
    project = deployment_info["project"]
    _log.info(f"Scraping files from SFMC for deployment {deployment}")

    # --------------------------------------------
    # Checks
    deployment_split = deployment.split("-")
    if len(deployment_split[1]) != 8:
        _log.error(
            "The deployment must be the glider name followed by the deployment date",
        )
        raise ValueError("Unsuccessful deployment_split")
    else:
        glider = deployment_split[0]
        year = utils.year_path(project, deployment)

    # --------------------------------------------
    # Create sfmc directory structure, if needed
    _log.info(f"Making sfmc deployment dirs at {sfmc_path}")
    sfmc_local_path = os.path.join(sfmc_path, f"sfmc-{deployment}/")
    utils.mkdir_pass(sfmc_path)
    utils.mkdir_pass(sfmc_local_path)

    # sfmc_pwd_file = os.path.join(sfmc_local_path, ".sfmcpwd.txt")
    # _log.debug(f'SFMC ssh password written to {sfmc_pwd_file}')
    # if not os.path.isfile(sfmc_pwd_file):
    #     _log.info('Writing SFMC ssh pwd to file')
    #     file = open(sfmc_pwd_file, 'w+')
    #     file.write(gcp.access_secret_version(gcpproject_id, secret_id))
    #     file.close()
    #     os.chmod(sfmc_pwd_file, stat.S_IREAD)

    # --------------------------------------------
    # rsync with SFMC
    _log.info(f"Starting rsync with SFMC dockerver for {glider}")
    sfmc_glider = os.path.join(
        "/var/opt/sfmc-dockserver/stations/noaa/gliders",
        glider,
        "from-glider/",
    )
    sfmc_server_path = f"swoodman@sfmc.webbresearch.com:{sfmc_glider}"

    rsync_args = [
        "sshpass",
        "-p",
        gcp.access_secret_version(gcpproject_id, secret_id),
        "rsync",
        "-aP",
        "--delete",
        sfmc_server_path,
        sfmc_local_path,
    ]
    # NOTE: sshpass via file does not currently work. Unsure why
    # rsync_args = ['sshpass', '-f', sfmc_pwd_file,
    #               'rsync', "-aP", "--delete", sfmc_server_path, sfmc_local_path]
    # os.remove(sfmc_pwd_file) #delete sfmc_pwd_file
    # _log.debug(f'Removed SFMC ssh password file')

    _log.debug(rsync_args)
    retcode = subprocess.run(rsync_args, capture_output=True)
    _log.debug(retcode.args)

    if retcode.returncode != 0:
        _log.error("Error rsyncing with SFMC dockserver")
        _log.error(f"Args: {retcode.args}")
        _log.error(f"stderr: {retcode.stderr}")
        raise ValueError("Unsuccessful rsync with SFMC dockserver")
    else:
        _log.info(f"Successfully completed rsync with SFMC dockerver for {glider}")
        _log.debug(f"Args: {retcode.args}")
        _log.debug(f"stderr: {retcode.stdout}")

    # Check for unexpected file extensions
    sfmc_file_ext = utils.find_extensions(sfmc_local_path)
    file_ext_expected = {".cac", ".CAC", ".sbd", ".tbd", ".ad2"}
    #  ".ccc", ".scd", ".tcd", ".cam"
    file_ext_weird = sfmc_file_ext.difference(file_ext_expected)
    if len(file_ext_weird) > 0:
        # os.listdir(sfmc_local_path)
        logging.warning(
            f"File with the following extensions ({file_ext_weird}) "
            + "were downloaded from the SFMC, "
            + "but will not be organized copied to the GCP bucket",
        )
        # logging.warning(f'File list: TODO')

    # --------------------------------------------
    # Copy files to subfolders, and rsync with bucket
    logging.info("Starting file management on VM")
    bucket_deployment = f"gs://{bucket}/{project}/{year}/{deployment}"

    # cache files
    name_cac = "cac"
    utils.mkdir_pass(os.path.join(sfmc_local_path, name_cac))
    rt_file_mgmt(
        sfmc_file_ext,
        ".[Cc][Aa][Cc]",
        name_cac,
        sfmc_local_path,
        f"gs://{bucket}/cache",
        rsync_delete=False,
    )

    # sbd/tbd files
    name_stbd = "stbd"
    bucket_stbd = os.path.join(bucket_deployment, "data", "binary", "rt")
    utils.mkdir_pass(os.path.join(sfmc_local_path, name_stbd))
    rt_file_mgmt(sfmc_file_ext, ".[SsTt]bd", name_stbd, sfmc_local_path, bucket_stbd)

    # Do not bother with compressed files, because the SFMC uncompresses them

    # name_ccc  = 'ccc'
    # putils.mkdir_pass(os.path.join(sfmc_local_path, name_ccc))
    # rt_file_mgmt(sfmc_file_ext, '.ccc', name_ccc, sfmc_local_path,
    #              f'gs://{bucket}/cache-compressed')

    # # scd/tcd files
    # name_stcd = 'stcd'
    # bucket_stcd = os.path.join(bucket_deployment, 'data', 'binary', 'rt-compressed')
    # putils.mkdir_pass(os.path.join(sfmc_local_path, name_stcd))
    # rt_file_mgmt(
    #     sfmc_file_ext, '.[SsTt]cd', name_stcd, sfmc_local_path, bucket_stcd)

    # ad2 files
    name_ad2 = "ad2"
    bucket_ad2 = (
        f"gs://amlr-gliders-acoustics-dev/{project}/{year}/{deployment}/data/rt/"
    )
    utils.mkdir_pass(os.path.join(sfmc_local_path, name_ad2))
    rt_file_mgmt(sfmc_file_ext, ".ad2", name_ad2, sfmc_local_path, bucket_ad2)

    # # cam files TODO
    # name_cam  = 'cam'
    # putils.mkdir_pass(os.path.join(sfmc_local_path, name_cam))
    # rt_files_mgmt(sfmc_file_ext, '.cam', name_cam, sfmc_local_path, bucket_cam)

    # --------------------------------------------
    return 0


def rt_file_mgmt(
    sfmc_ext_all,
    ext_regex,
    subdir_name,
    local_path,
    bucket_path,
    rsync_delete=True,
):
    """
    Move real-time files from the local sfmc folder (local_path)
    to their subdirectory (subdir_path).
    Then uses gcloud to rsync to their place in the bucket (bucket_path)

    The rsync_delete flag indicates if the --delete-unmatched-destination-objects
    flag is used in the command

    ext_regex_path does include * for copying files (eg is '.[st]bd')
    """

    if any(re.search(ext_regex, i) for i in sfmc_ext_all):
        # Check paths
        if not os.path.isdir(local_path):
            _log.error(f"Necessary path ({local_path}) does not exist")
            raise FileNotFoundError(f"Could not find {local_path}")

        subdir_path = os.path.join(local_path, subdir_name)
        if not os.path.isdir(subdir_path):
            _log.error(f"Necessary path ({subdir_path}) does not exist")
            raise FileNotFoundError(f"Could not find {subdir_path}")

        # Move files so as to do rsync later
        _log.info(f"Moving {subdir_name} files to their local subdirectory")
        mv_cmd = f"mv {os.path.join(local_path, f'*{ext_regex}')} {subdir_path}"
        _log.debug(mv_cmd)
        retcode_tmp = subprocess.call(mv_cmd, shell=True)
        _log.debug(retcode_tmp)

        # Do rsync
        _log.info(f"Rsyncing {subdir_name} subdirectory with bucket directory")
        rsync_args = ["gcloud", "storage", "rsync", "-r"]
        if rsync_delete:
            rsync_args.append("--delete-unmatched-destination-objects")
        rsync_args.extend([subdir_path, bucket_path])

        _log.debug(rsync_args)
        retcode = subprocess.run(rsync_args, capture_output=True)

        if retcode.returncode != 0:
            _log.error(f"Error rsyncing {subdir_name} files to bucket")
            _log.error(f"Args: {retcode.args}")
            _log.error(f"stderr: {retcode.stderr}")
            raise ValueError("Unsuccessful rsync to bucket")
        else:
            _log.info(f"Rsynced {subdir_name} files to {bucket_path}")
            _log.debug(f"Args: {retcode.args}")
            _log.debug(f"stderr: {retcode.stdout}")
    else:
        _log.info(f"No {subdir_name} files to copy")

    return 0
