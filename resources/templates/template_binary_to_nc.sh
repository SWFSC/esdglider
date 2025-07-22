#!/bin/bash

# NOTE: This template script is still in development

DEPLOYMENTS_PATH=/mnt/amlr-gliders-deployments-dev
IMAGERY_PATH=/mnt/amlr-gliders-imagery-raw-dev

# fusermount -u $DEPLOYMENTS_PATH
gcsfuse --implicit-dirs amlr-gliders-deployments-dev $DEPLOYMENTS_PATH
gcsfuse --implicit-dirs amlr-gliders-imagery-raw-dev $IMAGERY_PATH
echo -e "\nend of bucket mounting\n"

# Path on glider-proc VM
/opt/conda-envs/esdglider/bin/python esdglider/scripts/binary_to_nc.py SANDIEGO amlr08-20220513 delayed "$PATH_DEPLOYMENTS" --loglevel=info --write_timeseries --write_gridded
