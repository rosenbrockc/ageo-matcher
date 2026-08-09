# Cross-Disciplinary Blind Evaluation

The blind suites test whether the catalog retrieves, installs, assembles, and
executes reusable algorithm graphs without exposing hidden reference answers to
an agent. The open-data manifest is `evaluations/open_data_blind.json`; pinned
source metadata and checksums live in `evaluations/open_data_sources.json`.

## Coverage

The public suite spans four provider-owned workflows:

- signal processing: expert-annotated ambulatory waveform event-rate estimation;
- computer science: guarded Dijkstra shortest paths on a real road graph;
- geospatial: WGS84 GPS fixes to ECEF coordinates;
- robotics: linear state initialization, prediction, and readout.

Every task has three prompt variants: explicit discipline language, masked
language that describes only the operation, and nearby-domain language. Search
metadata contains descriptions and context gates, never fixture outputs.

The report records top-k recall, reciprocal rank, selected CDG, bound leaves,
newly installed provider distributions, cold/warm state, search/build/runtime
latency, and hidden correctness metrics. When enabled, the agent comparison
adds three arms for each task:

- `small_sciona`: a small model instructed to use `sciona-build`;
- `small_scratch`: the same small model implementing from scratch;
- `large_scratch`: a larger-model scratch control.

Each agent receives only a smoke-test input. Reference values stay in the
runner. Scratch solutions are rejected if they import Sciona or packaged
domain-specific implementations. Token summaries report input, cached input,
output, reasoning output, and input-plus-output totals separately; cached and
reasoning subsets are not added to the total a second time.

## Public Data

The runner downloads only registry-declared files, verifies byte count and
SHA-256 before use, and caches them outside the repository. The pinned sources
are MIT-BIH Arrhythmia Database record 100 (ODC Attribution 1.0), DIMACS
Challenge 9 Rome99 (public domain), and UCI GPS Trajectories (CC BY 4.0).
Committed tests use synthetic fixtures and never require network access.

Fetch the data without running an evaluation:

```bash
.venv/bin/python scripts/fetch_open_evaluation_data.py
```

Run against an existing local catalog API:

```bash
.venv/bin/python scripts/run_open_data_blind.py \
  --api-url http://127.0.0.1:8000 \
  --output output/open-data-blind
```

Add `--with-agents --tool-python /path/to/cold-venv/bin/python` for the agent
comparison. Agent launch failures remain failed runs instead of being confused
with successful deterministic builds.

## Disposable End To End

```bash
.venv/bin/python scripts/run_provider_staging_e2e.py \
  --open-data-evaluation
```

Add `--open-data-agent-comparison --agent-repetitions 5` to run repeated agent
arms. Staging builds
provider wheels, publishes the real catalog into disposable Supabase, serves
verified wheels over local HTTPS, installs selected providers into cold virtual
environments, and tears the infrastructure down afterward.

The PostgreSQL scale check creates a temporary GIN-indexed table with 10,500
multidisciplinary documents, measures recall/MRR/latency, and rolls back. It
does not contaminate the catalog or turn load-test distractors into atoms.
