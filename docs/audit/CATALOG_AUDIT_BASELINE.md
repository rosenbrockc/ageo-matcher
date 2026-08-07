# Catalog Audit Baseline

## Status

This baseline was regenerated on August 7, 2026 from a disposable Supabase
project using all ten sibling `sciona-atoms*` repositories. The run applied the
real migration tree, seeded the federated provider inventory, and executed all
file-backed catalog backfills.

Regenerate the baseline without building provider wheels or running the full
execution acceptance test:

```bash
.venv/bin/python scripts/run_provider_staging_e2e.py \
  --audit-only \
  --audit-output /tmp/sciona-catalog-audit.json
```

Audit an already seeded database directly:

```bash
sciona catalog audit-providers \
  --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

## Policy

An atom is audit-ready only when it has:

- a non-empty technical description
- IO specifications
- a parameter contract, including an explicit provider declaration when the
  parameter set is empty
- an English dejargonized description below the jargon threshold
- at least one reference
- an audit rollup
- approved semantic and developer-semantics review
- a trust-ready status
- no broken or misleading overall verdict

The staging test fails if any served atom does not satisfy this policy. Audit
gaps remain seeded for operator visibility but are not served as candidates.

## Current Baseline

- Seeded atoms: `3,380`
- Audit-ready and served atoms: `2,604`
- Audit gaps: `776`
- Served atoms that are not audit-ready: `0`
- Provider repositories: `10`
- Metadata identifiers reconciled exactly: `7,857`
- Unresolved metadata identifiers: `0`

| Provider | Seeded | Audit-ready | Gap |
| --- | ---: | ---: | ---: |
| `sciona-atoms-ml` | 2,327 | 1,984 | 343 |
| `sciona-atoms` | 360 | 212 | 148 |
| `sciona-atoms-dl` | 136 | 33 | 103 |
| `sciona-atoms-signal` | 168 | 75 | 93 |
| `sciona-atoms-physics` | 103 | 93 | 10 |
| `sciona-atoms-robotics` | 52 | 39 | 13 |
| `sciona-atoms-cs` | 50 | 2 | 48 |
| `sciona-atoms-bio` | 84 | 72 | 12 |
| `sciona-atoms-geo` | 20 | 16 | 4 |
| `sciona-atoms-fintech` | 80 | 78 | 2 |
| **Total** | **3,380** | **2,604** | **776** |

Marginal blocker counts are:

- missing audit rollup: `0`
- missing technical description: `342`
- missing dejargonized description: `342`
- missing parameter contract: `0`
- trust review not ready: `433`
- missing IO specifications: `0`
- missing references: `26`

These counts overlap; one atom can have several blockers.

## Known Provider Test Debt

The ML provider currently reports `5,829` passing, `308` failing, and `5`
skipped tests under the matcher virtual environment. Most failures are stale
tests that expect globally ambiguous leaf names in the atom registry, while the
runtime intentionally uses module-qualified identities. The remaining failures
include scikit-learn private-API/version drift. Do not restore bare-name registry
semantics to make this suite green; migrate those tests family by family and
validate behavior against the supported scikit-learn version.

The physics provider currently reports `151` passing and `49` failing tests.
Those failures are stale tests and publication fixtures that address the
symbolic registry by legacy bare names, while the shared registry now uses
collision-safe module-qualified identities. Strict catalog reconciliation is
still exact. Migrate the tests and fixtures without adding bare-name aliases to
the live registry.

## Work Order

Completed in this audit pass:

- normalized all `137` legacy review bundles
- resolved all `25` stale provider identifiers
- added explicit pending records for the `27` previously unrepresented seeded atoms
- made publication fail when the seed and audit inventories differ
- preserved the serving gate: no atom is published without audit readiness

Remaining work should proceed by evidence-complete families:

1. Exercise the newly served CS, geospatial, and robotics slices through
   retrieval, cold installation, and deterministic behavior checks.
2. Close the two-atom fintech tail and the four-atom geospatial tail without
   inventing missing reference or semantic evidence.
3. Expand deep-learning and CS coverage by complete behavior-tested families,
   not isolated rows.
4. Add technical and dejargonized descriptions for the `301` ML rows that lack
   both, using source behavior and focused tests rather than generated filler.
5. Complete remaining core, signal, physics, bio, and ML families selected by
   observed local problem failures.

Every local problem trial must record which candidates were considered, which
audit-ready FQDNs were selected, what was installed, and whether deterministic
execution validated the intended behavior. Retrieval benchmark cases should be
added from observed misses, not constructed around whichever atom currently
passes.
