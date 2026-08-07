# Federated Provider Catalog Runtime

Sciona atom providers are independent Python distributions that contribute to
the `sciona.atoms` PEP 420 namespace. The matcher does not require those
distributions to be installed before catalog retrieval.

## Catalog publication

Provider repositories expose distribution metadata through `pyproject.toml`.
The default installation requirement is the exact project name and version:

```toml
[project]
name = "sciona-atoms-signal"
version = "1.0.0"
```

An immutable wheel can be published explicitly:

```toml
[tool.sciona.provider]
wheel-url = "https://packages.example/sciona_atoms_signal-1.0.0-py3-none-any.whl"
wheel-sha256 = "<64 lowercase hex characters>"
```

Provider namespace exclusions make ownership explicit when source history
contains compatibility copies:

```toml
[tool.sciona.provider]
excluded-import-prefixes = ["sciona.atoms.numerical"]
```

Run a side-effect-free inventory, then apply the complete publication:

```bash
sciona catalog publish-providers --workspace-root /path/to/providers
sciona catalog publish-providers --workspace-root /path/to/providers \
  --apply --ensure-owner
```

Before publication, require every audit manifest, tracked review bundle, and
reference binding to resolve to an installable seeded atom:

```bash
sciona catalog reconcile-providers --workspace-root /path/to/providers --strict
```

The reconciler accepts only exact identities or unique same-provider
module-and-symbol matches. Deterministic aliases can be rewritten with
`--apply`. Metadata without an installable implementation can be removed with
`--retire-unresolved`; each removal is recorded in the provider's
`data/retired_catalog_metadata.json`. Retirement is explicit and is not part of
ordinary publication.

Tracked review bundles are merged into the publication input in memory.
Provider-local `data/audit_manifest.json` files are generated working artifacts
and are not required in a clean checkout. A bundle that fails the current
schema or database taxonomy is ignored conservatively, logged as audit debt,
and cannot make an atom publishable.

## Provider release contract

Every provider pull request and main-branch build must validate the wheel that
will be published:

```bash
sciona catalog validate-provider-release /path/to/sciona-atoms-signal
```

The contract requires the distribution name to match the repository name, a
SemVer-compatible version, setuptools PEP 420 discovery, at least one decorated
atom, unique module-and-symbol targets, and no `sciona`, `sciona.atoms`, or
`sciona.probes` namespace initializer. It builds from a clean source copy,
checks excluded-prefix ownership and catalog modules inside the wheel, installs
the wheel without dependencies in a clean virtual environment, and verifies
the installed version, namespaces, and module files.

Provider CI checks out `sciona-matcher` and runs this central validator. A
release must pass that workflow before its immutable wheel URL and SHA-256 are
published to the catalog. Bump `[project].version` for every changed wheel;
catalog installation requirements are exact `name==version` pins.

## Embedding lifecycle

Catalog publication embeds all changed, publishable atoms with the configured
embedding space. The defaults are OpenAI `text-embedding-3-small`, 1,536
dimensions, and input schema `atom-search-v1`. Operators may pin the model
revision independently:

```bash
export OPENAI_API_KEY=...
export SCIONA_CATALOG_EMBEDDING_MODEL=text-embedding-3-small
export SCIONA_CATALOG_EMBEDDING_MODEL_REVISION=text-embedding-3-small
export SCIONA_CATALOG_EMBEDDING_DIMENSIONS=1536
export SCIONA_CATALOG_EMBEDDING_INPUT_SCHEMA=atom-search-v1
```

Model, revision, dimensions, input schema, response model, input hash, and a
derived space identifier are stored with every vector. A new space becomes
active only after its refresh succeeds; anonymous and authenticated search can
read vectors only from that active space. Changing any provenance field makes
the affected catalog rows eligible for refresh.

Embedding API calls use bounded exponential retry. Operators can tune the
limits with `SCIONA_CATALOG_EMBEDDING_MAX_ATTEMPTS` and
`SCIONA_CATALOG_EMBEDDING_RETRY_BACKOFF_SECONDS`. A terminal batch failure
marks its pending queue rows `failed`, records a bounded error message, aborts
publication, and leaves the prior embedding space active. A successful rerun
upserts by atom ID and activates the new space only after every requested row
is stored.

## Runtime discovery

`sciona catalog search` calls the platform catalog API. The API embeds the
query and invokes Postgres hybrid search when an embedding provider is
configured; otherwise it falls back to Postgres full-text search.

```bash
sciona catalog search "estimate energy from mass and velocity"
```

Search is side-effect free. Installation occurs only after selecting an exact
atom:

```bash
sciona catalog install \
  sciona.atoms.physics.mechanics.kinetic_energy
```

The installer accepts one pinned distribution requirement. When a wheel URL is
present, it permits HTTPS, verifies the declared SHA-256 digest, installs into
the current virtual environment, refreshes the namespace, and imports the
catalog-declared module and symbol. It does not install other search candidates.

Concurrent requests for the same distribution are serialized with a
cross-process lock and recheck installed state after acquiring it. Pip installs
time out after 300 seconds and wheel downloads are capped at 512 MiB by default.
The operational overrides are
`SCIONA_PROVIDER_INSTALL_LOCK_TIMEOUT_SECONDS`,
`SCIONA_PROVIDER_INSTALL_TIMEOUT_SECONDS`, and
`SCIONA_PROVIDER_MAX_WHEEL_BYTES`.

## Staging acceptance test

With Docker running, exercise the deployed path against four real provider
wheels and a disposable migrated Supabase project:

```bash
.venv/bin/python scripts/run_provider_staging_e2e.py
```

The runner publishes the sibling provider inventory, embeds more than 1,000
publishable rows, serves immutable wheels
over locally trusted HTTPS, starts the platform API, creates a fresh matcher
virtual environment, and installs and executes one selected atom each from
signal processing, physics, fintech, and machine learning. It also verifies
embedding-space activation, public visibility, pagination, and an empty repeat
refresh. Search candidates must remain uninstalled until their individual
selection step.

For the faster catalog-only audit, omit wheel construction and execution:

```bash
.venv/bin/python scripts/run_provider_staging_e2e.py \
  --audit-only \
  --audit-output /tmp/sciona-catalog-audit.json
```

The current counts and provider work order are recorded in
[Catalog Audit Baseline](audit/CATALOG_AUDIT_BASELINE.md). The zero-extra-key
CLI and future MCP boundary are described in
[Local Agent Tooling](LOCAL_AGENT_TOOLING.md). Hosted deployment work is
deferred in [PRODUCTIONIZE.md](../PRODUCTIONIZE.md).

The checked-in intent benchmark is deliberately multidisciplinary. It currently
requires recall-at-10 for core utilities, biology, deep learning, fintech,
machine learning, physics, and signal processing; the staging runner requires
all seven disciplines to pass. Signal, physics, fintech, and machine learning
also cross the wheel-install and execution boundary in a cold environment.

Computer science, geospatial, and robotics are seeded but are not yet part of
the served acceptance set because their audit rollups are incomplete. Adding
them requires resolving their catalog metadata and extending the same benchmark
with discipline-native intents. Do not weaken the generic retrieval gate or add
special-case query logic to make a single discipline pass.
