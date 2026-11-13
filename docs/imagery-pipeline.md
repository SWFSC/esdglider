# Imagery pipeline

For routine use on a prepared workstation, start with the
[operator quickstart](imagery-pipeline-quickstart.md). This detailed guide covers
setup, configuration options, individual stages, and recovery.

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
| [`esdglider/imagery_run_records.py`](../esdglider/imagery_run_records.py) | Archives run configs, data provenance, and data snapshots. |

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

3. Ensure you also have the separate `glider-processing` repository cloned at
   `~/glider-processing`. Clone it if workstation provisioning has not already
   done so. This repository is required for the Git-tracked configuration
   workflow described here: save your deployment YAML files in its
   `inference-configs/{year}/` directory, using the deployment's year, not
   directly in your home directory.
4. Configure Google Application Default Credentials. Your identity needs read
   access to imagery and write access to metadata for the file-generation
   stages. DAG preview needs read/list access to the metadata and output
   buckets; DAG submission also requires read access to the pipeline YAML,
   create/get access for run archives in the output bucket, and Airflow API
   permissions. Resume needs read access to those archives.
5. The DAG's execution identity needs access to its image inputs, pipeline YAML,
   models, and output bucket. The workstation's access does not establish this.

Commands below assume the code repository is at `~/esdglider`, its Python
environment is already active, and the configuration repository is at
`~/glider-processing`. They can be run from any working directory.

## 1. Create a deployment configuration

Copy `~/glider-processing/inference-configs/template.yaml` into the deployment's
year folder in the same repository, creating the folder if needed. For a 2026
deployment, save the copy as
`~/glider-processing/inference-configs/2026/glider-imagery.yaml`.
Edit the copy to select the deployment and model:

```yaml
deployment_name: glider-yyymmdd

dag:
  model_type: andy-6d-v1-detector-model
```
This file is your configuration file which will tell the program what model you want to run and on which data.
Replace the placeholder with the actual deployment name and an eight-digit
`YYYYMMDD` date. Use the year from that date for the configuration folder
(not necessarily the year you run the pipeline).
For a more complicated example, see the
[expanded deployment configuration example](#expanded-deployment-configuration-example).

- `deployment_name` is simply the folder name for the deployment ([example from bucket land](https://console.cloud.google.com/storage/browser/swfscesd-glider-imagery-data-in/2026/amlr08-20260321)).
- `model_type` must be a model which is already migrated to the DAG. For a list of migrated models, see [this JSON in the cloud](https://console.cloud.google.com/storage/browser/_details/ggn-nmfs-osi-dev-1-data/configs/model_runtime_definitions.json). To migrate a model yourself, follow the instructions in [this Google Doc](https://docs.google.com/document/d/12fqDmZUKytkDYhLj-7FLqf-RxoFs1BMrfv8ihtv5Zlc/edit?tab=t.0#heading=h.bka726cftr42).

Keep the same configuration for all stages.
Pass this repository-local YAML file to `--config` in each command below.
Commit and push it through the usual Git workflow; the pipeline does not
automatically commit or contact GitHub.
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
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --dry-run
```

This is a **path-only preview**: it does not mount buckets, scan images, list
cloud objects, write files, or query Airflow. It cannot confirm input existence,
permissions, manifest counts, existing outputs, or model validity.

To extract metadata, generate manifests, and submit all selected DAG runs with
one command:

```bash
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --submit
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
   gs://ggn-nmfs-osi-dev-1-data/SWFSC/glider/glider-yyymmdd/runs/{run_id}/outputs/01/
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

After a live submission, the remote DAG writes model results to the configured
`dag.output_bucket`, which defaults to `ggn-nmfs-osi-dev-1-data`. The requested
output prefix for each manifest chunk is:

```text
gs://{output_bucket}/SWFSC/glider/{deployment_name}/runs/{run_id}/outputs/{chunk_number:02d}/
```

For example, chunk 1 with the default chunk size is written under:

```text
gs://ggn-nmfs-osi-dev-1-data/SWFSC/glider/glider-yyymmdd/runs/{run_id}/outputs/01/
```

The chunk number comes from the manifest filename. The remote DAG and its
pipeline YAML determine the actual result files beneath that prefix; an
accepted submission does not guarantee that results have been created.

### Automatic run archives

Every new live submission gets a UTC timestamp plus UUID run ID, printed to
stderr along with its GCS folder. No manual upload or additional bucket is
required. Under `SWFSC/glider/{deployment_name}/runs/{run_id}/`:

```text
config/input.yaml        # exact authored YAML bytes, when --config is supplied
config/resolved.json     # effective local settings, options, paths, and DAG plans
config/pipeline.yaml     # snapshot of dag.yaml_config_path
manifests/*.json         # archived copies of the selected manifests
outputs/01/              # remote model results for chunk 1
outputs/02/              # remote model results for chunk 2, etc.
```

The `resolved.json` record includes defaults and CLI overrides, resolved local paths,
source YAML hash, available Git commit/dirty status, input object generations
and hashes, selection limit, and exact Airflow IDs and payloads. With CLI-only
configuration, `input.yaml` is absent and the source is explicitly recorded as
null. The Git status describes the working tree at invocation, not a promise
that all changes were committed. This captures the local configuration and
submission inputs, **not** remote model weights, container versions, runtime
definitions, or image contents; those can still change independently.

[`run-imagery-pipeline.py`](../scripts/run-imagery-pipeline.py) captures the
deployment YAML and CLI options before generating files.
[`trigger-osi-dag.py`](../scripts/trigger-osi-dag.py) saves that YAML as
`config/input.yaml` and the resolved settings and submission details as
`config/resolved.json`. It copies selected manifest JSON files into `manifests/`
and the YAML referenced by `dag.yaml_config_path` into `config/pipeline.yaml`.
The submitted DAG runs read these archived copies.

The original deployment metadata JSON, image metadata JSONL, and generated
manifests remain in the metadata bucket. The archived files and model results
under `outputs/` share the run folder in the output bucket.

All archive uploads use `if_generation_match=0` (create only), and
`resolved.json` is written last, before any Airflow submission. An upload or
permission failure prevents submission. A partial archive without that final
record cannot be resumed; inspect it and start a new run. Preview commands do
not create records; their proposed new-run ID and input locations are not
reserved for a later live invocation.

This is **application-level append-only behavior, not storage-enforced
immutability**. Users with update/delete permissions can still alter or remove
objects through other tools. No retention lock, bucket policy, or IAM change is
made by these commands. The DAG identity must also be able to read the archived
manifests and pipeline YAML in the output bucket.

### Resume DAG submission for an existing run

Keep the same YAML, effective CLI overrides, and original `--test-limit`, if any.
The DAG command accepts `--num-cores` and `--directory-filter` for matching the
recorded file-generation settings; they do not rerun extraction or change the
selection of existing manifests.
After inspecting Airflow, reuse the printed run ID:

```bash
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --resume-run-id YOUR_RUN_ID --submit
```

The individual DAG command accepts the same options. Resume is **DAG-only**:
it does not extract metadata, regenerate manifests, or rewrite the archive.
It loads the saved plans, checks settings and selection, and verifies snapshot
generations and hashes. Shared manifests may have changed; the archived copies
are used instead. Without `--submit`, this previews the saved plans after
verification. The runner's `--dry-run` remains a path-only preview and does not
verify any cloud record.

Already successful, queued, or running recorded chunks are skipped. Missing
Airflow runs are submitted with their original IDs. Failed runs block
submission; inspect and recover them in Airflow. Resume rejects `--overwrite`.
Starting without `--resume-run-id` intentionally creates a **new run** and can
repeat inference even if another run for that deployment is already active.

### Prepare files and preview submissions instead

Omitting `--submit` still **writes metadata and manifests**, but previews the
resulting DAG payloads instead of submitting:

```bash
python ~/esdglider/scripts/run-imagery-pipeline.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml
```

### Trigger DAG if metadata and manifests already exist

After reviewing that preview, submit using the individual DAG command below;
there is no need to extract metadata or regenerate manifests again:

```bash
python ~/esdglider/scripts/trigger-osi-dag.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --submit
```

### Runner options and recovery

- `--test-limit 1` limits only the final DAG stage; metadata extraction and
  manifest generation still process their full configured inputs.
- `--overwrite` permits replacement of metadata and manifests in the first two
  stages. It does not bypass DAG run guards or delete stale manifest chunks.
- Existing metadata/manifests are refused by default. The runner does not silently reuse
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
- During explicit resume, matching queued/running runs are skipped; failed
  runs block submission. Inspect/recover those runs in Airflow.
- Output objects without a matching successful run also block new submissions.
  Their presence alone does not establish completion. This includes outputs
  in the selected run folder. Older output layouts are not reused.

IDs are derived from archived input locations and submitted settings. They are
stable within one recorded run, but different new runs have different IDs and
output folders. Existing folders are not automatically deleted.

All selected runs are checked before any new runs are submitted. However,
submitting multiple runs is **not all-or-nothing**: jobs 1–4 could be accepted
before a connection problem prevents job 5 from being submitted. The command
stops and reports failure, but jobs 1–4 remain submitted. Retrying the same
run with `--resume-run-id` uses the same IDs rather than creating duplicate
copies. Successful and active runs skip; failed runs require inspection.

## Alternative: run individual stages

Use the following commands when you want to inspect or recover each stage
separately.

### Extract image metadata

Preview the derived paths without mounting buckets, scanning images, or writing:

```bash
python ~/esdglider/scripts/extract-image-metadata.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --dry-run
```

Generate the metadata:

```bash
python ~/esdglider/scripts/extract-image-metadata.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml
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
python ~/esdglider/scripts/generate-osi-json.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml
```

This reads the image-metadata JSONL file and writes one manifest per directory
chunk. For the example config:

```text
gs://swfscesd-glider-imagery-metadata/2026/glider-yyymmdd/osi-manifests-050dirs/glider-yyymmdd-image-manifest-chunk01.json
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
python ~/esdglider/scripts/trigger-osi-dag.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --dry-run
```

Preview is also the default when neither `--dry-run` nor `--submit` is supplied.
It obtains credentials and lists manifest/output objects, but does not mount
buckets, submit DAG runs, or write results. It does not query Airflow run states
or verify that the model/YAML will execute.

Each stdout line is a JSON object with a `dag_run_id`, the `conf` payload,
and an `existing_output_objects` flag. Operational logs go to stderr.
For a new-run preview, IDs are illustrative: live submission gets a new run ID
and replaces source input URIs with archived snapshot URIs.

For manifest chunk 01, inspect these payload fields:

| Field | Example |
| --- | --- |
| `input_file` | `gs://swfscesd-glider-imagery-metadata/2026/glider-yyymmdd/osi-manifests-050dirs/glider-yyymmdd-image-manifest-chunk01.json` |
| `output_bucket` | `ggn-nmfs-osi-dev-1-data` |
| `output_folder` | `SWFSC/glider/glider-yyymmdd/runs/{run_id}/outputs/01` |

Use `--test-limit 1` to select only the first manifest in numeric chunk order.
The output folder always comes from the manifest chunk number, even when other
chunks are absent. Filtering cannot renumber chunk 10 to output folder `01`.

### Submit and monitor

To submit an initial live test:

```bash
python ~/esdglider/scripts/trigger-osi-dag.py --config ~/glider-processing/inference-configs/2026/glider-imagery.yaml --submit --test-limit 1
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
deployment_name: glider-yyymmdd
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
`--dry-run`, `--submit`, `--overwrite`, `--test-limit`, and `--resume-run-id`. Local manifest
output is selected with `--output-directory` on the individual manifest command.
See [Optional settings and precedence](#optional-settings-and-precedence)
for how YAML and command-line overrides interact.
