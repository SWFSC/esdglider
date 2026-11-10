"""Shared configuration and file handling for the three imagery commands.

CLI values override YAML values, which override package defaults. Loading and
validation perform no cloud operations. Deployment names are always explicit.
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import logging
from pathlib import Path
import re
import shutil
from urllib.parse import urlparse

from esdglider import defaults


@dataclass(frozen=True)
class ImageryConfig:
    """Validated settings shared by metadata, manifest, and DAG commands.

    Attributes:
        deployment_name: Deployment identifier ending in a YYYYMMDD date.
        directory_chunk_size: Maximum image directories per manifest.
        mount_root: Local directory containing bucket mountpoints.
        num_cores: Metadata worker count, or None for the executor default.
        directory_filter: Optional image directory subset.
        imagery_bucket: Bucket containing source images.
        metadata_bucket: Bucket containing metadata and manifests.
        composer_url: HTTPS Airflow webserver URL.
        dag_id: Airflow DAG identifier.
        model_type: Explicit inference model identifier; empty when unselected.
        yaml_config_path: Container pipeline YAML URI.
        output_bucket: Bucket for model results.
    """

    deployment_name: str
    directory_chunk_size: int = 50
    mount_root: Path = field(default_factory=lambda: Path.home() / "mnt-gcs")
    num_cores: int | None = None
    directory_filter: list[str] | None = None
    imagery_bucket: str = defaults.BUCKET_IMAGERY_IN
    metadata_bucket: str = defaults.BUCKET_IMAGERY_META
    composer_url: str = (
        "https://14668048b1ac46eba7798ad8ed19d75a-"
        "dot-us-central1.composer.googleusercontent.com"
    )
    dag_id: str = "optics-pipeline"
    model_type: str = ""
    yaml_config_path: str = (
        "gs://ggn-nmfs-osi-dev-1-data/"
        "SWFSC/sam/viame-custom-pipeline-viamecsv-uploadno.yaml"
    )
    output_bucket: str = "ggn-nmfs-osi-dev-1-data"

    @property
    def year(self) -> str:
        """Return the deployment year as a four-digit string."""
        return self.deployment_name.rsplit("-", 1)[1][:4]

    @property
    def manifest_prefix(self) -> str:
        """Return the deployment's manifest object prefix, including slash."""
        return (
            f"{self.year}/{self.deployment_name}/"
            f"osi-manifests-{self.directory_chunk_size:03d}dirs/"
        )

    @property
    def output_prefix(self) -> str:
        """Return the deployment's model-output object prefix."""
        return (
            f"SWFSC/glider/{self.deployment_name}/"
            f"{self.directory_chunk_size:03d}dirs"
        )

    def image_paths(self) -> dict:
        """Return imagery paths using the existing package path helper."""
        from esdglider import paths

        return paths.get_path_imagery(
            self.deployment_name,
            imagery_in_path=self.mount_root / self.imagery_bucket,
            imagery_meta_path=self.mount_root / self.metadata_bucket,
            data_out_path=Path.home() / "mnt-gcs" / defaults.BUCKET_DATA_OUT,
        )


def create_parser(stage: str, description: str) -> argparse.ArgumentParser:
    """Build a stage-specific parser with shared configuration overrides.

    Args:
        stage: One of metadata, manifests, dag, or pipeline.
        description: Help text describing the command.

    Returns:
        An argument parser; unset configuration overrides are omitted.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.set_defaults(require_model=stage in ("dag", "pipeline"))
    parser.add_argument("--config", type=Path, help="Shared YAML config file.")
    for name in (
        "deployment-name", "imagery-bucket", "metadata-bucket", "mount-root",
    ):
        parser.add_argument(
            f"--{name}", default=argparse.SUPPRESS,
            help=f"Override {name.replace('-', ' ')} from config.",
        )
    parser.add_argument(
        "--directory-chunk-size", type=int, default=argparse.SUPPRESS,
        help="Directories per manifest (default: 50).",
    )
    if stage in ("metadata", "pipeline"):
        parser.add_argument(
            "--num-cores", type=int, default=argparse.SUPPRESS,
            help="Positive number of metadata extraction workers.",
        )
    if stage in ("manifests", "pipeline"):
        parser.add_argument(
            "--directory-filter", nargs="+", default=argparse.SUPPRESS,
            help="Process only these image directory names.",
        )
        if stage == "manifests":
            parser.add_argument(
                "--output-directory", type=Path,
                help="Write manifests locally instead of the metadata bucket.",
            )
    if stage in ("dag", "pipeline"):
        for name in (
            "composer-url", "dag-id", "model-type", "yaml-config-path",
            "output-bucket",
        ):
            parser.add_argument(
                f"--{name}", default=argparse.SUPPRESS,
                help=(
                    "Model identifier; required in config or on the CLI."
                    if name == "model-type" else
                    f"Override DAG {name.replace('-', ' ')}."
                ),
            )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run", action="store_true",
            help=(
                "Print paths only; no cloud access or writes."
                if stage == "pipeline" else
                "Preview only (default); lists cloud objects, no submissions."
            ),
        )
        mode.add_argument(
            "--submit", action="store_true",
            help="Explicitly submit live DAG runs; may incur cloud costs.",
        )
        parser.add_argument(
            "--test-limit", type=int,
            help="Limit to this positive number of manifests.",
        )
        if stage == "pipeline":
            parser.add_argument(
                "--overwrite", action="store_true",
                help="Replace existing metadata/manifests after inspection.",
            )
    else:
        parser.add_argument(
            "--overwrite", action="store_true",
            help="Replace existing outputs; otherwise refuse to write.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print derived paths only; no bucket mounts or writes.",
        )
    return parser


def load_config(args: argparse.Namespace) -> ImageryConfig:
    """Load, merge, and validate settings before any cloud operations.

    Args:
        args: Parsed stage arguments.

    Returns:
        Validated effective configuration.

    Raises:
        OSError: If the configuration cannot be read.
        ValueError: If YAML, settings, or deployment date are invalid.
    """
    values = {}
    if args.config is not None:
        import yaml

        class ConfigLoader(yaml.SafeLoader):
            """Load safe YAML mappings without duplicate or nonstring keys."""

            def construct_mapping(self, node, deep=False):
                """Return a mapping, rejecting ambiguous configuration keys.

                Args:
                    node: YAML mapping node.
                    deep: Whether to construct nested objects immediately.

                Returns:
                    The constructed mapping.

                Raises:
                    ValueError: If keys are duplicated or are not strings.
                """
                mapping = {}
                for key_node, value_node in node.value:
                    key = self.construct_object(key_node, deep=deep)
                    if not isinstance(key, str) or key in mapping:
                        raise ValueError(
                            "Configuration keys must be unique strings: "
                            f"{key!r}"
                        )
                    mapping[key] = self.construct_object(value_node, deep=deep)
                return mapping

        try:
            with args.config.expanduser().open(encoding="utf-8") as stream:
                document = yaml.load(stream, Loader=ConfigLoader)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError("Configuration must be a YAML mapping.")
        values = dict(document)
    dag = values.pop("dag", {})
    dag_keys = {
        "composer_url", "dag_id", "model_type", "yaml_config_path",
        "output_bucket",
    }
    root_keys = set(ImageryConfig.__dataclass_fields__) - dag_keys
    if not isinstance(dag, dict):
        raise ValueError("'dag' must be a mapping.")
    if values.keys() - root_keys or dag.keys() - dag_keys:
        unknown = (values.keys() - root_keys) | (dag.keys() - dag_keys)
        raise ValueError(
            f"Unknown configuration keys: {sorted(map(str, unknown))}"
        )
    values.update(dag)
    values.update({
        key: value for key, value in vars(args).items()
        if key in ImageryConfig.__dataclass_fields__
    })
    deployment = values.get("deployment_name")
    if not isinstance(deployment, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_]*-\d{8}", deployment,
    ):
        raise ValueError("deployment_name must have the form glider-YYYYMMDD.")
    try:
        datetime.strptime(deployment.rsplit("-", 1)[1], "%Y%m%d")
    except ValueError as exc:
        raise ValueError("deployment_name contains an invalid date.") from exc
    config = ImageryConfig(**values)
    if args.require_model and "model_type" not in values:
        raise ValueError(
            "Choose a model explicitly using dag.model_type in the YAML "
            "configuration or --model-type."
        )
    for key in ("directory_chunk_size", "num_cores"):
        value = getattr(config, key)
        if key == "num_cores" and value is None:
            continue
        if type(value) is not int or value < 1:
            raise ValueError(f"{key} must be a positive integer.")
    test_limit = getattr(args, "test_limit", None)
    if test_limit is not None and test_limit < 1:
        raise ValueError("test_limit must be a positive integer.")
    for key in (
        "imagery_bucket", "metadata_bucket", "output_bucket", "dag_id",
        "composer_url", "yaml_config_path",
    ):
        value = getattr(config, key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a nonempty string.")
    for key in ("imagery_bucket", "metadata_bucket", "output_bucket"):
        if not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", getattr(config, key),
        ):
            raise ValueError(f"{key} must be a bucket name, not a URI or path.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", config.dag_id):
        raise ValueError("dag_id contains invalid characters.")
    if "model_type" in values and (
        not isinstance(config.model_type, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", config.model_type)
    ):
        raise ValueError("model_type must be a nonempty model identifier.")
    url = urlparse(config.composer_url)
    if (
        url.scheme != "https" or not url.netloc or url.username
        or url.password or url.query or url.fragment
    ):
        raise ValueError(
            "composer_url must be an HTTPS URL without credentials."
        )
    uri = urlparse(config.yaml_config_path)
    if (
        uri.scheme != "gs" or not uri.netloc or uri.path in ("", "/")
        or uri.query or uri.fragment
    ):
        raise ValueError("yaml_config_path must be a gs://bucket/object URI.")
    if config.directory_filter is not None and (
        not isinstance(config.directory_filter, list)
        or not config.directory_filter
        or any(
            not isinstance(item, str) or not item
            for item in config.directory_filter
        )
    ):
        raise ValueError(
            "directory_filter must be null or a nonempty list of strings."
        )
    if (
        not isinstance(config.mount_root, (str, Path))
        or not str(config.mount_root)
    ):
        raise ValueError("mount_root must be a nonempty path.")
    return ImageryConfig(**{
        **values, "mount_root": Path(config.mount_root).expanduser().resolve(),
    })


def configure_logging() -> None:
    """Send operational logs and captured warnings to stderr."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.captureWarnings(True)


def mount_bucket(
    config: ImageryConfig, bucket: str, *, read_only: bool,
) -> None:
    """Mount a bucket at its configured local path.

    Args:
        config: Validated settings.
        bucket: Bucket name.
        read_only: Whether to disallow writes through the mount.

    Raises:
        OSError: If the mountpoint cannot be created.
        RuntimeError: If mounting fails.
    """
    from esdglider import gcp

    mountpoint = config.mount_root / bucket
    mountpoint.mkdir(parents=True, exist_ok=True)
    gcp.gcs_mount_bucket(bucket, mountpoint, ro=read_only)


def check_outputs(outputs: list[Path], *, overwrite: bool) -> None:
    """Reject existing outputs unless replacement was explicitly requested.

    Args:
        outputs: Planned output file paths.
        overwrite: Whether existing files may be replaced.

    Raises:
        FileExistsError: If any output exists without overwrite permission.
        IsADirectoryError: If an output is a directory.
    """
    for output in outputs:
        if output.is_dir():
            raise IsADirectoryError(output)
        if output.exists() and not overwrite:
            raise FileExistsError(
                f"{output} exists; inspect it or use --overwrite."
            )


def publish_file(source: Path, destination: Path, *, overwrite: bool) -> None:
    """Copy a completed local file to its destination.

    A failed copy can leave a partial destination; the caller must report the
    error, and recovery requires inspection and explicit overwrite.

    Args:
        source: Completed temporary file.
        destination: Final path, possibly on a bucket mount.
        overwrite: Whether replacement is allowed.

    Raises:
        OSError: If copying fails or exclusive creation finds an existing file.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader:
        with destination.open("wb" if overwrite else "xb") as writer:
            shutil.copyfileobj(reader, writer)
    logging.info("Wrote %s", destination)
