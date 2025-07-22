#!/bin/bash

# # In glider-proc VM:
# sudo touch /opt/glider-scripts/scrape-sfmc.sh
# sudo chmod 775 /opt/glider-scripts
# sudo chown -R gliders /opt/glider-scripts
# sudo chgrp -R gliders /opt/glider-scripts
# # Use nano, or other to paste in and update template code below

# Scrape SFMC for definied deployment/project, using esdglider conda env
DEPLOYMENT=calanus-20241019
PROJECT=ECOSWIM
/opt/conda-envs/esdglider/bin/python /opt/esdglider/scripts/scrape-sfmc.py $PROJECT $DEPLOYMENT --loglevel=info --logfile=/opt/glider-scripts/$DEPLOYMENT-scrape-sfmc.log
