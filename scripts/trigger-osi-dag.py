"""Preview or submit OSI DAG runs using shared deployment YAML settings.

Lists manifest JSON files and output objects in Cloud Storage. Preview is the
default and performs no submissions or writes. --submit explicitly enables
live requests. Output folders use manifest chunk numbers, not list positions.
Deterministic Airflow run IDs prevent identical submissions from being
silently repeated. Successful existing runs are reported separately from
unfinished or failed runs; output objects alone never establish success.
"""

from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING, TypedDict

from esdglider import imagery_cli

if TYPE_CHECKING:
    import requests

logger = logging.getLogger(__name__)


class DagConf(TypedDict):
    """Container parameters accepted by the existing optics-pipeline DAG."""

    input_file: str
    model_type: str
    output_bucket: str
    output_folder: str
    yaml_config_path: str


@dataclass(frozen=True)
class RunPlan:
    """A stable Airflow run ID and its unchanged model parameter payload.

    Attributes:
        dag_run_id: Deterministic ID derived from the complete configuration.
        conf: Parameters passed to the remote DAG.
    """

    dag_run_id: str
    conf: DagConf


def build_plans(
    names: list[str], config: imagery_cli.ImageryConfig,
) -> list[RunPlan]:
    """Map manifest names to stable numbered outputs and DAG run IDs.

    Args:
        names: Manifest object names returned by Cloud Storage.
        config: Validated effective configuration.

    Returns:
        Plans sorted by numeric manifest chunk number.

    Raises:
        ValueError: If manifests are absent, misnamed, or have duplicate chunks.
    """
    pattern = re.compile(
        re.escape(config.deployment_name) + r"-image-manifest-chunk(\d+)\.json"
    )
    chunks = {}
    for name in names:
        if not name.endswith(".json"):
            continue
        match = pattern.fullmatch(PurePosixPath(name).name)
        if (
            not match or not name.startswith(config.manifest_prefix)
            or "/" in name[len(config.manifest_prefix):]
        ):
            raise ValueError(f"Unexpected manifest name: {name}")
        chunk = int(match[1])
        if chunk < 1 or chunk in chunks:
            raise ValueError(f"Invalid or duplicate manifest chunk: {name}")
        chunks[chunk] = name
    if not chunks:
        raise ValueError(f"No manifests found under {config.manifest_prefix}")
    plans = []
    for chunk, name in sorted(chunks.items()):
        conf: DagConf = {
            "input_file": f"gs://{config.metadata_bucket}/{name}",
            "model_type": config.model_type,
            "output_bucket": config.output_bucket,
            "output_folder": f"{config.output_prefix}/{chunk:02d}",
            "yaml_config_path": config.yaml_config_path,
        }
        digest = hashlib.sha256(
            json.dumps(conf, sort_keys=True).encode("utf-8")
        ).hexdigest()
        plans.append(RunPlan(f"osi-{digest}", conf))
    return plans


def submit_plans(
    plans: list[RunPlan],
    existing_outputs: set[str],
    config: imagery_cli.ImageryConfig,
    session: "requests.Session",
) -> None:
    """Preflight all runs, then submit only new, unambiguous work.

    Args:
        plans: Selected manifest submissions.
        existing_outputs: Output folders already containing objects.
        config: Validated DAG settings.
        session: Authenticated HTTP session with refreshing credentials.

    Raises:
        RuntimeError: If an existing run is unfinished or failed.
        FileExistsError: If outputs exist without a matching successful run.
        requests.exceptions.RequestException: If an API request fails.
    """
    url = (
        f"{config.composer_url.rstrip('/')}/api/v1/dags/"
        f"{config.dag_id}/dagRuns"
    )
    pending = []
    for plan in plans:
        response = session.get(f"{url}/{plan.dag_run_id}", timeout=60)
        if response.status_code == 200:
            state = response.json().get("state")
            if state == "success":
                logger.info("Already successful: %s", plan.conf["input_file"])
                continue
            raise RuntimeError(
                f"Run {plan.dag_run_id} already exists with state {state!r}; "
                "inspect/recover it in Airflow rather than resubmitting."
            )
        if response.status_code != 404:
            response.raise_for_status()
            raise RuntimeError(
                f"Unexpected Airflow response: {response.status_code}"
            )
        if plan.conf["output_folder"] in existing_outputs:
            raise FileExistsError(
                f"Output objects exist at {plan.conf['output_folder']}; "
                "inspect their run history before submitting."
            )
        pending.append(plan)
    for plan in pending:
        response = session.post(
            url, json={"dag_run_id": plan.dag_run_id, "conf": plan.conf},
            timeout=60,
        )
        response.raise_for_status()
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Unexpected Airflow response: {response.status_code}"
            )
        logger.info(
            "Submitted %s as %s (not yet completed)",
            plan.conf["input_file"], plan.dag_run_id,
        )


def main(argv: list[str] | None = None) -> None:
    """Preview selected manifests or explicitly submit live DAG runs.

    Args:
        argv: Optional arguments; defaults to the process command line.

    Raises:
        ValueError: If settings or manifest names are invalid.
        OSError: If configuration cannot be read.
        RuntimeError: If a run is blocked or submission fails.
    """
    parser = imagery_cli.create_parser("dag", __doc__)
    args = parser.parse_args(argv)
    config = imagery_cli.load_config(args)
    imagery_cli.configure_logging()
    from google.cloud import storage

    client = storage.Client()
    names = [
        blob.name for blob in client.bucket(config.metadata_bucket).list_blobs(
            prefix=config.manifest_prefix,
        )
    ]
    plans = build_plans(names, config)
    if args.test_limit is not None:
        plans = plans[:args.test_limit]
    output_bucket = client.bucket(config.output_bucket)
    existing_outputs = {
        plan.conf["output_folder"] for plan in plans
        if next(iter(output_bucket.list_blobs(
            prefix=plan.conf["output_folder"] + "/", max_results=1,
        )), None) is not None
    }
    if not args.submit:
        for plan in plans:
            print(json.dumps({
                "dag_run_id": plan.dag_run_id,
                "conf": plan.conf,
                "existing_output_objects": (
                    plan.conf["output_folder"] in existing_outputs
                ),
            }))
        logger.info(
            "Previewed %d runs; no submissions. Airflow states not checked.",
            len(plans),
        )
        if existing_outputs:
            logger.warning(
                "Existing output objects require inspection; they do not "
                "prove successful processing."
            )
        return
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    with AuthorizedSession(credentials) as session:
        submit_plans(plans, existing_outputs, config, session)


if __name__ == "__main__":
    main()
