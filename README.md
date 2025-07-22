# glider-utils

Utility functions for ESD glider data processing.

This repo was inspired by [cproofutils](https://github.com/c-proof/cproofutils) and [votoutils](https://github.com/voto-ocean-knowledge/votoutils), as well as informed by experiences developing [amlr-gliders](https://github.com/us-amlr/amlr-gliders). Although this repo is a collection of utility functions and functionality, it roughly follows structure and opinions outlined by [Cookiecutter Data Science](https://cookiecutter-data-science.drivendata.org/).

For more detailed information about the Ecosystem Science Division's (ESD) glider data processing, see the ESD glider lab manual: https://swfsc.github.io/glider-lab-manual/

## esdglider Conda Environment

Create the esdglider conda environment, which contains all of the packages needed to 1) use the esdglider package and 2) additional glider-utils operations. To isntall the esdglider package in the conda environment, see below. From the directory above where this repo is cloned:

```bash
conda env create -f glider-utils/environment.yml
```

To update the environment after making any changes to the yml file:

```bash
conda env update -f glider-utils/environment.yml --prune
```

## esdglider Package

This glider-utils repo contains the esdglider Python toolbox, which contains functionality for processing glider data from the ESD. To install and use this package, the recommended process is to create the esdglider conda environment, and then install the esdglider toolbox as editable. From the directory above where this repo is cloned:

```bash
# Create as descriebd above, and activate the esdglider conda environment
conda activate esdglider

# Install esdglider package
pip install -e glider-utils
```

You can then use esdglider functions in your scripts. For instance:

```python
from esdglider.glider import binary_to_nc
```

For developers, the pyproject.toml and setup.py files specify for pip how to install the esdglider package. See [here](https://packaging.python.org/en/latest/tutorials/packaging-projects/) and [here](https://setuptools.pypa.io/en/latest/userguide/development_mode.html) for more info.

### esdglider in another conda environment

To install and use esdglider in another conda environment, you can either 1) activate the conda environment and install esdglider using pip as described above, or 2) add the following to your env yml file:

```yml
dependencies:
  ...
  - pip:
      - ...
      - git+https://github.com/swfsc/glider-utils.git@main #installs esdglider package from glider-utils repo
```

### Modules

* **acoustics**: functions for generating metadata for acoustic data processing
* **data**: folder for data included in the package
* **config**: functions for creating ESD metadata files, typically by interacting with the Glider & Mooring database
* **gcp**: functions specific to interacting with ESD's Google Cloud Platform (GCP) project
* **glider**: processing functions for slocum glider data, for instance generating slocum glider data paths or writing NetCDF files
* **imagery**: functions for generating metadata for image processing
* **rt**: functions for handling real-time glider data, including scraping data from the SFMC or doing real-time data file management
* **utils**: utility functions for glider data shenanigans

## Notebooks

This folder contains Jupyter notebooks, for instance for demonstrating sample processing or data access, or sanity-checking profile calculations.

## Resources

ESD glider utility references and resources. Most relevant are an example data folder, and two template shell scripts for setting up automated SFMC scraping or binary data processing in GCP. There are also some example scripts for common processes in the 'examples' folder.

## Scripts

Python scripts that are functionally wrappers around esdglider functions. These scripts use argparse, and are designed to be run via the command line on GCP virtual machines. Templates for shell scripts that run these Python scripts can be found in the resources folder. Some common aspects of these scripts incldue specifying the log level, and specifying a log file to which to write these logs.

This folder also contains individual 'test' scripts for reference, eg smw-test.py.

## Disclaimer

This repository is a scientific product and is not official communication of the National Oceanic and Atmospheric Administration, or the United States Department of Commerce. All NOAA GitHub project code is provided on an ‘as is’ basis and the user assumes responsibility for its use. Any claims against the Department of Commerce or Department of Commerce bureaus stemming from the use of this GitHub project will be governed by all applicable Federal law. Any reference to specific commercial products, processes, or services by service mark, trademark, manufacturer, or otherwise, does not constitute or imply their endorsement, recommendation or favoring by the Department of Commerce. The Department of Commerce seal and logo, or the seal and logo of a DOC bureau, shall not be used in any manner to imply endorsement of any commercial product or activity by DOC or the United States Government.
