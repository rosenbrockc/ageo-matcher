# AGEO-Matcher Memory

## Project Structure
- `sciona/` — main package (architect, hunter, synthesizer, ingester, graph_store, cli)
- `tests/` — pytest suite; mock LLMs route by system prompt keywords (`"paradigm"`, `"sub-nodes"`, `"critic"`)
- `docker-compose.yml` — memgraph-mage + postgres + visualizer
- Sibling repo: `../ageo-atoms/ageoa/biosppy/` — ingested CDG atoms

## Key Patterns
- LLM mocking: `async def complete(system, user)` that checks `system.lower()` for keywords
- DecompositionAgent flow: `select_strategy` → (optional skeleton) → `decompose_node` → `critique` → `advance_node`
- CUSTOM paradigm has NO skeleton → root is DECOMPOSED with no pending children → done immediately
- Paradigms WITH skeletons (SIGNAL_FILTER, DIVIDE_AND_CONQUER, etc.) create PENDING intermediate nodes
- `_AcceptAllCatalog` trick: override `is_atomic()` to always return True, stops decomposition recursion

## Biosppy CDGs in Memgraph
- Upserted per-subdirectory as `biosppy.<subdir>` repos (not flat `biosppy`)
- 12 repos, 105 atoms, 26 decomposed nodes with topo_hashes
- EMG detectors (Solnik/Abbink/Bonato) are `signal_filter` concept_type with 5-7 inputs
- `upsert_cdg.py` uses non-recursive glob; subdirs need individual upsert calls

## Completed Work
- E2E test `tests/test_retrieval_e2e_hodges.py` — all 11 passing
- Fix: bypassed `select_strategy`/skeleton by calling `decompose_node` directly with pre-built state (Option D)
- Root cause: `_AcceptAllCatalog` marks all skeleton nodes ATOMIC → empty pending queue → done immediately

## Heuristic Layer Status
- Shared heuristic system is now implemented across `ageo-matcher` and `../ageo-atoms`
- Canonical shared heuristic registry lives in `../ageo-atoms/data/heuristics/canonical_registry.json`
- Family heuristic registries now live in `../ageo-atoms/data/heuristics/families/`
  - `signal_event_rate.json`
  - `divide_and_conquer.json`
  - `sequential_filter.json`
- Atom-side heuristic metadata now lives in `../ageo-atoms/ageoa/**/heuristic_metadata.json`
- Metadata format now supports multi-record files via `{"records": [...]}` so one ingested family directory can describe several heuristic-producing atoms
- Current live examples:
  - `ageoa.biosppy.ecg_zz2018_d12.assemblezz2018sqi`
  - `ageoa.biosppy.ecg_zz2018.calculatecompositesqi_zz2018`
  - `ageoa.biosppy.ecg_zz2018.calculatefrequencypowersqi`
  - `ageoa.biosppy.ecg_zz2018.calculatebeatagreementsqi`
  - `ageoa.kalman_filters.filter_rs.evaluatemeasurementoracle`
- `ageo-matcher` now loads heuristic registries and atom heuristic metadata from sibling `../ageo-atoms` with external-first precedence
- Important matcher compatibility fixes already made:
  - default heuristic asset root now correctly points at sibling `../ageo-atoms`
  - shared family registry assets using `heuristic_bindings` are normalized into matcher-side `entries`
  - external atom heuristic metadata supports multi-record files

## ECG HR Status
- Focused ECG HR optimization on a private five-minute holdout now completes cleanly
- Baseline biosppy chain:
  - `bandpass_filter -> r_peak_detection -> heart_rate_computation`
  - loss around `8.862357815188158`
- Expansion plus heuristic evidence is now real, not just scaffolded
- The materially useful enrichment so far is `insert_outlier_rejection_after_detection`
  - binds to `ageoa.biosppy.ecg.reject_outlier_intervals`
  - improved focused ECG loss to about `7.958128037119418`
- The stronger current enrichment is now `insert_outlier_rejection_after_detection_median_smoothed`
  - proposal is structurally reachable from the plain `detect -> rate` scaffold
  - binds to:
    - `ageoa.biosppy.ecg.reject_outlier_intervals`
    - `ageoa.biosppy.ecg.heart_rate_computation_median_smoothed`
  - latest successful rerun:
    - output dir: `output/principal_optimize_20260410_123651`
    - baseline trial 1: `8.862357815188158`
    - selected expansion trial 2: `7.741363964117423`
    - proposal selection result in `trial_history.json` is `selected=expansion`, `selected_reason=best_admissible_improvement`
- Earlier jump-removal-only enrichment improved very little and confirmed that weak enrichment assets are a bigger bottleneck than proposal routing alone
- SQI existed as an expansion option before, but did not fire because the runtime heuristic/evidence layer was too weak and mismatched to the observed failure mode
- Key lesson: heuristics need to be first-class, de-jargonized, and cross-family; expansions should consume heuristic evidence, not only raw telemetry or domain-specific ad hoc rules
- Important recent bug fixes that enabled the median-smoothed win:
  - cohort reporting now records score-excluded labels, not only guidance-excluded labels
  - expansion rules can now propose a robust smoothed terminal measure stage directly from the baseline scaffold
  - `ageo-atoms` now has `ageoa.biosppy.ecg.heart_rate_computation_median_smoothed`
  - graph rewriter parent/depth inference now considers newly appended RHS nodes, so terminal rewrite nodes become root children and are emitted by the synthesizer composition
- Important failure artifact to remember:
  - earlier failed rerun `output/principal_optimize_20260410_110754`
  - median-smoothed proposal was generated but scored as `1e12` because the synthesizer composition returned `reject_outlier_intervals_result` instead of the final robust-smoothed rate node
  - this was fixed by the graph rewriter parent/child propagation patch, not by changing heuristics

## Federation / Namespace Risk
- Current abstractions are mostly helping rather than hurting the future `sciona.atoms.*` direction
- The main rename debt is not in the heuristic meaning layer; it is in package/import/path assumptions
- Hard-coded `ageoa` assumptions still exist in matcher runtime/import/discovery code
- Future goal is not just “rename `ageoa` to `sciona.atoms`”
- Real target is a federated provider model where multiple repos can supply assets and atoms under a common logical namespace
  - examples: `sciona.atoms.physics`, `sciona.atoms.fintech`
- Important future requirement:
  - separate logical atom identity from import FQDN and from asset repository path
- A dedicated architecture plan for PEP 420 plus federated atom providers is needed before too much more package-aware logic accumulates

## Memory Index
- [session_handoff_20260412_namespace_status.md](session_handoff_20260412_namespace_status.md) — Fresh-session handoff for the current namespace migration state, latest commits, and the recommended next execution step
- [atoms_repo_placement_approval_matrix.md](atoms_repo_placement_approval_matrix.md) — Proposed approval matrix for where current atom families and matcher-owned structural assets should live across `sciona-atoms*` repos
- [atoms_repo_high_level_migration_plan.md](atoms_repo_high_level_migration_plan.md) — High-level migration plan for splitting the default/general provider from discipline-specific atom repos, including heuristics sequencing guidance
- [sciona_atoms_general_detailed_migration_plan.md](sciona_atoms_general_detailed_migration_plan.md) — Detailed repo-specific migration plan for the default/general `sciona-atoms` provider
- [sciona_atoms_signal_detailed_migration_plan.md](sciona_atoms_signal_detailed_migration_plan.md) — Detailed repo-specific migration plan for `sciona-atoms-signal`
- [sciona_atoms_bio_detailed_migration_plan.md](sciona_atoms_bio_detailed_migration_plan.md) — Detailed repo-specific migration plan for `sciona-atoms-bio`
- [sciona_atoms_fintech_detailed_migration_plan.md](sciona_atoms_fintech_detailed_migration_plan.md) — Detailed repo-specific migration plan for `sciona-atoms-fintech`
- [sciona_atoms_ml_detailed_migration_plan.md](sciona_atoms_ml_detailed_migration_plan.md) — Detailed repo-specific migration plan for `sciona-atoms-ml`
- [sciona_atoms_physics_detailed_migration_plan.md](sciona_atoms_physics_detailed_migration_plan.md) — Detailed repo-specific migration plan for `sciona-atoms-physics`
- [sciona_atoms_robotics_detailed_migration_plan.md](sciona_atoms_robotics_detailed_migration_plan.md) — Detailed repo-specific migration plan for `sciona-atoms-robotics`
- [runtime_probes_phaseA_implementation_plan.md](runtime_probes_phaseA_implementation_plan.md) — Detailed Phase A plan for finishing the local runtime-probe split and stabilizing the shared core boundary
- [runtime_probes_phaseB_parallel_remediation_plan.md](runtime_probes_phaseB_parallel_remediation_plan.md) — Detailed Phase B plan for parallel family remediation using disjoint probe-plan ownership
- [runtime_probes_phaseC_namespace_packaging_plan.md](runtime_probes_phaseC_namespace_packaging_plan.md) — Detailed Phase C plan for the long-term `sciona.probes.<domain>` namespace and provider contract
- [runtime_probes_phaseD_migration_plan.md](runtime_probes_phaseD_migration_plan.md) — Detailed Phase D migration plan from the local scripts registry to provider-owned probe registries
- [pep420_phase0_namespace_contract_plan.md](pep420_phase0_namespace_contract_plan.md) — Detailed Phase 0 plan for the namespace contract, provider identity, and compatibility seams
- [pep420_phase1_sequential_filter_pilot_plan.md](pep420_phase1_sequential_filter_pilot_plan.md) — Detailed Phase 1 pilot-family plan using `sequential_filter`
- [pep420_phase2_packetization_plan.md](pep420_phase2_packetization_plan.md) — Detailed Phase 2 packetization and worker-ownership plan for parallel family migrations
- [pep420_phase3_parallel_waves_plan.md](pep420_phase3_parallel_waves_plan.md) — Detailed Phase 3 plan for provisional parallel migration waves after packetization
- [pep420_phase4_integration_and_legacy_narrowing_plan.md](pep420_phase4_integration_and_legacy_narrowing_plan.md) — Detailed Phase 4 integration plan for mixed legacy and namespace providers
- [pep420_family_migration_execution_plan.md](pep420_family_migration_execution_plan.md) — Phased PEP 420 / federated-provider execution plan for migrating family skeletons and assets into provider-owned CDGs and audited atoms
- [project_expansion_family_analysis.md](project_expansion_family_analysis.md) — Ranked analysis of which skeleton paradigms should get expansion rules next
- [plan_mcmc_expansion.md](plan_mcmc_expansion.md) — Approved implementation plan for MCMC/HMC expansion rules (ready to implement)
- [project_platform_architecture.md](project_platform_architecture.md) — Full platform architecture: frontend, backend, infra, business model
- [feedback_test_speed.md](feedback_test_speed.md) — User expects test suite under 1 minute; investigate if slow
