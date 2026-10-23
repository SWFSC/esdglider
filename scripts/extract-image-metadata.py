# This script was written by Gemini, and adapted by Sam Woodman
 
import json
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from tqdm import tqdm

from esdglider import defaults, gcp, imagery, paths, utils

logger = logging.getLogger(__name__)


deployment_name = "chovy-20261005"
run_pipeline_bool = True

home = Path.home()
mnt_path = home / "mnt-gcs"

logs_bucket_name = "swfscesd-glider-logs" #defaults.BUCKET_LOGS
imagery_in_bucket_name = defaults.BUCKET_IMAGERY_IN
imagery_meta_bucket_name = defaults.BUCKET_IMAGERY_META

logs_path = home / "mnt-gcs" / logs_bucket_name
imagery_in_path = mnt_path / imagery_in_bucket_name
imagery_meta_path = mnt_path / imagery_meta_bucket_name

log_file_name = f"{deployment_name}-image-metadata-extraction.log"
# num_cores = os.cpu_count()  # Uses all available cores
# INPUT_DIR = Path("./images")
    

def run_pipeline(
    files: list[Path], 
    deployment_name: str, 
    depl_meta_file: Path, 
    img_meta_file: Path, 
    num_cores: int | None = None
):
    """
    Runs pipeline to generate the deployment-level and image-specific
    metadata files. 

    Parameters
    ----------
    files : list
        list of image file paths
    deployment_name : str
        name of the deployment
    depl_meta_file : str
        Path object of the deployment-level metadata file
    img_meta_file : str
        Path object of the image-level metadata file    
    num_cores : int | None
        Number of cores to use for extracing image datetime from metadata
        If None, uses all available cores (i.e., os.cpu_count())

    Returns
    -------
    Nothing
    """

    if num_cores is None:
        num_cores = os.cpu_count()

    if depl_meta_file.is_file():
        logger.error(f"depl_meta_file file ({depl_meta_file}) already exists")
        return  

    if img_meta_file.is_file():
        logger.error(f"img_meta_file file ({img_meta_file}) already exists")
        return

    # Generate Manifest (from first valid image)
    depl_metadata = imagery.extract_deployment_metadata(files[0], deployment_name)
    logger.info("Writing deployment-level metadata to %s", depl_meta_file)
    depl_meta_file.parent.mkdir(parents=True, exist_ok=True)
    with depl_meta_file.open("w") as f:
        json.dump(depl_metadata, f, indent=4)
    logger.info("Completed deployment-level metadata")

    # Generate Index via Multiprocessing
    logger.info("Extracting image-level metadata, and writing to %s", img_meta_file)
    img_meta_file.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Using %s cores", num_cores)
    with img_meta_file.open("a", encoding="utf-8") as f:  # noqa: SIM117
        with ProcessPoolExecutor(max_workers=num_cores) as executor:
            for result in tqdm(
                executor.map(imagery.extract_image_metadata, files), 
                total=len(files), 
                unit="images", 
            ):
                f.write(json.dumps(result) + "\n")
    logger.info("Completed image-level metadata")


if __name__ == "__main__":
    gcp.gcs_mount_bucket(logs_bucket_name, logs_path, ro=False)        
    logging.basicConfig(
        filename=logs_path / log_file_name,
        filemode="w",
        format="%(name)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    print(f"Writing logs to {logs_path / log_file_name}")
    logger.info(
        "Starting image metadata extraction for deployment %s, via script %s", 
        deployment_name,
        __file__
    )

    ### Mount bucket, and get paths
    gcp.gcs_mount_bucket(imagery_in_bucket_name, imagery_in_path, ro=True)
    gcp.gcs_mount_bucket(imagery_meta_bucket_name, imagery_meta_path, ro=False)

    img_paths = paths.get_path_imagery(
        deployment_name=deployment_name, 
        imagery_in_path=imagery_in_path, 
        imagery_meta_path=imagery_meta_path, 
        data_out_path="", 
    )

    ### Generate file list
    # Supports both cases and png extensions
    extensions = {'.jpg', '.jpeg', '.png'}
    
    # Gather all files that match the extension set
    logger.info("Getting all file paths")
    files = [
        p for p in Path(img_paths["imagedir"]).rglob('*') 
        if p.suffix.lower() in extensions
    ]
    if not files:
        logger.error("No files")
    else:
        logger.info("There are %s files", len(files))

        # Check method 1 - substring
        logger.info("Checking for any questionable paths via substring check")
        sub_check = 'checkpoint'
        file_check = [i for i in files if sub_check in str(i)]
        if file_check:
            logger.warning("The substring '%s' is in the following paths:", sub_check)
            for f in file_check:
                logger.warning("path: %s", f)

        # Check method 2 - length
        logger.info("Checking for any questionable paths via length check")
        utils.check_string_length([str(i.name) for i in files])

        # tmp_folder = home / "tmp-meta" / deployment_name
        # depl_meta_file = (tmp_folder / Path(img_paths["deplmetapath"]).name)
        # img_meta_file = (tmp_folder / Path(img_paths["imgmetapath"]).name)
        
        depl_meta_file = Path(img_paths["deplmetapath"])
        img_meta_file = Path(img_paths["imgmetapath"])

        if run_pipeline_bool:
            run_pipeline(
                files=files, 
                deployment_name=deployment_name, 
                depl_meta_file=depl_meta_file, 
                img_meta_file=img_meta_file, 
                num_cores=os.cpu_count(), 
            )
        logger.info("There are %s files", len(files))

        # print(f"gcloud storage mv {str(depl_meta_file)} gs://{img_paths["deplmetapath"]}")
        # print(f"gcloud storage mv {str(img_meta_file)} gs://{img_paths["imgmetapath"]}")
