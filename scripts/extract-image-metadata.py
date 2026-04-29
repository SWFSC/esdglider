from PIL import Image
from PIL.ExifTags import TAGS
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from tqdm import tqdm
import logging
import os
from esdglider import gcp, paths, utils # type: ignore

deployment_name = "amlr07-20221204"

home = Path.home()
mnt_path = home / "gcs-mnt"

imagery_in_bucket_name = "swfscesd-glider-imagery-data-in"
imagery_in_path = mnt_path / imagery_in_bucket_name

num_cores = os.cpu_count()  # Uses all available cores
# INPUT_DIR = Path("./images")
global_file = home / f"{deployment_name}-deployment-metadata.json"
index_file = home / f"{deployment_name}-index-metadata.jsonl"

# def extract_metadata(file):
#     logging.info(f"{file.name}---------------------")
#     try:
#         # open the image
#         image = Image.open(file)
#         metadata = {
#             "Filename": file.name,
#             "Directory": file.parent.name, 
#             # "Size": image.size,
#             "Height": image.height,
#             "Width": image.width,
#             "Format": image.format,
#             "Mode": image.mode,
#         }
#         logging.debug(f"image params: {metadata}")

#         # extracting the exif metadata
#         exifdata = image.getexif()
#         logging.debug("Starting exifdata")

#         # looping through all the tags present in exifdata
#         for tagid in exifdata:        
#             # Get the tag name
#             tagname = TAGS.get(tagid, tagid)
#             if tagname == "ExifOffset":
#                 continue

#             # Get the value, and format it
#             value = exifdata.get(tagid)
#             logging.debug(f"{tagname}: {value}")
#             if isinstance(value, bytes):
#                 value = value.decode(errors='ignore').strip('\x00')
#             elif hasattr(value, 'numerator'):
#                 value = float(value) # type: ignore

#             metadata[str(tagname)] = value

#     except Exception as e:
#         metadata = {"file": file, "error": str(e)}
    
#     return metadata


# def run_pipeline(image_dir, file_path):
#     # Supports both cases and png extensions
#     extensions = {'.jpg', '.jpeg', '.png'}
    
#     # Gather all files that match the extension set
#     files = [
#         p for p in image_dir.rglob('*') 
#         if p.suffix.lower() in extensions
#     ]
    
#     if not files:
#         return

#     # Process and write results
#     file_path.touch(exist_ok=True)
#     with file_path.open(mode="a", encoding="utf-8") as f:
#         with ProcessPoolExecutor(max_workers=num_cores) as executor:
#             # tqdm is kept as a progress bar; if you want 0 output, remove the tqdm wrapper
#             for result in tqdm(executor.map(extract_metadata, files), total=len(files), disable=False):
#                 f.write(json.dumps(result) + "\n")

def get_global_metadata(file):
    """Extracts deployment-wide tags from a single sample image."""
    logging.info(f"Extracting deployment-level metadata from {file}")

    try:
        with Image.open(file) as img:            
            global_metadata = {
                "Deployment": deployment_name,
                "Height": img.height,
                "Width": img.width,
                "Format": img.format,
                "Mode": img.mode,
            }

            exifdata = img.getexif()
            for tagid in exifdata:        
                # Get the tag name
                tagname = TAGS.get(tagid, tagid)

                # Get the value, and format it
                value = exifdata.get(tagid)
                logging.debug(f"{tagname}: {value}")
                if isinstance(value, bytes):
                    value = value.decode(errors='ignore').strip('\x00')
                elif hasattr(value, 'numerator'):
                    value = float(value) # type: ignore

                global_metadata[str(tagname)] = value

    except Exception as e:
        global_metadata = {"file": file, "error": str(e)}    

    return global_metadata


def extract_image_record(image_path):
    """Worker function for per-image high-frequency data."""
    try:
        with Image.open(image_path) as img:
            raw_exif = img.getexif()
            
            # Extract just the datetime
            # Tag 36867 is DateTimeOriginal
            dt = raw_exif.get(36867) or raw_exif.get(306) # Fallback to DateTime
            
            if hasattr(dt, 'decode'): dt = dt.decode() # type: ignore
            dt_str = str(dt).strip('\x00') if dt else "UNKNOWN"

            return {
                "n": image_path.name,            # Filename
                "p": image_path.parent.name,      # Immediate parent directory
                "dt": dt_str                     # DateTime
            }
        
    except Exception:
        return {"n": image_path.name, "error": "failed"}
    

def run_pipeline(files, manifest_file, index_file):
    # Generate Manifest (from first valid image)
    global_metadata = get_global_metadata(files[0])
    with manifest_file.open("w") as f:
        json.dump(global_metadata, f, indent=4)

    # Generate Index via Multiprocessing
    with index_file.open("a", encoding="utf-8") as f:
        with ProcessPoolExecutor(max_workers=num_cores) as executor:
            for result in tqdm(executor.map(extract_image_record, files), total=len(files)):
                f.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    logging.basicConfig(
        format="%(module)s:%(levelname)s:%(message)s",
        level=logging.INFO,
    )

    ### Mount bucket, and get paths
    gcp.gcs_mount_bucket(imagery_in_bucket_name, imagery_in_path, ro=True)

    img_paths = paths.get_path_imagery(
        deployment_name = deployment_name, 
        imagery_in_path = imagery_in_path, 
        data_out_path = "", 
    )

    ### Generate file lise
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

        print("done")

        # run_pipeline(files, global_file, index_file)
        