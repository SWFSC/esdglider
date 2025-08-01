import logging

import sqlalchemy

import esdglider.config as config

"""
Scrape info from database, and generate draft of
yaml deployment config file. This script will normally have to be run
from a local computer to access the database

'db/glider-db-prod.txt' is the database URL, used to create the
sqlalchemy engine. It should not be committed to GitHub.
"""

# For testing
# path_config = "C:/Users/sam.woodman/Downloads"

path_config = "C:/SMW/Gliders_Moorings/Gliders/glider-lab/deployment-configs"

if __name__ == "__main__":
    logging.basicConfig(
        format="%(module)s:%(asctime)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with open("db/glider-db-prod.txt", "r") as f:
        conn_string = f.read()
    engine = sqlalchemy.create_engine(conn_string)

    config.make_deployment_yaml(
        engine=engine,
        deployment_name="amlr04-20231128",
        out_path=path_config,
    )
