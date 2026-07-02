#!/usr/bin/env python

import argparse
import logging
import sys

import esdglider.slocum.rt as rt


def main(args):
    """
    rsync files from sfmc, and send them to correct bucket directories;
    Returns 0
    """
    if args.logfile == "":
        logging.basicConfig(
            format="%(asctime)s %(module)s:%(levelname)s:%(message)s [line %(lineno)d]",
            level=getattr(logging, args.loglevel.upper()),
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        logging.basicConfig(
            filename=args.logfile,
            filemode="a",
            format="%(asctime)s %(module)s:%(levelname)s:%(message)s [line %(lineno)d]",
            level=getattr(logging, args.loglevel.upper()),
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    # TODO: extract paths call, a la slocum.binary_to_nc
    deployment_info = {
        "project": args.project,
        "deployment": args.deployment,
    }

    rt.scrape_sfmc(
        deployment_info=deployment_info,
        bucket=args.bucket,
        sfmc_path=args.sfmc_path,
        gcpproject_id=args.gcpproject_id,
        secret_id=args.secret_id,
    )

    logging.info("Completed rt.scrape_sfmc")


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(
        description=main.__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    arg_parser.add_argument(
        "project",
        type=str,
        help="Glider project name",
        choices=["FREEBYRD", "REFOCUS", "SANDIEGO", "ECOSWIM"],
    )

    arg_parser.add_argument(
        "deployment",
        type=str,
        help="Deployment name, eg amlr03-20220425",
    )

    arg_parser.add_argument(
        "--sfmcpath",
        type=str,
        dest="sfmc_path",
        help="The SFMC directory on the local machine "
        + "where the files from the SFMC will be copied",
        default="/var/sfmc",
    )

    arg_parser.add_argument(
        "--gcpproject",
        type=str,
        dest="gcpproject_id",
        help="GCP project ID",
        default="ggn-nmfs-usamlr-dev-7b99",
    )

    arg_parser.add_argument(
        "--gcpbucket",
        type=str,
        dest="bucket",
        help="GCP glider deployments bucket name",
        default="amlr-gliders-deployments-dev",
    )

    arg_parser.add_argument(
        "--gcpsecret",
        type=str,
        dest="secret_id",
        help="GCP secret ID that contains the SFMC password for the rsync",
        default="sfmc-swoodman",
    )

    arg_parser.add_argument(
        "-l",
        "--loglevel",
        type=str,
        help="Verbosity level",
        choices=["debug", "info", "warning", "error", "critical"],
        default="info",
    )

    arg_parser.add_argument(
        "--logfile",
        type=str,
        help="File to which to write logs",
        default="",
    )

    parsed_args = arg_parser.parse_args()

    sys.exit(main(parsed_args))
