"""Preview or submit OSI DAG runs using shared deployment YAML settings.

Lists manifest JSON files and output objects in Cloud Storage. Preview is the
default and performs no submissions or writes. --submit explicitly enables
live requests. Output folders use manifest chunk numbers, not list positions.
Deterministic Airflow run IDs prevent identical submissions from being
silently repeated. Successful existing runs are reported separately from
unfinished or failed runs; output objects alone never establish success.
"""

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import PurePosixPath
import re
from typing import TYPE_CHECKING, TypedDict

from esdglider import imagery_cli, imagery_run_records

if TYPE_CHECKING:
    from google.cloud.storage.client import Client
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

    Example:
        A plan has this shape (the run ID and paths are illustrative)::

            RunPlan(
                dag_run_id="osi-<sha256 of conf>",
                conf={
                    "input_file": "gs://output-bucket/run/manifests/chunk01.json",
                    "model_type": "andy-6d-v1-detector-model",
                    "output_bucket": "output-bucket",
                    "output_folder": "run/outputs/01",
                    "yaml_config_path": "gs://output-bucket/run/config/pipeline.yaml",
                },
            )
    """

    dag_run_id: str
    conf: DagConf


def build_plans(
    names: list[str], config: imagery_cli.ImageryConfig,
    *,
    output_prefix: str | None = None,
) -> list[RunPlan]:
    """Map manifest names to stable numbered outputs and DAG run IDs.

    Args:
        names: Manifest object names returned by Cloud Storage.
        config: Validated effective configuration.
        output_prefix: Optional run-specific model output prefix.

    Returns:
        Plans sorted by numeric manifest chunk number.

    Raises:
        ValueError: If manifests are absent, misnamed, or have duplicate chunks.
    """
    if not config.model_type:
        raise ValueError("Choose a model explicitly before planning DAG runs.")
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
            "output_folder": (
                f"{output_prefix or config.output_prefix}/{chunk:02d}"
            ),
            "yaml_config_path": config.yaml_config_path,
        }
        digest = hashlib.sha256(
            json.dumps(conf, sort_keys=True).encode("utf-8")
        ).hexdigest()
        plans.append(RunPlan(f"osi-{digest}", conf))
    return plans


def prepare_run(
    client: "Client",
    config: imagery_cli.ImageryConfig,
    plans: list[RunPlan],
    context: dict,
    location: imagery_run_records.RunLocation,
) -> list[RunPlan]:
    """Archive inputs and save a create-only record before submitting jobs.

    Args:
        client: Authenticated GCS client.
        config: Effective local settings.
        plans: Selected chunk plans with run-specific output paths; see RunPlan
            for an example payload.
        context: Original user configuration, command options, and Git details,
            produced automatically by imagery_run_records.capture_context.
        location: New run location.

    Returns:
        Plans referring to the archived manifests and pipeline YAML.

    Raises:
        ValueError: If provenance does not match the effective settings.
        google.api_core.exceptions.GoogleAPICallError: If archiving fails.

    Example:
        A CLI-only context has this shape (CLI options are abbreviated).
        ``settings`` contains every resolved ImageryConfig field::

            {
                "settings": imagery_run_records.resolved_settings(config),
                "cli_options": {
                    "deployment_name": "calanus-20261007",
                    "model_type": "andy-6d-v1-detector-model",
                    "submit": True,
                    "test_limit": 1,
                },
                "operations": {
                    "submit": True, "test_limit": 1, "overwrite": False,
                },
                "source": None,
                "input_yaml_base64": None,
                "software": {"available": False, "reason": "git unavailable"},
            }

        With --config, ``input_yaml_base64`` holds the exact YAML bytes encoded
        as base64, and ``source`` holds its path, SHA-256 hash, and Git details.
        Callers normally use capture_context rather than build this by hand.
    """
    if context["settings"] != imagery_run_records.resolved_settings(config):
        raise ValueError("Run context does not match the effective configuration.")
    bucket = client.bucket(location.bucket_name)
    logging.info("Pipeline run ID: %s", location.run_id)
    logging.info("Run folder: gs://%s/%s/", bucket.name, location.prefix)
    snapshots = []
    encoded = context["input_yaml_base64"]
    if encoded is not None:
        contents = base64.b64decode(encoded, validate=True)
        if hashlib.sha256(contents).hexdigest() != context["source"]["sha256"]:
            raise ValueError("Input YAML hash does not match run context.")
        imagery_run_records.create_record(
            bucket, location, "input.yaml", contents,
            content_type="application/yaml",
        )
        _, input_snapshot = imagery_run_records.read_snapshot(
            client,
            f"gs://{bucket.name}/{location.config_object('input.yaml')}",
        )
        if input_snapshot["sha256"] != context["source"]["sha256"]:
            raise ValueError("Archived input YAML changed during recording.")
        snapshots.append(input_snapshot)
    pipeline_bytes, pipeline_source = imagery_run_records.read_snapshot(
        client, config.yaml_config_path,
    )
    pipeline_snapshot = imagery_run_records.archive_object(
        bucket, f"{location.prefix}/config/pipeline.yaml", pipeline_bytes,
    )
    snapshots.append(pipeline_snapshot)
    originals = [pipeline_source]
    archived_plans = []
    for plan in plans:
        contents, source = imagery_run_records.read_snapshot(
            client, plan.conf["input_file"],
        )
        snapshot = imagery_run_records.archive_object(
            bucket,
            f"{location.prefix}/manifests/"
            f"{PurePosixPath(plan.conf['input_file']).name}",
            contents,
        )
        originals.append(source)
        snapshots.append(snapshot)
        conf: DagConf = {
            **plan.conf,
            "input_file": snapshot["uri"],
            "yaml_config_path": pipeline_snapshot["uri"],
        }
        digest = hashlib.sha256(
            json.dumps(conf, sort_keys=True).encode("utf-8")
        ).hexdigest()
        archived_plans.append(RunPlan(f"osi-{digest}", conf))
    record = {
        "schema_version": 1,
        "run_id": location.run_id,
        "timestamp_utc": location.run_id[:16],
        "run_prefix": location.prefix,
        "settings": context["settings"],
        "cli_options": context["cli_options"],
        "operations": context["operations"],
        "source": context["source"],
        "software": context["software"],
        "paths": {
            **config.image_paths(),
            "output_prefix": location.output_prefix,
            "record_uri": (
                f"gs://{bucket.name}/{location.config_object('resolved.json')}"
            ),
        },
        "original_inputs": originals,
        "snapshots": snapshots,
        "plans": [asdict(plan) for plan in archived_plans],
        "remote_runtime_captured": False,
    }
    imagery_run_records.create_record(
        bucket, location, "resolved.json",
        json.dumps(record, indent=2, sort_keys=True).encode("utf-8"),
        content_type="application/json",
    )
    return archived_plans


def resume_run(
    client: "Client",
    config: imagery_cli.ImageryConfig,
    location: imagery_run_records.RunLocation,
    *,
    test_limit: int | None,
) -> list[RunPlan]:
    """Load an existing record and verify archived inputs before DAG-only resume.

    Args:
        client: Authenticated GCS client.
        config: Settings which must match the original run.
        location: Existing run location.
        test_limit: Original manifest selection limit.

    Returns:
        The exact recorded plans, preserving chunk IDs and paths.

    Raises:
        ValueError: If the record, settings, or archived inputs do not match.
        google.api_core.exceptions.GoogleAPICallError: If records are missing.
    """
    contents, _ = imagery_run_records.read_snapshot(
        client,
        f"gs://{location.bucket_name}/{location.config_object('resolved.json')}",
    )
    record = json.loads(contents)
    if (
        record.get("schema_version") != 1
        or record.get("run_id") != location.run_id
        or record.get("run_prefix") != location.prefix
        or record.get("settings") != imagery_run_records.resolved_settings(config)
        or record.get("operations", {}).get("test_limit") != test_limit
    ):
        raise ValueError("Resume settings or run record do not match.")
    snapshot_uris = {snapshot["uri"] for snapshot in record["snapshots"]}
    for uri in snapshot_uris:
        if not uri.startswith(
            f"gs://{location.bucket_name}/{location.prefix}/"
        ):
            raise ValueError("Recorded snapshot is outside the run folder.")
    imagery_run_records.verify_snapshots(client, record["snapshots"])
    plans = []
    for saved in record["plans"]:
        conf = saved["conf"]
        if (
            conf["output_bucket"] != location.bucket_name
            or not conf["output_folder"].startswith(location.output_prefix + "/")
            or not conf["input_file"].startswith(
                f"gs://{location.bucket_name}/{location.prefix}/manifests/"
            )
            or conf["input_file"] not in snapshot_uris
            or conf["yaml_config_path"] != (
                f"gs://{location.bucket_name}/{location.prefix}/config/pipeline.yaml"
            )
            or conf["yaml_config_path"] not in snapshot_uris
        ):
            raise ValueError("Recorded plan is outside the run folder.")
        digest = hashlib.sha256(
            json.dumps(conf, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if saved["dag_run_id"] != f"osi-{digest}":
            raise ValueError("Recorded DAG run ID does not match its payload.")
        plans.append(RunPlan(saved["dag_run_id"], conf))
    if not plans:
        raise ValueError("Run record contains no submission plans.")
    return plans


def submit_plans(
    plans: list[RunPlan],
    existing_outputs: set[str],
    config: imagery_cli.ImageryConfig,
    session: "requests.Session",
    *,
    resume: bool = False,
) -> None:
    """Preflight all runs, then submit only new, unambiguous work.

    Args:
        plans: Selected manifest submissions; see RunPlan for an example.
        existing_outputs: Bucket-relative output folders containing objects,
            e.g. {"run/outputs/01", "run/outputs/02"}, not gs:// URIs.
        config: Validated DAG settings.
        session: Authenticated HTTP session with refreshing credentials.
        resume: Skip already queued/running recorded runs when continuing.

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
            if state == "success" or (resume and state in {"running", "queued"}):
                logger.info(
                    "Existing run %s is %s; not resubmitting",
                    plan.dag_run_id, state,
                )
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
    location = imagery_run_records.RunLocation(
        config.output_bucket, config.deployment_name,
        args.resume_run_id or imagery_run_records.new_run_id(),
    )
    if args.resume_run_id:
        plans = resume_run(
            client, config, location, test_limit=args.test_limit,
        )
    else:
        names = [
            blob.name
            for blob in client.bucket(config.metadata_bucket).list_blobs(
                prefix=config.manifest_prefix,
            )
        ]
        plans = build_plans(names, config, output_prefix=location.output_prefix)
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
            "Previewed %d runs; no submissions or records written. "
            "New live submissions use a new run ID and archived input URIs.",
            len(plans),
        )
        if existing_outputs:
            logger.warning(
                "Existing output objects require inspection; they do not "
                "prove successful processing."
            )
        return
    if not args.resume_run_id:
        if args.run_context is not None:
            context = json.loads(args.run_context.read_text(encoding="utf-8"))
        else:
            context = imagery_run_records.capture_context(config, args)
        plans = prepare_run(client, config, plans, context, location)
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    with AuthorizedSession(credentials) as session:
        submit_plans(
            plans, existing_outputs, config, session,
            resume=args.resume_run_id is not None,
        )


if __name__ == "__main__":
    main()
