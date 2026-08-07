# Quickstart

Minimal path to create a new algorithm from a goal and take it through profiling.

## Prerequisites

- Activate the repo virtualenv: `source .venv/bin/activate`
- Build the search index once:

```bash
sciona index build --prover python
```

- For Python synthesis/profile runs, use the same runtime as the benchmark:

```bash
export PYTHONPATH=$PWD
export SCIONA_PYTHON_PATH=$PWD/.venv/bin/python
export PYTHON_JULIACALL_INIT=no
export JULIA_DEPOT_PATH=/tmp/sciona-julia-depot
```

## 1. Generate Grounding Artifacts

Pick a goal and output directory:

```bash
sciona run "Detect heart rate from raw ECG signal" \
  --mode verified \
  --prover python \
  --output output/my_algorithm
```

This produces at least:
- `output/my_algorithm/cdg.json`
- `output/my_algorithm/matches.json`

## 2. Synthesize The Algorithm

```bash
sciona synthesize output/my_algorithm/cdg.json output/my_algorithm/matches.json \
  --mode verified \
  --prover python \
  --output output/my_algorithm/verified.py
```

## 3. Export A Runnable Artifact

```bash
sciona export output/my_algorithm/verified.py \
  --target python-pkg \
  --prover python \
  --output-dir output/my_algorithm/export_python_pkg
```

The runnable artifact is:
- `output/my_algorithm/export_python_pkg/runner.py`

## 4. Profile It

Provide a private evaluation adapter through local configuration:

```bash
sciona profile \
  --cdg output/my_algorithm/cdg.json \
  --artifact output/my_algorithm/verified.py \
  --dataset "$E2E_PROFILE_DATASET" \
  --dataset-var tracker=single \
  --metric precision
```

Profile prints ranked bottlenecks/gradients for the generated pipeline.
