# Sciona Matcher

Sciona Matcher is a **catalog-first, verified composition system for algorithm development**. It helps an agent and a human developer turn an objective into a typed Conceptual Dependency Graph (CDG), retrieve proven building blocks, assemble an executable algorithm, evaluate it against real data, and refine the graph without regenerating known work from scratch.

The project is intentionally cross-disciplinary. An atom may be a signal-processing primitive, a statistical estimator, a geometry operation, a financial transform, a robotics component, or a machine-checked theorem. Reusable structures and successful CDGs capture how those atoms have worked together on prior problems. ECG heart-rate detection is a deliberately approachable showcase, not a special case in the architecture.

Sciona separates **discovery** from **installation**. Metadata for atoms, structures, CDGs, and evaluation datasets lives in PostgreSQL and remains searchable even when its provider package is absent. Once a candidate is selected, only the distribution providing that exact fully qualified atom is installed into the environment. Provider repositories contribute to the shared `sciona.atoms` [PEP 420](https://peps.python.org/pep-0420/) namespace, so a catalog can grow to many thousands of discipline-specific atoms without making every developer install all of them up front.

The product website, API, Docker stack, Supabase project, and deployment assets
are being split into the sibling repository `../sciona-infra`. This repository
is the core algorithm-generation and tooling package.

## Project intent

Strong models can write many algorithms directly. Sciona is useful when the cost, reliability, and auditability of repeatedly creating those algorithms matters:

- **Catalog-first reuse.** Search for atoms, structures, and successful CDGs before synthesizing new code. A discipline-specific solution should surface and be used when its evidence fits the objective.
- **Verification-first composition.** Matches are checked through compiler proofs, import and signature checks, contracts, ghost witnesses, or evaluation data as appropriate to the domain.
- **Deterministic-first execution.** Structured parsing, catalog search, type checks, graph assembly, execution, and scoring stay deterministic. Language models are reserved for conceptual ambiguity and graph evolution.
- **Compounding knowledge.** Successful components, graph patterns, provenance, and evaluations become reusable evidence for later problems.
- **Human-guided evolution.** The visualizer exposes intermediate values, losses, graph diffs, and branch history so domain experts can redirect an unpromising search before more agent time is spent.
- **Agent-tool interoperability.** Codex, Claude, agy, or another command-line agent can use Sciona's catalog and runtime as deterministic tools without needing a separate Sciona API key or a second hosted-model call hidden behind the CLI.

The target comparison is therefore not only whether a large model can solve a problem. It is whether a smaller, faster agent can retrieve and compose validated knowledge with fewer tokens, less elapsed time, and a more reproducible result.

## Execution modes

The pipeline supports graduated tiers so the overhead scales with the task:

| Mode | What runs | Best for |
|------|-----------|----------|
| `rapid` | Direct Hunter match, no decomposition | Simple single-predicate lookups |
| `structured` | Architect decomposition + single-pass batch match | Standard tasks with clear decomposition |
| `single_agent` | Deterministic planner with partial acceptance and selective re-decomposition | Most production tasks |
| `verified` | Full multi-round orchestration with Architect refinement | High-correctness tasks |

Set via `--mode <mode>` or `SCIONA_EXECUTION_MODE` in `.env`.

## How it works

The system implements an **Agentic Development Cycle** with four rounds, wrapped by an optional **Principal** meta-optimizer. All rounds follow the **Deterministic -> Agentic -> Deterministic** sandwich pattern.

**Round 0 -- Ingester** (Smart Ingester): parses existing source code (Python, C++, Julia, Rust) into `RawDataFlowGraph` models via language-specific extractors. A control-flow decomposer attempts deterministic AST-based splitting first; the LLM is only used for chunking when confidence is low. Generates `@register_atom` wrappers, Pydantic state models, ghost witnesses, and FFI bindings.

**Round 1 -- Architect** (Conceptual Dependency Agent): decomposes a high-level goal into an atomic Conceptual Dependency Graph (CDG) using LangGraph, with PostgreSQL-backed checkpointing for time-travel and fork/resume.

**Round 2 -- Hunter** (Functional Matching Agent): grounds each atomic CDG leaf into a verified catalog function through:

1. **Catalog retrieval** -- searches PostgreSQL metadata and, when configured, semantic indexes without requiring provider distributions to be installed
2. **Reranking** -- combines semantic similarity, lexical evidence, types, contracts, and provenance; ambiguous cases may be escalated to an agent
3. **Verification Oracle** -- compiler-based checking (Lean 4, Coq) or import-based verification (Python)
4. **Failure Analyzer** -- deterministic regex analysis for known error patterns; LLM fallback for unrecognized errors

**Round 3 -- Synthesizer** (Assembly + Verification): assembles matched atoms into compilable skeleton files, with an optional ghost witness simulation pass. Repair uses a deterministic fix database (type coercions, common imports, syntax corrections) before LLM patching.

The handoff between rounds is validated: every atomic leaf must have a `type_signature` and `description` before conversion to PDG nodes.

**Principal** (Meta-Optimizer): wraps the four rounds in a NAS-style optimisation loop (Forward → Evaluate → Backward → Update). Uses Optuna for HPO, ghost simulation for early pruning, interval-arithmetic precision gradients for credit assignment, and the Architect's checkpoint time-travel for coordinate descent over decomposition structure.

See [ARCH.md](ARCH.md) for the full architecture.

## Installation

```bash
# Core only
pip install -e .

# With Lean 4 support
pip install -e ".[indexer,lean,hunter]"

# With Coq support
pip install -e ".[indexer,coq,hunter]"

# With ghost witness simulation (requires sciona-atoms)
pip install -e ".[ghost]"

# With the human-guided visualizer
pip install -e ".[visualizer]"

# Everything
pip install -e ".[all]"
```

For website/API/infra development, use the sibling `sciona-infra` repository
rather than installing platform dependencies here.

### External requirements

- **Lean 4**: Install [elan](https://github.com/leanprover/elan) and run `lean-explore data fetch` for Mathlib data
- **Coq**: Install via opam with your project's dependencies
- **PostgreSQL**: Recommended for the shared atom/provider/data catalog and required for persistent catalog workflows
- **LLM (optional)**: Catalog search, provider installation, deterministic execution, and UI inspection do not require an LLM API key. Configure a provider only for Sciona commands that perform agent-driven decomposition or refinement:
  - Anthropic: `SCIONA_LLM_PROVIDER=anthropic` + `SCIONA_ANTHROPIC_API_KEY`
  - Codex: `SCIONA_LLM_PROVIDER=codex` + `SCIONA_OPENAI_API_KEY`
  - Local llama.cpp (Hunter default): `SCIONA_HUNTER_LLM_PROVIDER=llama_cpp`

An external CLI agent can own the language reasoning and call Sciona only for deterministic discovery, materialization, execution, and validation. PostgreSQL full-text search is the current zero-key retrieval baseline. Hybrid semantic search is used when compatible catalog embeddings are configured; canonical local catalog embeddings remain planned work and are tracked in [Local agent tooling](docs/LOCAL_AGENT_TOOLING.md).

## Catalog and providers

Atoms are published by independent provider distributions rather than bundled into this repository. Their metadata includes the exact Python symbol, owning distribution, version and integrity information, contracts, provenance, and searchable discipline tags. Searching is side-effect free; installation is an explicit action on one selected candidate.

```bash
# Point the client at a local catalog API backed by the local PostgreSQL stack.
export SCIONA_API_URL=http://127.0.0.1:8000

# Search metadata. No provider package is installed.
sciona catalog search "estimate energy from mass and velocity"

# Materialize only the distribution that owns the selected atom.
sciona catalog install sciona.atoms.physics.mechanics.kinetic_energy
```

Provider workspaces can be audited, published, reconciled, and release-validated with first-class catalog commands:

```bash
sciona catalog audit-providers \
  --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres
sciona catalog publish-providers --workspace-root ../ --apply --ensure-owner
sciona catalog reconcile-providers --workspace-root ../ --strict
sciona catalog validate-provider-release ../sciona-atoms-physics
```

Evaluation data follows the same decoupled model. Atom and CDG packages do not contain mutable dataset URLs. PostgreSQL associates versioned, checksum-pinned artifacts and evaluation contracts with atoms or graph inputs, allowing new public data to be added without changing or rereleasing provider code. The visualizer downloads an artifact only when execution needs it and caches verified content under `~/.cache/sciona/datasets`.

```bash
# Validate a dataset manifest, then persist it when ready.
sciona catalog ingest-dataset dataset.json
sciona catalog ingest-dataset dataset.json --apply \
  --database-url postgresql://postgres:postgres@127.0.0.1:54322/postgres
```

See [Provider catalog runtime](docs/provider_catalog_runtime.md), [Data artifact catalog](docs/DATA_ARTIFACT_CATALOG.md), and [Local agent tooling](docs/LOCAL_AGENT_TOOLING.md) for the schemas and operating model.

## Human-guided visual development

The visualizer makes graph evolution inspectable and steerable rather than presenting only a final generated file:

```bash
sciona visualize --api
# Open the printed URL (http://127.0.0.1:8080 by default).
```

The intended workflow is:

1. **State the objective and provide evaluation data.** Choose catalog-backed inputs whose evaluation contract defines references, named outputs, and the loss to compute. Without representative inputs, versions can execute but cannot be compared meaningfully.
2. **Inspect the initial match.** Open any CDG node to review its contract, provenance, ports, and intermediate inputs and outputs. Execution and repair diagnostics remain attached to the version being examined.
3. **Compare evolution versions.** Initial match, Principal expansion, and later refinements appear as separate tabs with loss, loss delta, and structural changes. Graph diffs identify added, removed, and changed nodes.
4. **Guide refinement.** Select the version to refine, describe the observed problem or desired direction, and create a candidate branch. The proposal is executed and evaluated against the same contract so its effect is measurable.
5. **Continue, reject, or branch.** Continue refining a useful candidate, or reject a direction while preserving its history and returning to its parent. The branch graph modal shows the execution-version DAG and allows any prior version to become a new branch point.

The fixed visualization menu provides layout, fit, reset, and legend controls without competing with graph actions. The toolbar exposes browsing, the guided tutorial and tour, file opening, execution, evaluation inputs, and branch history. Compare and telemetry views support deeper diagnosis, while the repair log records deterministic validation and ghost-witness failures.

The ECG showcase demonstrates this loop with public data, but objective definition, dataset selection, node inspection, scoring, refinement guidance, and branching are generic operations shared by every discipline. See [Visualizer documentation](docs/VISUALIZE.md) and the [cross-disciplinary blind evaluation](docs/CROSS_DISCIPLINARY_BLIND_EVALUATION.md).

## Configuration

Core pipeline settings are read from `.env` (prefixed with `SCIONA_`) via pydantic-settings; catalog and service clients also consume the connection variables shown below:

```bash
# .env
SCIONA_INDEX_DIR=data/index
SCIONA_LLM_PROVIDER=anthropic
SCIONA_ANTHROPIC_API_KEY=sk-ant-...
SCIONA_LLM_MODEL=claude-sonnet-4-5-20250929
# For Codex/OpenAI provider:
# SCIONA_OPENAI_API_KEY=sk-...
# SCIONA_LLM_MODEL=codex-mini-latest
# Hunter local defaults (GBNF + speculative retrieval):
SCIONA_HUNTER_LLM_PROVIDER=llama_cpp
SCIONA_HUNTER_LLM_MODEL=llama-3.1-8b-instruct
SCIONA_LLAMA_CPP_BASE_URL=http://127.0.0.1:8080/v1
SCIONA_HUNTER_MODE=speculative_local
SCIONA_HUNTER_USE_GBNF=true
SCIONA_HUNTER_MAX_ITERATIONS=5

# PostgreSQL persistence (optional -- omit for in-memory only)
# Used by Architect checkpoints, shared context, and telemetry
SCIONA_POSTGRES_URI=postgresql://sciona:sciona_dev@localhost:5433/sciona_architect

# Local provider/data catalog and the catalog API used by CLI search/install
SCIONA_DATA_CATALOG_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
SCIONA_API_URL=http://127.0.0.1:8000

# Telemetry storage: "auto" (Postgres when URI set), "postgres", or "file"
SCIONA_TELEMETRY_BACKEND=auto
```

CLI flags override `.env` when provided. See `sciona/config.py` for all options.

### PostgreSQL setup

PostgreSQL is the durable source for provider and data catalog metadata. It is optional for one-off in-memory decomposition, but recommended for the intended catalog-first workflow. Architect checkpoints, shared context, and pipeline telemetry can use the same service or a separate database. Use any local PostgreSQL instance, or reuse the Docker assets from the sibling `sciona-infra` repository:

```bash
# Default URI: postgresql://sciona:sciona_dev@localhost:5433/sciona_architect
```

Runtime tables are created automatically where supported; catalog schemas are applied by the local infrastructure migrations. Set `SCIONA_TELEMETRY_BACKEND=file` to disable PostgreSQL telemetry even when a URI is configured.

## Usage

### Build an index

```bash
# Index Lean 4 / Mathlib declarations
sciona index build --prover lean4

# Index a Coq project
sciona index build --prover coq --path ./my-coq-project
```

### Decompose a goal (Round 1)

```bash
# Basic decomposition (in-memory checkpointing)
sciona decompose "Implement merge sort" --no-persist --output cdg.json

# Use Codex for Round 1 (override .env)
sciona decompose "Implement merge sort" --llm-provider codex --llm-model codex-mini-latest --no-persist

# With a specific thread ID
sciona decompose "Sort and search" --thread-id my-run-01

# View checkpoint history for a thread
sciona history my-run-01
```

### Optimize a goal (Principal)

```bash
# Run the NAS-style optimisation loop over a benchmark
sciona optimize "Implement merge sort" --benchmark data/bench.json --metric latency --trials 20

# With Codex and a custom timeout
sciona optimize "FFT spectral analysis" --benchmark data/fft_bench.json \
  --metric precision --trials 50 --timeout 300 \
  --llm-provider codex --llm-model codex-mini-latest
```

### Match predicates (Round 2)

```bash
# Single statement
sciona match --statement "forall n m : Nat, n + m = m + n" --prover lean4

# Use Codex for Round 2 (override .env)
sciona match --statement "forall n m : Nat, n + m = m + n" --prover lean4 --llm-provider codex --llm-model codex-mini-latest

# Use local llama.cpp for Round 2 (default if configured in .env)
sciona match --statement "forall n m : Nat, n + m = m + n" --prover lean4 --llm-provider llama_cpp --llm-model llama-3.1-8b-instruct

# Batch from a PDG file
sciona match --pdg-file predicates.json --prover lean4
```

The PDG file is a JSON array:

```json
[
  {
    "predicate_id": "p1",
    "statement": "forall n m : Nat, n + m = m + n",
    "informal_desc": "commutativity of addition"
  }
]
```

## Development

The [cross-disciplinary blind evaluation](docs/CROSS_DISCIPLINARY_BLIND_EVALUATION.md)
exercises catalog retrieval, cold provider installation, deterministic CDG
assembly, hidden correctness checks, PostgreSQL scale, and optional agent
comparison arms against checksum-pinned public datasets.

```bash
pip install -e ".[dev]"
pytest                     # run all tests (skips slow/model-download tests)
pytest -m slow             # run slow tests (requires transformers + torch)
mypy sciona/
```

## Project structure

```
sciona/
  types.py          Shared domain types (Declaration, PDGNode, MatchResult, ...)
  protocols.py      Protocol interfaces (SemanticIndex, ProofEnvironment, ...)
  config.py         AgeomConfig (pydantic-settings, reads .env)
  telemetry.py      Pipeline events, run snapshots, live SSE subscribers
  telemetry_store.py  PostgresTelemetryStore + TelemetryDrain (async Postgres persistence)
  visualizer_api.py FastAPI server for the core CDG visualizer + telemetry dashboard
  cli.py            CLI entrypoint (decompose, history, match, index, assemble, optimize)
  architect/        Round 1 -- Conceptual Dependency Agent
    models.py         CDG node/edge Pydantic models, ConceptType enum (incl. DSP types)
    catalog.py        PrimitiveCatalog (CLRS-30, coq-100-theorems)
    embedder.py       FAISS-based SkillIndex for primitive matching
    skeletons.py      Pre-built graph templates per paradigm (13 templates incl. DSP)
    nodes.py          LangGraph node functions (select_strategy, decompose, critique, ...)
    state.py          DecompositionState TypedDict + DecompositionDeps
    graph.py          StateGraph assembly + DecompositionAgent (with time-travel)
    checkpointer.py   Checkpointer factory (AsyncPostgresSaver / MemorySaver)
    handoff.py        CDGExport, validate_handoff, to_pdg_nodes (Round 1 -> 2 bridge)
    prompts.py        Prompt templates for decomposition LLM calls
    ingest_clrs.py    CLRS-30 ingestion
    ingest_coq100.py  coq-100-theorems ingestion
  indexer/          Semantic Indexer
    embedder.py       UniXcoder embeddings (768-dim, L2-normalized)
    faiss_store.py    FAISS IndexFlatIP vector store
    lean_source.py    Lean 4/Mathlib declarations via lean-explore
    coq_source.py     Coq declarations via coqpyt
    builder.py        IndexBuilder + SemanticIndexImpl
  judge/            Verification Oracle
    lean_env.py       Lean 4 REPL via lean-interact
    coq_env.py        Coq compiler via coqpyt
    checker.py        VerificationOracleImpl (routes by prover)
  hunter/           Round 2 -- Retrieval Agent
    nodes.py          pydantic-graph nodes (search/rank/verify/reformulate cycle)
    graph.py          Graph assembly + HunterAgent
    llm.py            LLM clients (Anthropic Claude + Codex/OpenAI)
    embedding_reranker.py  Cosine similarity + type-token bonus ranking
    failure_analyzer.py    Deterministic regex-based failure analysis
    prompts.py        Prompt templates
  ingester/         Round 0 -- Smart Ingester (multi-language)
    models.py         RawDataFlowGraph, MethodFact, MacroAtomSpec, IngestionBundle, ...
    base_extractor.py BaseExtractor protocol, SourceLanguage enum, EXTENSION_MAP
    python_extractor.py  PythonASTExtractor (wraps ast-based extraction)
    treesitter_extractor.py  TreeSitterExtractor (C++, Julia via tree-sitter)
    extractor.py      Phase 1: Python AST extraction (_SelfAccessVisitor, SSA edges)
    control_flow_decomposer.py  Deterministic AST-based function splitting
    chunker.py        Phase 2: deterministic-first chunking, then LLM fallback
    emitter.py        Phase 3: code generation (@register_atom wrappers, state models, CDG, FFI)
    ffi_emitter.py    FFI binding generation (ctypes for C++/Rust, juliacall for Julia)
    graph.py          IngesterAgent state machine + extractor dispatch (_get_extractor)
    prompts.py        LLM prompt templates for chunker/repair
  services/         Service-layer runtime wrappers
    planner_service.py  SingleAgentPlanner (deterministic decision tree)
    models.py         PlannerPolicy, PlannerState, PlannerRunResult, ...
  principal/        Meta-Optimizer (NAS-style loop over the pipeline)
    models.py         OptimizationMetric, NodeTelemetry, BenchmarkResult, NodeGradient
    evaluator.py      ExecutionSandbox (subprocess benchmark + trace parsing)
    backprop.py       CreditAssigner (per-node gradient computation)
    hpo.py            OptunaManager (early pruning, fANOVA importance)
    graph.py          Principal StateGraph (seed → forward → evaluate → gradients → time_travel)
  synthesizer/      Round 3 -- Assembly + Verification
    pipeline.py       assemble_and_check() orchestration (with ghost simulation pass)
    ghost_sim.py      CDG -> SimNode conversion, run_ghost_simulation()
    assembler.py      CDG + MatchResults -> SkeletonFile (Lean 4 / Coq / Python)
    compiler.py       SkeletonCompiler (wraps ProofEnvironment)
    toposort.py       Kahn's algorithm for dependency ordering
    contracts.py      ContractGenerator with DSP constraint pattern recognition
    classifier.py     Regex error classifier + deterministic fix database
    models.py         AssemblyUnit, GlueEdge, SkeletonFile, AssemblyResult, ...
    repair.py         Compile-analyze-patch repair loop (deterministic-first)
    extractor.py      FFI export and verification certificates
tests/
```

## License

MIT
