import logging

import sqlalchemy

from esdglider import defaults, metadata

logger = logging.getLogger(__name__) 

"""
Scrape info from database, and generate draft of
yaml deployment config file. This script will normally have to be run
from a local computer to access the database

'db/glider-db-prod.txt' is the database URL, used to create the
sqlalchemy engine. It should not be committed to GitHub.
"""

deployment_name = "amlr01-20181216"
# path_config = "C:/Users/sam.woodman/Downloads"
path_config = "C:/Users/sam.woodman/Documents/GitHub/glider-processing/deployment-configs/2018"

if __name__ == "__main__":
    logging.basicConfig(
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with open("db/glider-db-prod.txt", "r") as f:
        logger.info("Creating database connection")
        conn_string = f.read()
        engine = sqlalchemy.create_engine(conn_string)
        with engine.connect() as connection:
            metadata.make_deployment_yaml(
                con=connection,
                deployment_name=deployment_name,
                outdir=path_config,
                schema=defaults.DB_SCHEMA, 
            )
