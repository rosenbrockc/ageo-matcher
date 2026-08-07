#!/bin/bash
# example.sh: End-to-end algorithm generation for ECG Heart Rate Detection
# using a private templated adapter dataset configured outside Git.
set -euo pipefail

# Preflight checks
if ! command -v sciona >/dev/null 2>&1; then
  echo "Error: 'sciona' CLI not found in PATH." >&2
  exit 1
fi

# 1. Setup paths
GOAL="Detect heart rate from raw ECG signal"
ADAPTER_PATH="${E2E_PROFILE_DATASET:?Set E2E_PROFILE_DATASET to a local adapter path}"
BUILD_DIR="./build/ecg_hr_detection"
DIST_DIR="./dist/ecg_hr_detection"
RUN_METRICS="$BUILD_DIR/run_shared_context_metrics.json"
SYNTH_METRICS="$BUILD_DIR/synthesize_shared_context_metrics.json"

mkdir -p "$BUILD_DIR" "$DIST_DIR"

if [[ ! -f "$ADAPTER_PATH" ]]; then
  echo "Error: adapter dataset not found at $ADAPTER_PATH" >&2
  exit 1
fi

if [[ -z "${SCIONA_POSTGRES_URI:-}" ]]; then
  echo "Warning: SCIONA_POSTGRES_URI is not set; shared context will use in-memory backend."
fi

echo "=== Step 1: Decompose, Match, and Assemble ==="
# Run the full orchestration loop: decompose → match → refine.
# Produces cdg.json and matches.json in the build directory.
sciona run "$GOAL" \
  --prover python \
  --output "$BUILD_DIR" \
  --trace

if [[ ! -f "$BUILD_DIR/cdg.json" || ! -f "$BUILD_DIR/matches.json" ]]; then
  echo "Error: expected orchestration outputs missing in $BUILD_DIR" >&2
  exit 1
fi
if [[ -f "$BUILD_DIR/shared_context_metrics.json" ]]; then
  cp "$BUILD_DIR/shared_context_metrics.json" "$RUN_METRICS"
fi

echo -e "\n=== Step 2: Synthesize Verified Source ==="
# Assemble CDG + match results into a compilable Python file,
# then run the compile-analyze-patch repair loop to fix type/shape mismatches.
sciona synthesize "$BUILD_DIR/cdg.json" "$BUILD_DIR/matches.json" \
  --prover python \
  --output "$BUILD_DIR/verified.py"

if [[ ! -f "$BUILD_DIR/verified.py" ]]; then
  echo "Error: synthesis output missing: $BUILD_DIR/verified.py" >&2
  exit 1
fi
if [[ -f "$BUILD_DIR/shared_context_metrics.json" ]]; then
  cp "$BUILD_DIR/shared_context_metrics.json" "$SYNTH_METRICS"
fi

echo -e "\n=== Step 3: Export as Python Package ==="
# Export the verified source into a distributable Python package.
sciona export "$BUILD_DIR/verified.py" \
  --target python-pkg \
  --prover python \
  --output-dir "$DIST_DIR"

echo -e "\n=== Step 4: Profile Against Benchmark ==="
# Evaluate the final artifact's performance on the configured private dataset.
# Produces per-node gradient scores ranking error contributors.
sciona profile \
  --cdg "$BUILD_DIR/cdg.json" \
  --artifact "$BUILD_DIR/verified.py" \
  --dataset "$ADAPTER_PATH" \
  --metric precision

echo -e "\n=== End-to-End Generation Complete ==="
echo "Verified source:  $BUILD_DIR/verified.py"
echo "Python package:   $DIST_DIR/"
echo "CDG structure:    $BUILD_DIR/cdg.json"
if [[ -f "$RUN_METRICS" ]]; then
  echo "Run metrics:      $RUN_METRICS"
fi
if [[ -f "$SYNTH_METRICS" ]]; then
  echo "Synthesize metrics: $SYNTH_METRICS"
fi
