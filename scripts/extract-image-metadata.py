import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from tqdm import tqdm
import logging
import os
from esdglider import gcp, imagery, paths, utils # type: ignore

deployment_name = "george-20240907"

home = Path.home()
mnt_path = home / "gcs-mnt"

imagery_in_bucket_name = "swfscesd-glider-imagery-data-in"
imagery_meta_bucket_name = "swfscesd-glider-imagery-metadata"

imagery_in_path = mnt_path / imagery_in_bucket_name
imagery_meta_path = mnt_path / imagery_meta_bucket_name

# num_cores = os.cpu_count()  # Uses all available cores
# INPUT_DIR = Path("./images")
    

def run_pipeline(files, deployment_name, depl_meta_file, img_meta_file, num_cores=None):
    """
    Runs pipeline to generate the deployment-level and image-specific
    metadata files. 

    This function was written by Gemini, and adapted by Sam Woodman

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

    # Generate Manifest (from first valid image)
    depl_metadata = imagery.extract_deployment_metadata(files[0], deployment_name)
    logging.info("Writing deployment-level metadata to %s", depl_meta_file)
    depl_meta_file.parent.mkdir(parents=True, exist_ok=True)
    with depl_meta_file.open("w") as f:
        json.dump(depl_metadata, f, indent=4)

    # Generate Index via Multiprocessing
    logging.info("Extracting file-level metadata, and writing to %s", img_meta_file)
    img_meta_file.parent.mkdir(parents=True, exist_ok=True)
    logging.info("Using %s cores", num_cores)
    with img_meta_file.open("a", encoding="utf-8") as f:
        with ProcessPoolExecutor(max_workers=num_cores) as executor:
            for result in tqdm(executor.map(imagery.extract_image_metadata, files), total=len(files)):
                f.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    logging.basicConfig(
        format="%(module)s:%(levelname)s:%(message)s",
        level=logging.INFO,
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
    
    # depl_file = img_paths / f"{deployment_name}-deployment-metadata.json"
    # index_file = imagery_meta_path / f"{deployment_name}-image-metadata.jsonl"
    # depl_meta_file = img_paths["deplmetapath"]
    # img_meta_file = img_paths["imgmetapath"]

    ### Generate file list
    # Supports both cases and png extensions
    extensions = {'.jpg', '.jpeg', '.png'}
    
    # Gather all files that match the extension set
    logging.info("Getting all file paths")
    files = [
        p for p in Path(img_paths["imagedir"]).rglob('*') 
        if p.suffix.lower() in extensions
    ]
    if not files:
        logging.error("No files")
    else:
        logging.info("There are %s files", len(files))

        # Check method 1 - substring
        logging.info("Checking for any questionable paths via substring check")
        sub_check = 'checkpoint'
        file_check = [i for i in files if sub_check in str(i)]
        if file_check:
            logging.warning(f"The substring '{sub_check}' is in the following paths:")
            for f in file_check:
                logging.warning("path: %s", f)

        # Check method 2 - length
        logging.info("Checking for any questionable paths via length check")
        utils.check_string_length([str(i.name) for i in files])

        run_pipeline(
            files=files, 
            deployment_name=deployment_name, 
            depl_meta_file=Path(img_paths["deplmetapath"]), 
            img_meta_file=Path(img_paths["imgmetapath"]), 
            num_cores=os.cpu_count()
        )
        