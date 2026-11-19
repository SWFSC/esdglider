"""Names and create-only storage for imagery pipeline run records.

Cloud Storage create-only preconditions prevent accidental replacement by this
client. Bucket IAM and retention settings are still required for immutability.
"""

import argparse
import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from google.cloud.storage.bucket import Bucket
    from google.cloud.storage.client import Client
    from esdglider.imagery_cli import ImageryConfig


_RUN_ID_PATTERN = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{32}")


def new_run_id() -> str:
    """Return a UTC timestamp and random identifier for a new pipeline run.

    Returns:
        A run ID suitable for use in a Cloud Storage object prefix.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid4().hex}"


@dataclass(frozen=True)
class RunLocation:
    """Identify a pipeline run in an output bucket.

    Attributes:
        bucket_name: Output bucket name.
        deployment_name: Deployment identifier.
        run_id: Timestamped unique run identifier.
    """

    bucket_name: str
    deployment_name: str
    run_id: str

    def __post_init__(self) -> None:
        """Validate path components to avoid ambiguous or unsafe prefixes.

        Raises:
            ValueError: If a path component is invalid.
        """
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", self.bucket_name,
        ):
            raise ValueError("bucket_name must be a Cloud Storage bucket name.")
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_]*-\d{8}", self.deployment_name,
        ):
            raise ValueError("deployment_name must have the form glider-YYYYMMDD.")
        if not _RUN_ID_PATTERN.fullmatch(self.run_id):
            raise ValueError(
                "run_id must have the form YYYYMMDDTHHMMSSZ-<uuid hex>."
            )
        try:
            datetime.strptime(self.run_id[:16], "%Y%m%dT%H%M%SZ")
        except ValueError as exc:
            raise ValueError("run_id contains an invalid UTC timestamp.") from exc

    @property
    def prefix(self) -> str:
        """Return the run-specific Cloud Storage object prefix."""
        return f"SWFSC/glider/{self.deployment_name}/runs/{self.run_id}"

    @property
    def output_prefix(self) -> str:
        """Return the per-run prefix intended for model outputs."""
        return f"{self.prefix}/outputs"

    def config_object(self, filename: str) -> str:
        """Return the object name for a supported create-only config record.

        Args:
            filename: Either ``input.yaml`` or ``resolved.json``.

        Returns:
            A bucket-relative object name.

        Raises:
            ValueError: If the record filename is not supported.
        """
        if filename not in {"input.yaml", "resolved.json"}:
            raise ValueError(f"Unsupported run config record: {filename!r}")
        return f"{self.prefix}/config/{filename}"


def create_record(
    bucket: "Bucket",
    location: RunLocation,
    filename: str,
    contents: bytes,
    *,
    content_type: str,
) -> None:
    """Create one run record without overwriting an existing object.

    Args:
        bucket: Authenticated Cloud Storage bucket matching ``location``.
        location: Validated location for this run.
        filename: Supported config record filename.
        contents: Exact bytes to store.
        content_type: MIME type for the record.

    Raises:
        ValueError: If the bucket or filename does not match the location.
        google.api_core.exceptions.PreconditionFailed: If the object exists.
        google.api_core.exceptions.GoogleAPICallError: If the upload fails.
    """
    if bucket.name != location.bucket_name:
        raise ValueError("Bucket does not match the run location.")
    blob = bucket.blob(location.config_object(filename))
    blob.upload_from_string(
        contents,
        content_type=content_type,
        if_generation_match=0,
    )


def resolved_settings(config: "ImageryConfig") -> dict:
    """Serialize all effective workstation settings without credentials.

    Args:
        config: Validated ImageryConfig instance.

    Returns:
        JSON-compatible effective settings including the resolved mount path.
    """
    settings = asdict(config)
    settings["mount_root"] = str(config.mount_root)
    return settings


def git_details(path: Path) -> dict:
    """Capture local Git revision and dirty status without publishing to GitHub.

    Args:
        path: File or directory whose containing repository is inspected.

    Returns:
        Revision and repository-relative file information, or unavailable status.

    Raises:
        subprocess.CalledProcessError: If an established repository cannot
            be inspected.
    """
    directory = path if path.is_dir() else path.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        logging.warning("Git is unavailable; provenance has no Git revision.")
        return {"available": False, "reason": "git unavailable"}
    if result.returncode != 0:
        logging.warning("No Git repository found for %s", path)
        return {"available": False, "reason": result.stderr.strip()}
    root = Path(result.stdout.strip())
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {
        "available": True,
        "commit": revision,
        "dirty": bool(status),
        "path": str(path.relative_to(root)),
    }


def capture_context(
    config: "ImageryConfig", args: argparse.Namespace,
) -> dict:
    """Record the input YAML and user options for a pipeline or DAG command.

    Args:
        config: Validated effective configuration.
        args: Parsed command-line options.

    Returns:
        Serializable provenance; exact YAML bytes are base64 encoded.

    Raises:
        OSError: If the input YAML cannot be read.
        ValueError: If the YAML changed since validation.
    """
    from esdglider import imagery_cli

    source = None
    encoded = None
    if args.config is not None:
        path = args.config.expanduser().resolve()
        contents = path.read_bytes()
        if imagery_cli.load_config(args, yaml_contents=contents) != config:
            raise ValueError("Configuration changed since validation; rerun.")
        encoded = base64.b64encode(contents).decode("ascii")
        source = {
            "path": str(path),
            "sha256": hashlib.sha256(contents).hexdigest(),
            "git": git_details(path),
        }
    options = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in vars(args).items()
        if key not in {"require_model", "run_context"}
    }
    return {
        "settings": resolved_settings(config),
        "cli_options": options,
        "source": source,
        "input_yaml_base64": encoded,
        "software": git_details(Path(__file__).resolve()),
        "operations": {
            "submit": args.submit,
            "test_limit": args.test_limit,
            "overwrite": getattr(args, "overwrite", False),
        },
    }


def archive_object(bucket: "Bucket", name: str, contents: bytes) -> dict:
    """Create a run snapshot and return its integrity metadata.

    Args:
        bucket: Destination output bucket.
        name: Run-specific destination object name.
        contents: Exact bytes to archive.

    Returns:
        URI, generation, and SHA-256 hash of the uploaded snapshot.

    Raises:
        google.api_core.exceptions.GoogleAPICallError: If creation fails.
    """
    blob = bucket.blob(name)
    blob.upload_from_string(contents, if_generation_match=0)
    return {
        "uri": f"gs://{bucket.name}/{name}",
        "generation": int(blob.generation),
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def read_snapshot(client: "Client", uri: str) -> tuple[bytes, dict]:
    """Read one exact GCS object generation and record its content hash.

    Args:
        client: Authenticated Cloud Storage client.
        uri: Valid gs://bucket/object URI.

    Returns:
        Object bytes and URI, generation, and SHA-256 metadata.

    Raises:
        ValueError: If the URI is invalid.
        google.api_core.exceptions.GoogleAPICallError: If reading fails.
    """
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"Invalid snapshot URI: {uri}")
    blob = client.bucket(parsed.netloc).blob(parsed.path.lstrip("/"))
    blob.reload()
    generation = int(blob.generation)
    contents = blob.download_as_bytes(if_generation_match=generation)
    return contents, {
        "uri": uri,
        "generation": generation,
        "sha256": hashlib.sha256(contents).hexdigest(),
    }


def verify_snapshots(client: "Client", references: list[dict]) -> None:
    """Refuse resume if any archived input was replaced or modified.

    Args:
        client: Authenticated Cloud Storage client.
        references: Previously recorded snapshot integrity metadata.

    Raises:
        ValueError: If any snapshot's generation or hash no longer matches.
        google.api_core.exceptions.GoogleAPICallError: If a snapshot is missing.
    """
    for expected in references:
        _, actual = read_snapshot(client, expected["uri"])
        if actual != expected:
            raise ValueError(f"Archived input changed: {expected['uri']}")
