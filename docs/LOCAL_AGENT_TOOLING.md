# Local Agent Tooling

## Objective

A developer using Codex, Claude, agy, or another command-line agent should be
able to discover and execute Sciona's deterministic assets without obtaining a
second LLM or embedding API key.

The command-line agent owns language reasoning. Sciona owns catalog retrieval,
evidence, provider materialization, validation, and deterministic execution.
Sciona should not hide another agent call behind its CLI.

## Canonical Interface

The `sciona` CLI is the compatibility boundary. Commands should emit stable,
versioned JSON on stdout and diagnostics on stderr so any shell-capable agent
can use them. A future MCP server should be a thin adapter over the same command
services, not a separate implementation.

The intended tool sequence is:

1. Search the local Postgres catalog for a problem or subproblem.
2. Inspect the evidence, contract, provenance, and installation metadata for a
   candidate.
3. Select one exact FQDN.
4. Install only that provider into the active virtual environment.
5. Invoke or compose the selected deterministic asset.
6. Validate the result and retain the selected FQDNs and versions as provenance.

## Retrieval Without API Keys

Postgres full-text search is the required zero-key baseline. It is available
immediately after local seeding and must remain a supported mode even when no
embedding model is installed.

Semantic retrieval should use a canonical local embedding space:

- generate atom embeddings during local catalog seeding with FastEmbed
- store the model name, revision, dimensions, input schema, and space ID beside
  the vectors
- embed queries locally with the same model
- use the existing Postgres hybrid-search RPC
- download the model once through the normal Python model cache; no API key is
  required
- fall back to full-text search when the optional local embedding dependency or
  model is unavailable

Managed embedding APIs may remain an optional hosted implementation, but they
must not define the local command contract or catalog semantics.

## Agent-Friendly Commands

The CLI should converge on these operations:

```text
sciona catalog search --local --format json <intent>
sciona catalog inspect --local --format json <fqdn>
sciona catalog install --local --format json <fqdn>
sciona catalog audit-providers --format json
sciona run --format json <problem-or-plan>
```

Search output should include an exact FQDN, artifact kind, score components,
domain tags, concise contract, audit verdict, evidence summary, provider ID,
and installation state. The agent should never have to infer an install target
from display text.

Installation and execution must be separate operations. Search must remain
side-effect free, and an agent must explicitly select an exact candidate before
Sciona installs a provider.

## Local Provider Resolution

Before immutable production URLs exist, local development should resolve a
provider from a declared sibling workspace or a locally built wheel cache. The
catalog still records the exact distribution name and version. Local resolution
must not silently install every sibling provider or accept an unpinned package.

The preferred resolution order is:

1. matching distribution and version already installed
2. matching wheel in the configured local artifact cache
3. matching sibling provider repo, built and validated on demand
4. immutable catalog URL when production distribution is enabled

## Near-Term Implementation Order

1. Finish the provider/family audit inventory and make it a strict local gate.
2. Add direct local catalog access to the CLI, avoiding a required hosted API.
3. Add `catalog inspect` and machine-readable score/evidence fields.
4. Add local sibling/wheel-cache provider resolution.
5. Add canonical FastEmbed catalog and query embedding support.
6. Add a thin MCP adapter after the CLI contract is stable.
7. Exercise the workflow on several cross-disciplinary problems and expand the
   benchmark only from observed failures.
