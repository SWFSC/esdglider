#!/usr/bin/env python

import argparse
import logging
import sys
import os

import esdglider.glider as glider


def main(args):
    """
    Process binary ESD glider data...
    """
    logging.basicConfig(
        format="%(module)s:%(levelname)s:%(message)s [line %(lineno)d]",
        level=getattr(logging, args.loglevel.upper()),
    )

    deployment_info = {
        "deployment_name": args.deployment_name,
        "mode": args.mode,
        "deploymentyaml": os.path.join(
            args.config_path, f"{args.deployment_name}.yml"
        )
    }
    paths = glider.get_path_glider(deployment_info, args.deployments_path)

    file_info = f"https://github.com/SWFSC/glider-lab: {os.path.basename(__file__)}"

    glider.binary_to_nc(
        deployment_info=deployment_info,
        paths=paths,
        write_raw=args.write_timeseries,
        write_timeseries=args.write_timeseries,
        write_gridded=args.write_gridded,
        file_info=file_info,
    )


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(
        description=main.__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        allow_abbrev=False,
    )

    arg_parser.add_argument(
        "project",
        type=str,
        help="Project name for deployment",
        choices=["FREEBYRD", "REFOCUS", "SANDIEGO", "ECOSWIM"],
    )

    arg_parser.add_argument(
        "deployment_name",
        type=str,
        help="Deployment name, eg amlr03-20220425",
    )

    arg_parser.add_argument(
        "mode",
        type=str,
        help="Specify which binary files will be converted to dbas. "
        + "'delayed' means [de]bd files will be converted, "
        + "and 'rt' means [st]bd files will be converted",
        choices=["delayed", "rt"],
    )

    arg_parser.add_argument(
        "deployments_path",
        type=str,
        help="Path to glider deployments directory. "
        + "In GCP, this will be the mounted bucket path",
    )

    arg_parser.add_argument(
        "config_path",
        type=str,
        help="Path to folder with the config (deployment yaml) file. "
        + "Usually in a clone of https://github.com/SWFSC/glider-lab",
    )

    arg_parser.add_argument(
        "--min_dt",
        type=str,
        default="2017-01-01",
        help="The minimum datetime kept during deployment processing. All "
        + "rows with timestamps less than this value will be dropped. "
        + "String must be readable by numpy.datetime64()",
    )

    arg_parser.add_argument(
        "--write_timeseries",
        help="flag; indicates if timeseries nc files should be written",
        action="store_true",
    )

    arg_parser.add_argument(
        "--write_gridded",
        help="flag; indicates if gridded nc files should be written",
        action="store_true",
    )

    arg_parser.add_argument(
        "-l",
        "--loglevel",
        type=str,
        help="Verbosity level",
        choices=["debug", "info", "warning", "error", "critical"],
        default="info",
    )

    # arg_parser.add_argument('--logfile',
    #     type=str,
    #     help='File to which to write logs',
    #     default='')

    parsed_args = arg_parser.parse_args()

    sys.exit(main(parsed_args))
