# Productionization Backlog

This file tracks hosted deployment work that is intentionally deferred while
the catalog and local problem-solving pipeline are proved across disciplines.
Items here are not prerequisites for local catalog search, local provider
installation, deterministic atom execution, or catalog audit work.

## Promotion Gate

Do not begin the hosted rollout until the local pipeline has:

- a current audit inventory for every provider and atom family
- discipline-native retrieval cases beyond ECG and signal processing
- repeated successful search, selection, installation, and execution runs on
  several materially different problems
- no special-case retrieval or synthesis behavior added for one benchmark
- a recorded account of misses, incorrect candidates, and missing structures

## Provider Distribution

- publish immutable wheels for each `sciona-atoms-*` provider
- record exact distribution versions, HTTPS wheel URLs, and SHA-256 digests
- define version retirement and catalog rollback procedures
- sign release artifacts and retain release provenance
- enforce the provider release validator on protected branches
- test upgrades when multiple provider wheels share the PEP 420 namespace

## Hosted Catalog

- choose and document the authoritative hosted Supabase/Postgres project
- apply the mirrored migration tree from `sciona-infra`
- configure backups, point-in-time recovery, and migration rollback
- establish catalog publication and embedding refresh workers
- define anonymous, authenticated, operator, and service-role access policies
- load-test search and pagination at 10,000 or more atoms per provider

## Hosted Embeddings

- decide whether hosted search uses a managed embedding API, the canonical
  local embedding model, or both as separate versioned spaces
- provision embedding credentials only on the catalog service, never in the
  developer CLI
- validate model revision, dimensions, input schema, retry, and activation
  behavior in the deployed environment
- monitor refresh lag, failed queue rows, model drift, and cost

## Operations

- monitor search latency, retrieval recall by discipline, and empty-result rate
- monitor provider install latency, checksum failures, and installation errors
- alert on catalog reconciliation drift and audit coverage regressions
- define service-level objectives and incident ownership
- add structured logs and traces across search, selection, installation, and
  execution
- document disaster recovery and catalog rebuild procedures

## Security And Policy

- threat-model remote wheel installation and catalog metadata compromise
- restrict allowed artifact hosts and validate transport, digest, and package
  identity
- add dependency and artifact vulnerability scanning
- enforce license and provenance policy before publication
- define tenant entitlement and private-provider behavior
- complete abuse, rate-limit, and resource-exhaustion controls

## Release Acceptance

- run the disposable staging acceptance test against release candidates
- run the multidisciplinary intent benchmark against the deployed catalog
- install selected providers into clean virtual environments
- execute representative deterministic atoms from multiple disciplines
- verify that unselected providers remain absent
- record the release catalog snapshot and audit baseline
