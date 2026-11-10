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
| [`scripts/run-imagery-pipeline.py`](../scripts/run-imagery-pipeline.py) | Recommended: runs all the other scripts in order. |
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
   [workstation guide](https://github.com/SWFSC/glider-lab/blob/main/gcs-scripts/linux-workstation-ml-dag.md):
   ```sh
   bash ~/install_gcsfuse.sh
   ```
2. Clone this repository and activate a Python environment containing the
   installed local `esdglider` package and its cloud dependencies. Use a checkout
   containing the imagery CLI scripts described here.
   
   ```sh
   cd ~/esdglider && conda activate esdglider
   ```

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

dag:
  model_type: andy-6d-v1-detector-model
```
This file is your configuration file which will tell the program what model you want to run and on which data.
For a more complicated example, see the
[expanded deployment configuration example](#expanded-deployment-configuration-example).

- `deployment_name` is simply the folder name for the deployment ([example from bucket land](https://console.cloud.google.com/storage/browser/swfscesd-glider-imagery-data-in/2026/amlr08-20260321)).
- `model_type` must be a model which is already migrated to the DAG. For a list of migrated models, see [this JSON in the cloud](https://console.cloud.google.com/storage/browser/_details/ggn-nmfs-osi-dev-1-data/configs/model_runtime_definitions.json). To migrate a model yourself, follow the instructions in [this Google Doc](https://docs.google.com/document/d/12fqDmZUKytkDYhLj-7FLqf-RxoFs1BMrfv8ihtv5Zlc/edit?tab=t.0#heading=h.bka726cftr42).

Keep the same configuration for all stages.
The YAML you save here configures these workstation commands; `dag.yaml_config_path` points
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
| `dag.model_type` | No default; required for the runner and DAG command, using YAML or `--model-type`. |
| `dag.yaml_config_path` | Defaults to `gs://ggn-nmfs-osi-dev-1-data/SWFSC/sam/viame-custom-pipeline-viamecsv-uploadno.yaml`; override when your model needs another pipeline YAML. |
| `dag.composer_url` | Existing OSI Composer Airflow URL; override for another environment. |
| `dag.dag_id` | `optics-pipeline` |

Unknown keys, invalid types, invalid dates, and nonpositive counts fail before
cloud operations. Live submission cannot be enabled through YAML: it always
requires the explicit CLI option `--submit`.

Select `dag.model_type` deliberately; the model shown in the example is not a
default. Metadata extraction and manifest generation do not require a model.
The runner and DAG command require one even for previews. Ensure the pipeline
YAML at `dag.yaml_config_path` is appropriate for the model you select.

## 2. Run the complete workflow (recommended for new data)

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

### Confirm submission and monitor progress

Live processing may incur cloud costs. The runner waits for the local stages
and submission requests, but **does not wait for remote inference to finish**.
A `Submitted ... as ...` message means Airflow accepted that run, not that the
images have finished processing.

1. Open the configured Cloud Composer Airflow UI. With the default settings,
   use the
   [optics-pipeline DAG page](https://14668048b1ac46eba7798ad8ed19d75a-dot-us-central1.composer.googleusercontent.com/dags/optics-pipeline/grid).
   Find the run ID printed by the command and inspect its state and task logs.
   A queued or running state is not completion; check for `success` or investigate
   failed tasks.
2. Check the
   [Batch jobs](https://console.cloud.google.com/batch/jobs?cloudshell=false&project=ggn-nmfs-osi-dev-1)
   for the underlying processing jobs. A submitted Airflow run may not have
   created its Batch job yet.
3. Inspect the
   [model outputs](https://console.cloud.google.com/storage/browser/ggn-nmfs-osi-dev-1-data/SWFSC/glider).
   For the first chunk in this example, the requested output location is:

   ```text
   gs://ggn-nmfs-osi-dev-1-data/SWFSC/glider/calanus-20261007/050dirs/01/
   ```

These links reflect the default environment; use the corresponding Airflow UI,
project, and bucket if you override them. The remote DAG and container YAML
determine the actual result files beneath the output location. Output files
alone do not prove that the entire run succeeded.

Operational logs and errors go to the terminal's stderr stream, **not the cloud
logs bucket**. Submission previews are printed to stdout. Zone allocation and
remote task timeouts remain DAG/runtime configuration concerns.

### What the workflow reads and writes

With the default buckets, source images are read recursively from:

```text
gs://swfscesd-glider-imagery-data-in/{year}/{deployment_name}/images/
```

Metadata and manifests are written beneath
`gs://swfscesd-glider-imagery-metadata/{year}/{deployment_name}/`:

- `{deployment_name}-deployment-metadata.json`
- `{deployment_name}-image-metadata.jsonl`
- `osi-manifests-050dirs/{deployment_name}-image-manifest-chunk01.json`, etc.

`050dirs` reflects the configured chunk size of 50 directories, not 50 images.
The final chunk can contain fewer directories. No source images is an error.

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
- A failed stage stops the runner and reports failure with a nonzero exit code.
  Earlier writes or accepted submissions are not undone. Inspect outputs and
  Airflow run states before retrying; see the safeguards below.
- Do not use `--overwrite` just to progress from a submission preview to live
  submission. Use the individual DAG command instead.

### Existing and partial files (both workflows)

Both file-generation commands refuse existing outputs by default and exit
nonzero. After inspecting the files, explicitly pass `--overwrite` to replace
them. Unexpected/stale JSON files in a manifest output directory must be
archived or removed deliberately before regenerating; `--overwrite` does not
silently delete old chunks.

The commands stage complete results on local temporary disk before publishing.
Allow enough temporary disk space for the generated metadata or manifests.
Copying the final files to a bucket is **not all-or-nothing**: an interrupted
copy can leave partial files or only some outputs updated. Inspect those
outputs before recovering with `--overwrite`. Do not run overlapping writers
for the same deployment.

### Existing DAG runs and submission failures (both workflows)

Before any new submission, the command checks every selected deterministic
Airflow run ID:

- A matching `success` run is reported as already successful and skipped.
- A matching active or failed run blocks new submissions. Inspect/recover that
  run in Airflow rather than creating duplicate work.
- Output objects without a matching successful run also block new submissions.
  Their presence alone does not establish completion. This includes outputs
  from runs launched by the older script without deterministic IDs.

IDs are derived from the manifest location and submitted settings, not the
current contents of files at those locations. Do not overwrite manifests or
pipeline YAML and expect the same location to create a fresh run automatically.
Existing folders are not automatically deleted.

All selected runs are checked before any new runs are submitted. However,
submitting multiple runs is **not all-or-nothing**: jobs 1–4 could be accepted
before a connection problem prevents job 5 from being submitted. The command
stops and reports failure, but jobs 1–4 remain submitted. Retrying the same
configuration uses the same IDs rather than creating duplicate copies.
Successful runs skip; active or failed runs require inspection before proceeding.

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

The [existing and partial file safeguards](#existing-and-partial-files-both-workflows)
apply to these individual commands as well as the runner.

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

To submit all selected manifests, omit `--test-limit`. The
[existing-run and submission safeguards](#existing-dag-runs-and-submission-failures-both-workflows)
apply here too.

Follow [Confirm submission and monitor progress](#confirm-submission-and-monitor-progress)
above to verify acceptance, track processing, and inspect outputs. An accepted
submission is not completed inference.

## Additional materials

### Expanded deployment configuration example

The minimal example above is sufficient for the usual workflow. The following
example shows all supported YAML settings and where they belong. Most values
shown are defaults; `num_cores: 4` demonstrates an optional override.
Keep only the settings you need in your own file.

```yaml
deployment_name: calanus-20261007
directory_chunk_size: 50
mount_root: ~/mnt-gcs
num_cores: 4

# null includes all image directories. To select a subset, use a list:
# directory_filter: [dir0000004, dir0000005]
directory_filter: null

imagery_bucket: swfscesd-glider-imagery-data-in
metadata_bucket: swfscesd-glider-imagery-metadata

dag:
  # Required for the runner and DAG command; there is no default model.
  model_type: andy-6d-v1-detector-model
  yaml_config_path: gs://ggn-nmfs-osi-dev-1-data/SWFSC/sam/viame-custom-pipeline-viamecsv-uploadno.yaml
  output_bucket: ggn-nmfs-osi-dev-1-data
  composer_url: https://14668048b1ac46eba7798ad8ed19d75a-dot-us-central1.composer.googleusercontent.com
  dag_id: optics-pipeline
```

Choose the model deliberately and check that its pipeline YAML is appropriate.
Directory names in a filter must exist in the image metadata; the names above
are illustrative.

Safety and execution controls remain command-line options, not YAML settings:
`--dry-run`, `--submit`, `--overwrite`, and `--test-limit`. Local manifest
output is selected with `--output-directory` on the individual manifest command.
See [Optional settings and precedence](#optional-settings-and-precedence)
for how YAML and command-line overrides interact.
