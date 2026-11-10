# Imagery pipeline

Instructions for how to extract image metadata, generate OSI manifests, and submit them to the Optics SI
Airflow DAG in Cloud Composer.
Basically you're telling the DAG how to find the images and run the correct
model.

These stages use imagery and image metadata, not
corrected profile files, so they can run before glider profiles are corrected.

The recommended workflow uses one command to run all three stages. Each stage
also remains available independently for users who prefer to work step by step.
Both approaches read the **same YAML file**; do not edit Python source files
to select a deployment.

## Files used in this workflow

Paths below are relative to the root of this repository.

| File | Purpose |
| --- | --- |
| [`scripts/run-imagery-pipeline.py`](../scripts/run-imagery-pipeline.py) | Recommended entry point: runs all three stages in order. |
| [`scripts/extract-image-metadata.py`](../scripts/extract-image-metadata.py) | Extracts deployment JSON and per-image JSONL metadata. |
| [`scripts/generate-osi-json.py`](../scripts/generate-osi-json.py) | Converts image metadata into chunked OSI manifest JSON files. |
| [`scripts/trigger-osi-dag.py`](../scripts/trigger-osi-dag.py) | Previews or submits manifest-based Airflow DAG runs. |
| [`resources/imagery-deployment.example.yaml`](../resources/imagery-deployment.example.yaml) | Template for shared deployment configuration. |
| [`esdglider/imagery_cli.py`](../esdglider/imagery_cli.py) | Shared CLI parsing, configuration validation, path derivation, and file handling |

Your deployment YAML is a separate file you create from the template. The
remote pipeline YAML referenced by `dag.yaml_config_path` is another
configuration file which lives in the cloud created by Sam, not the shared deployment YAML or the Airflow DAG source.

## Prerequisites

1. Use a Linux environment with `gcsfuse` available for the file-generation
   stages. For the lab-managed workstation setup, see the
   [workstation guide](https://github.com/SWFSC/glider-lab/blob/main/gcs-scripts/linux-workstation-ml-dag.md).
2. Clone this repository and activate a Python environment containing the
   installed local `esdglider` package and its cloud dependencies. Use a checkout
   containing the imagery CLI scripts described here.
3. Configure Google Application Default Credentials. Your identity needs read
   access to imagery and write access to metadata for the file-generation
   stages. DAG preview needs read/list access to the metadata and output
   buckets; DAG submission also requires Airflow API permissions.
4. The DAG's execution identity needs access to its image inputs, pipeline YAML,
   models, and output bucket. The workstation's access does not establish this.

Commands below assume the repository is at `~/esdglider`, with its Python environment
already active. They can be run from any working directory.

## 1. Create a deployment configuration

Copy the contents of
[`resources/imagery-deployment.example.yaml`](../resources/imagery-deployment.example.yaml)
into a file such as `~/calanus-imagery.yaml`:

```yaml
deployment_name: calanus-20261007
directory_chunk_size: 50

dag:
  model_type: andy-6d-v1-detector-model
  yaml_config_path: gs://ggn-nmfs-osi-dev-1-data/SWFSC/sam/viame-custom-pipeline-viamecsv-uploadno.yaml
```

Keep the same configuration for all stages.
The YAML configures these workstation commands; `dag.yaml_config_path` points
to the separate container pipeline configuration used by the remote DAG (not this YAML).

### Optional settings and precedence

Command-line options override YAML values, which override package defaults.
Use `--help` on each script for its available options.

| YAML setting | Default / purpose |
| --- | --- |
| `deployment_name` | Required unless supplied as `--deployment-name`; must end in a valid `YYYYMMDD` date. |
| `directory_chunk_size` | `50`; directories per manifest, not images. |
| `mount_root` | `~/mnt-gcs`; expanded relative paths are resolved from the working directory. |
| `num_cores` | `null`; executor's default worker count. Applies to extraction. |
| `directory_filter` | `null`; all directories. A nonempty list selects a subset for manifest generation. |
| `imagery_bucket` | `swfscesd-glider-imagery-data-in` |
| `metadata_bucket` | `swfscesd-glider-imagery-metadata` |
| `dag.output_bucket` | `ggn-nmfs-osi-dev-1-data` |
| `dag.model_type` | `andy-6d-v1-detector-model` |
| `dag.yaml_config_path` | Existing VIAME CSV pipeline YAML, as shown above. |
| `dag.composer_url` | Existing OSI Composer Airflow URL; override for another environment. |
| `dag.dag_id` | `optics-pipeline` |

Unknown keys, invalid types, invalid dates, and nonpositive counts fail before
cloud operations. Live submission cannot be enabled through YAML: it always
requires the explicit CLI option `--submit`.

## 2. Run the complete workflow (recommended)

After creating the configuration above, preview the planned paths:

```bash
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/calanus-imagery.yaml --dry-run
```

This is a **path-only preview**: it does not mount buckets, scan images, list
cloud objects, write files, or query Airflow. It cannot confirm input existence,
permissions, manifest counts, existing outputs, or model validity.

To extract metadata, generate manifests, and submit all selected DAG runs with
one command:

```bash
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/calanus-imagery.yaml --submit
```

### Prepare files and preview submissions instead

Omitting `--submit` still **writes metadata and manifests**, but previews the
resulting DAG payloads instead of submitting:

```bash
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/calanus-imagery.yaml
```

### Trigger DAG if metadata and manifests already exist

After reviewing that preview, submit using the individual DAG command below;
there is no need to extract metadata or regenerate manifests again:

```bash
python ~/esdglider/scripts/trigger-osi-dag.py --config ~/calanus-imagery.yaml --submit
```

### Runner options and recovery

- `--test-limit 1` limits only the final DAG stage; metadata extraction and
  manifest generation still process their full configured inputs.
- `--overwrite` permits replacement of metadata and manifests in the first two
  stages. It does not bypass DAG run guards or delete stale manifest chunks.
- Existing outputs are refused by default. The runner does not silently reuse
  metadata or skip stages; use the individual commands to continue from a
  completed stage.
- A failed stage stops the runner with a nonzero exit code. Earlier writes or
  accepted submissions are not rolled back. Inspect outputs and Airflow run
  states before retrying; see the recovery guidance below.
- Do not use `--overwrite` just to progress from a submission preview to live
  submission. Use the individual DAG command instead.

## Alternative: run individual stages

Use the following commands when you want to inspect or recover each stage
separately.

### Extract image metadata

Preview the derived paths without mounting buckets, scanning images, or writing:

```bash
python ~/esdglider/scripts/extract-image-metadata.py --config ~/calanus-imagery.yaml --dry-run
```

Generate the metadata:

```bash
python ~/esdglider/scripts/extract-image-metadata.py --config ~/calanus-imagery.yaml
```

With the default buckets, extraction reads JPEG and PNG files recursively under:

```text
gs://swfscesd-glider-imagery-data-in/{year}/{deployment_name}/images/
```

It writes:

```text
gs://swfscesd-glider-imagery-metadata/{year}/{deployment_name}/{deployment_name}-deployment-metadata.json
gs://swfscesd-glider-imagery-metadata/{year}/{deployment_name}/{deployment_name}-image-metadata.jsonl
```

Images are sorted to make the first image used for deployment metadata
repeatable. Per-image metadata retains the existing JSONL format.

**Changed behavior:** operational logs now go to stderr, not the cloud logs
bucket. The old `run_pipeline_bool` setting is replaced by `--dry-run`.
No images is an error, not a successful no-op.

### Generate OSI manifests

```bash
python ~/esdglider/scripts/generate-osi-json.py --config ~/calanus-imagery.yaml
```

This reads the image-metadata JSONL file and writes one manifest per directory
chunk. For the example config:

```text
gs://swfscesd-glider-imagery-metadata/2026/calanus-20261007/osi-manifests-050dirs/calanus-20261007-image-manifest-chunk01.json
```

The final chunk may contain fewer than 50 directories.

- Use `--dry-run` to print the input path and output directory without mounting
  or writing.
- Use `--directory-filter dir0000004 dir0000005` to override a YAML subset.
- Use `--output-directory ~/preview-manifests` for local output. This still
  mounts the metadata bucket read-only to read the JSONL file. It does not
  upload the local manifests; upload them to the expected manifest prefix
  before using the DAG command.

#### Existing and partial files

Both file-generation commands refuse existing outputs by default and exit
nonzero. After inspecting the files, explicitly pass `--overwrite` to replace
them. Unexpected/stale JSON files in a manifest output directory must be
archived or removed deliberately before regenerating; `--overwrite` does not
silently delete old chunks.

The commands stage complete results on local temporary disk before publishing.
Allow enough temporary disk space for the generated metadata or manifests.
The final copies to a bucket mount are **not a transaction**: an interrupted
publication can leave partial files or only some outputs updated. Inspect those
outputs before recovering with `--overwrite`. Do not run overlapping writers
for the same deployment.

### Preview DAG submissions

```bash
python ~/esdglider/scripts/trigger-osi-dag.py --config ~/calanus-imagery.yaml --dry-run
```

Preview is also the default when neither `--dry-run` nor `--submit` is supplied.
It obtains credentials and lists manifest/output objects, but does not mount
buckets, submit DAG runs, or write results. It does not query Airflow run states
or verify that the model/YAML will execute.

Each stdout line is a JSON object with a stable `dag_run_id`, the `conf` payload,
and an `existing_output_objects` flag. Operational logs go to stderr.

For manifest chunk 01, inspect these payload fields:

| Field | Example |
| --- | --- |
| `input_file` | `gs://swfscesd-glider-imagery-metadata/2026/calanus-20261007/osi-manifests-050dirs/calanus-20261007-image-manifest-chunk01.json` |
| `output_bucket` | `ggn-nmfs-osi-dev-1-data` |
| `output_folder` | `SWFSC/glider/calanus-20261007/050dirs/01` |

Use `--test-limit 1` to select only the first manifest in numeric chunk order.
The output folder always comes from the manifest chunk number, even when other
chunks are absent. Filtering cannot renumber chunk 10 to output folder `01`.

### Submit and monitor

To submit an initial live test:

```bash
python ~/esdglider/scripts/trigger-osi-dag.py --config ~/calanus-imagery.yaml --submit --test-limit 1
```

To submit all selected manifests, omit `--test-limit`.
An accepted API request is a **submission**, not completed inference.

Before any new submission, the command checks every selected deterministic
Airflow run ID:

- A matching `success` run is reported as already successful and skipped.
- A matching active or failed run blocks new submissions. Inspect/recover that
  run in Airflow rather than creating duplicate work.
- Output objects without a matching successful run also block new submissions.
  Their presence alone does not establish completion. This includes outputs
  from runs launched by the older script without deterministic IDs.

IDs are derived from manifest URI and DAG payload, not the current contents of
objects at those URIs. Do not overwrite manifests or pipeline YAML and expect
the same URI to create a fresh run automatically. Existing folders are not automatically deleted.

All selected runs are preflighted before posting new ones, but HTTP submissions
are not transactional: a later request can fail after earlier ones were
accepted. The command exits nonzero; retrying the same configuration uses the
same IDs instead of silently creating duplicates.

Monitor:

- The configured Cloud Composer Airflow UI, DAG `optics-pipeline` [here](https://14668048b1ac46eba7798ad8ed19d75a-dot-us-central1.composer.googleusercontent.com/dags/optics-pipeline/grid).
- [Batch jobs](https://console.cloud.google.com/batch/jobs?cloudshell=false&project=ggn-nmfs-osi-dev-1).
- [Model outputs](https://console.cloud.google.com/storage/browser/ggn-nmfs-osi-dev-1-data/SWFSC/glider).

For the first example chunk, the requested output prefix is:

```text
gs://ggn-nmfs-osi-dev-1-data/SWFSC/glider/calanus-20261007/050dirs/01/
```

The remote DAG and container YAML determine the actual result files beneath
that location. Zone allocation and remote task timeouts remain DAG/runtime
configuration concerns.
