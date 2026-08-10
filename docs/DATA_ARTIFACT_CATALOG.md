# Data Artifact Catalog

SCIONA stores example, validation, and benchmark datasets as versioned data
artifacts in Postgres. Atom and CDG packages do not contain dataset links.
Adding a new known-good input therefore does not require a provider release or
a `cdg.json` change.

## Manifest

```json
{
  "fqn": "sciona.data.public.synthetic_signal.v1",
  "version": "1.0.0",
  "name": "Public synthetic signal",
  "description": "A deterministic signal fixture.",
  "modality": "time_series",
  "media_type": "application/x-npy",
  "shape": [4096],
  "dtype": "float64",
  "sampling": {"frequency_hz": 256},
  "evaluation": {
    "schema_version": "1.0",
    "objective": "mae",
    "prediction_node_id": "measure",
    "spec": {
      "loss": "mae",
      "prediction": {
        "value_output": "rate",
        "time_output": "indices",
        "time_kind": "timestamp"
      },
      "reference": {
        "value_source": "reference_rate",
        "time_source": "reference_index"
      }
    },
    "reference_data": {
      "reference_index": [100, 200, 300],
      "reference_rate": [60.0, 61.0, 60.0]
    }
  },
  "license_expression": "CC0-1.0",
  "attribution": {
    "source": "Example public source",
    "url": "https://example.org/data"
  },
  "assets": [
    {
      "asset_path": "signal.npy",
      "byte_size": 32896,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "format": "npy",
      "media_type": "application/x-npy",
      "storage_uri": "https://example.org/data/signal.npy"
    }
  ],
  "compatibility": [
    {
      "consumer_fqdn": "sciona.atoms.signal.example_rate",
      "input_port": "signal",
      "kind": "validated",
      "confidence": 1.0,
      "evidence": {"suite": "public-validation-v1"}
    }
  ]
}
```

Assets require an exact byte count and SHA-256 digest. Compatibility kinds are
`example`, `validated`, `benchmark`, and `incompatible`. Evidence belongs in
Postgres with the compatibility record, not in atom source metadata.

Evaluation is optional. When present, it defines the objective, the graph node
and named outputs to score, and the reference channels. The visualizer uses
this contract to score each executable evolution version after execution.
Aligned-series `mse`, `rmse`, and `mae` objectives are supported. The contract
belongs to the versioned data artifact, so adding a benchmark or reference
source does not require an atom-provider release or discipline-specific UI
code.

## Ingestion

Validation is the default and does not connect to Postgres:

```bash
sciona catalog ingest-dataset dataset.json
```

Apply a validated manifest to a local Supabase/Postgres catalog:

```bash
sciona catalog ingest-dataset dataset.json --apply \
  --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

Every compatibility consumer must already exist in the unified artifact
catalog. Ingestion is transactional, so a missing atom/CDG rejects the entire
manifest.

## Discovery And Cache

The CDG visualizer queries Postgres by atom FQN and input port. A selected
dataset is downloaded only when execution needs it, checked against its byte
count and SHA-256 digest, and stored by content hash under
`~/.cache/sciona/datasets`.

Supported public transports are HTTPS and anonymous S3. Signed S3 is also
supported when normal AWS credentials are available. A hosted visualizer can
be used without an API key by setting `SCIONA_DATA_CATALOG_URL`; local tools
otherwise query `SCIONA_DATA_CATALOG_DATABASE_URL`, `SCIONA_POSTGRES_URI`, or
the standard local Supabase Postgres URL.

Synthetic fallback is disabled by default. It is available only for explicit
development demonstrations with
`SCIONA_DATASET_ALLOW_SYNTHETIC_FALLBACK=true`; evaluation runs should never
enable it.
