# Audit Status Improvement Implementation Plan

> The counts below are historical. The current ten-provider baseline is
> maintained in
> [Catalog Audit Baseline](../audit/CATALOG_AUDIT_BASELINE.md); as of August 6,
> 2026 it contains 3,380 seeded atoms, 1,302 audit-ready atoms, and 2,078 audit
> gaps.

## Status

Drafted on April 15, 2026 as the worker-facing execution plan for improving the
audit status of the remaining non-publishable atoms without weakening the
publishability bar.

## Purpose

The manifest and export paths are working correctly now. The remaining blocker
is upstream audit completeness.

The rule for public publication is now explicit:

- no full audit bundle
- no publishability
- no public manifest inclusion

Developer mode can still surface unpublished atoms for local iteration, but
that does not change the public publication bar.

This plan is about increasing the number of atoms that genuinely meet the audit
requirements by producing real provider-owned audit artifacts and replaying them
through the existing Supabase ingestion path.

## Current Repo Reality

As of the latest local replay:

- `504` atoms exist in Supabase
- `99` atoms are publishable
- `405` atoms remain non-publishable

Current non-publishable distribution by namespace:

- `physics`: `90`
- `fintech`: `80`
- `bio`: `58`
- `robotics`: `52`
- `expansion`: `45`
- `inference`: `34`
- `signal_processing`: `15`
- `state_estimation`: `11`
- `numpy`: `9`
- `scipy`: `9`
- `dynamic_programming`: `1`
- `ml`: `1`

Largest current family buckets:

- `tempo_jl`: `75`
- `quantfin`: `46`
- `expansion`: `45`
- `molecular_docking`: `42`
- `pronto`: `30`
- `institutional_quant_engine`: `27`
- `mcmc_foundational`: `24`
- `rust_robotics`: `22`

Current missing metadata surfaces for the `405` non-publishable atoms:

- `missing_io = 363`
- `missing_params = 405`
- `missing_technical_description = 405`
- `missing_dejargonized_description = 405`
- `missing_references = 159`
- `missing_audit_rollup = 405`

Interpretation:

- most of the remaining atoms are missing the full audited metadata bundle
- this is not mainly a references-only cleanup problem
- the work should be organized around provider-owned audit bundle production,
  not row-by-row Supabase patching

## Workstreams

This plan groups the remaining audit work into five phases:

1. [AUDIT_STATUS_PHASE_1_INVENTORY_AND_GAP_CLASSIFICATION.md](/Users/conrad/personal/sciona-matcher/docs/plans/AUDIT_STATUS_PHASE_1_INVENTORY_AND_GAP_CLASSIFICATION.md)
2. [AUDIT_STATUS_PHASE_2_PROVIDER_AUDIT_BUNDLE_PRODUCTION.md](/Users/conrad/personal/sciona-matcher/docs/plans/AUDIT_STATUS_PHASE_2_PROVIDER_AUDIT_BUNDLE_PRODUCTION.md)
3. [AUDIT_STATUS_PHASE_3_REFERENCES_AND_REVIEW_COMPLETION.md](/Users/conrad/personal/sciona-matcher/docs/plans/AUDIT_STATUS_PHASE_3_REFERENCES_AND_REVIEW_COMPLETION.md)
4. [AUDIT_STATUS_PHASE_4_SUPABASE_INGEST_AND_PUBLICATION_RATCHET.md](/Users/conrad/personal/sciona-matcher/docs/plans/AUDIT_STATUS_PHASE_4_SUPABASE_INGEST_AND_PUBLICATION_RATCHET.md)
5. [AUDIT_STATUS_PHASE_5_BLESSED_AUDIT_BASELINE.md](/Users/conrad/personal/sciona-matcher/docs/plans/AUDIT_STATUS_PHASE_5_BLESSED_AUDIT_BASELINE.md)

## Dependency Structure

### Hard dependencies

- Phase 1 must happen first because it produces the canonical family/provider
  inventory and the target bundles workers are expected to fill.
- Phase 4 depends on meaningful provider outputs from Phases 2 and 3.
- Phase 5 is last by design.

### Soft dependencies

- Phase 2 and Phase 3 can run in parallel after Phase 1.
- Phase 3 can start on provider slices that already have draft audit manifests
  while Phase 2 is still filling missing audit bundles in other families.
- Phase 4 can start on narrow provider slices once a first batch of provider
  audit artifacts is stable; it does not require every family to be finished.

## Parallelization Analysis

The work is highly parallelizable because the missing audit surfaces are mostly
provider-owned.

### Wave 0: Inventory and ratchet definition

Contains:

- Phase 1

Reason:

- one integrator should define the canonical family/provider inventory and the
  publication ratchet rules before provider workers start generating assets

### Wave 1: Provider audit bundle production

Contains:

- Phase 2
- Phase 3

Reason:

- once the inventory is frozen, the provider repos can work independently on
  audit manifests, IO specs, parameter manifests, descriptions, references, and
  review completion

Recommended split:

- Worker A: `sciona-atoms-physics` / `tempo_jl`
- Worker B: `sciona-atoms-fintech` / `quantfin` and
  `institutional_quant_engine`
- Worker C: `sciona-atoms-bio` / `molecular_docking` and `pronto`
- Worker D: `sciona-atoms-robotics` / `rust_robotics`
- Worker E: `sciona-atoms` / `expansion`, `inference`, `state_estimation`
- Worker F: `sciona-atoms-signal` / `signal_processing`
- Worker G: cross-provider reference registry and review normalization

### Wave 2: Supabase ingest and ratchet

Contains:

- Phase 4

Reason:

- one integrator should own the shared seeding/backfill surfaces and the
  publication gate checks

### Wave 3: Blessed audit baseline

Contains:

- Phase 5

Reason:

- one integrator should run the final reset/reseed/rebackfill/export baseline

## Shared Hotspots

Do not assign these files to multiple workers in the same wave:

- [../sciona-atoms/src/sciona/atoms/supabase_backfill.py](</Users/conrad/personal/sciona-atoms/src/sciona/atoms/supabase_backfill.py>)
- [../sciona-atoms/src/sciona/atoms/supabase_seed.py](</Users/conrad/personal/sciona-atoms/src/sciona/atoms/supabase_seed.py>)
- provider `audit_manifest.json` files for the same family
- provider `references.json` and `registry.json` files for the same family
- [sciona/api/snapshot.py](/Users/conrad/personal/sciona-matcher/sciona/api/snapshot.py)
- [scripts/export_manifest.py](/Users/conrad/personal/sciona-matcher/scripts/export_manifest.py)

## Recommended Delivery Order

1. Run Phase 1 and freeze the provider/family target inventory.
2. Run Phases 2 and 3 in parallel across provider repos.
3. Reconcile shared registry and review conventions.
4. Run Phase 4 on the first provider batch and broaden until the non-publishable
   count moves materially.
5. Run Phase 5 and record the new audited baseline.

## Exit Criteria

This plan should be considered complete only when all of the following are
true:

- a substantial fraction of the remaining `405` atoms have real audit bundles
  in provider-owned artifacts
- publishability increases only through those provider audit artifacts, not
  through source-derived fallbacks
- the public manifest atom count rises while still remaining strictly
  audit-backed
- developer mode continues to surface unpublished atoms without changing the
  public publication bar
- one clean local replay documents the new audited baseline and the remaining
  non-publishable inventory is narrow and clearly classified
