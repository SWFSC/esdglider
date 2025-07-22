#!/bin/bash

gcloud storage rsync gs://amlr-gliders-deployments-dev/cache ~/esdglider/resources/example-data/cache

# Or, from a local computer:
# gcloud storage rsync C:\SMW\Gliders_Moorings\Gliders\Standard-glider-files\Cache gs://amlr-gliders-deployments-dev/cache
