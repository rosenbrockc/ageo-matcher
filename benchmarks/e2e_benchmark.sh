#!/usr/bin/env bash
# e2e_benchmark.sh — Compare the full sciona pipeline against a raw LLM
# on real-world goals. Measures: leaf coverage, match quality, latency.
set -uo pipefail  # no -e: individual runs may fail without aborting the whole benchmark

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CALL_DIR="$(pwd)"
cd "$REPO_ROOT"

GOAL_CONFIG="${1:-}"
if [ -n "$GOAL_CONFIG" ] && [ ! -f "$GOAL_CONFIG" ] && [ -f "$CALL_DIR/$GOAL_CONFIG" ]; then
    GOAL_CONFIG="$CALL_DIR/$GOAL_CONFIG"
fi

OUTPUT_DIR="output/e2e_benchmark_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"
BENCHMARK_PYTHON="${E2E_PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [ ! -x "$BENCHMARK_PYTHON" ]; then
    BENCHMARK_PYTHON="$(command -v python3)"
fi

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BOLD}[e2e]${NC} $*"; }
ok()    { echo -e "${GREEN}[pass]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# A goal config is required so no discipline is a hidden benchmark default.
if [ -n "$GOAL_CONFIG" ] && [ -f "$GOAL_CONFIG" ]; then
    _goal_config="$GOAL_CONFIG"
    GOAL=$("$BENCHMARK_PYTHON" -c "import yaml, sys; print(yaml.safe_load(open('$_goal_config'))['goal'])")
    PROVER=$("$BENCHMARK_PYTHON" -c "import yaml, sys; print(yaml.safe_load(open('$_goal_config')).get('prover', 'python'))")
    _gt_json=$("$BENCHMARK_PYTHON" -c "import yaml, json, sys; print(json.dumps(yaml.safe_load(open('$_goal_config')).get('ground_truth_patterns', [])))")
    EVAL_SPEC_PATH=$("$BENCHMARK_PYTHON" -c "import os, yaml; data=yaml.safe_load(open('$_goal_config')) or {}; p=data.get('evaluation_spec_file') or ''; print(os.path.abspath(os.path.join(os.path.dirname('$_goal_config'), p)) if p else '')")
    PROFILE_METRIC=${E2E_PROFILE_METRIC:-$("$BENCHMARK_PYTHON" -c "import yaml; data=yaml.safe_load(open('$_goal_config')) or {}; print(data.get('optimization_metric') or 'precision')")}
    info "Loaded goal config from $_goal_config"
else
    fail "A YAML goal config is required (for example e2e_goals/shortest_path.yml)"
    exit 2
fi
export LLM_PROVIDER="${E2E_LLM_PROVIDER:-codex_shim}"
export LLM_MODEL="${E2E_LLM_MODEL:-gpt-5.3-codex}"

# Force all LLM routing through the chosen provider to avoid stale shim
# sockets from other providers (e.g. claude_shim set in .env).
export SCIONA_LLM_PROVIDER="$LLM_PROVIDER"
export SCIONA_LLM_MODEL="$LLM_MODEL"
export SCIONA_HUNTER_LLM_PROVIDER="$LLM_PROVIDER"
export SCIONA_HUNTER_LLM_MODEL="$LLM_MODEL"
export SCIONA_ARCHITECT_LLM_PROVIDER="$LLM_PROVIDER"
export SCIONA_ARCHITECT_LLM_MODEL="$LLM_MODEL"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [[ "$LLM_PROVIDER" == *_cli ]]; then
    export SCIONA_ALLOW_LEGACY_SUBPROCESS_PROVIDERS="${E2E_ALLOW_LEGACY_SUBPROCESS:-true}"
fi

PROMPT_OVERRIDE_KEYS=(
    ARCHITECT_STRATEGY
    ARCHITECT_DECOMPOSE
    ARCHITECT_CRITIQUE
    HUNTER_SCORE
    HUNTER_REFORMULATE
    HUNTER_ANALYZE_FAILURE
    SYNTHESIZER_REPAIR
    SYNTHESIZER_TACTIC
    INGESTER_CHUNK
    INGESTER_HOIST_STATE
    INGESTER_ABSTRACT
    INGESTER_FIX_TYPE
    INGESTER_FIX_GHOST
    INGESTER_OPAQUE_WITNESS
    INGESTER_FIX_MESSAGE_CYCLE
    INGESTER_DECOMPOSE
    ORCHESTRATOR_REFINE
)
for key in "${PROMPT_OVERRIDE_KEYS[@]}"; do
    export "SCIONA_${key}_LLM_PROVIDER=$LLM_PROVIDER"
    export "SCIONA_${key}_LLM_MODEL=$LLM_MODEL"
done

# When E2E_GENERIC_ONLY=true, disable phrase rules so the benchmark exercises
# the generic keyword/embedding path only.
if [[ "$(printf '%s' "${E2E_GENERIC_ONLY:-}" | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
    export SCIONA_DISABLE_PHRASE_RULES=1
    info "Generic-only mode: SCIONA_DISABLE_PHRASE_RULES=1"
fi

# Exercise the deployed Postgres/pgvector retrieval path. Offline FAISS runs
# are separate diagnostics and must opt in explicitly.
export SCIONA_SEMANTIC_INDEX_BACKEND="${E2E_SEMANTIC_INDEX_BACKEND:-postgres}"
MODE_TIMEOUT_S="${E2E_MODE_TIMEOUT_S:-240}"
RAW_TIMEOUT_S="${E2E_RAW_TIMEOUT_S:-120}"
INCLUDE_SYNTHESIS="${E2E_INCLUDE_SYNTHESIS:-true}"
SYNTH_TIMEOUT_S="${E2E_SYNTH_TIMEOUT_S:-240}"
EXPORT_TIMEOUT_S="${E2E_EXPORT_TIMEOUT_S:-120}"
PROFILE_TIMEOUT_S="${E2E_PROFILE_TIMEOUT_S:-180}"
export SCIONA_EVALUATOR_TIMEOUT_S="${E2E_EVALUATOR_TIMEOUT_S:-$PROFILE_TIMEOUT_S}"
DEFAULT_PROFILE_DATASET="$HOME/.happy/resources/synced/hpy-templated-datasets/NIGHTCAP/sciona.yml"
if [ ! -f "$DEFAULT_PROFILE_DATASET" ]; then
    for candidate in \
        "$HOME/.happy/resources/synced/hpy-templated-datasets/NIGHTCAP/ageom.yml" \
        "$HOME/.happy/resources/synced/hpy-templated-datasets/NIGHTCAP/adapter.yml"
    do
        if [ -f "$candidate" ]; then
            DEFAULT_PROFILE_DATASET="$candidate"
            break
        fi
    done
fi
PROFILE_DATASET="${E2E_PROFILE_DATASET:-$DEFAULT_PROFILE_DATASET}"
PROFILE_DATASET_VARS="${E2E_PROFILE_DATASET_VARS:-}"
if [ -z "$PROFILE_DATASET_VARS" ] && [ -f "$PROFILE_DATASET" ] && rg -Fq '$(tracker)' "$PROFILE_DATASET"; then
    PROFILE_DATASET_VARS="tracker=single"
fi
export E2E_PROFILE_DATASET_VARS="$PROFILE_DATASET_VARS"
export MPLCONFIGDIR="${E2E_MPLCONFIGDIR:-/tmp/sciona-mplcfg}"
export SCIONA_PYTHON_PATH="${E2E_PROOF_PYTHON:-$BENCHMARK_PYTHON}"
export PYTHON_JULIACALL_INIT="${E2E_PYTHON_JULIACALL_INIT:-no}"
DEFAULT_JULIA_DEPOT="/tmp/sciona-julia-depot"
if [[ "$(printf '%s' "${E2E_USE_HOME_JULIA_DEPOT:-}" | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
    DEFAULT_JULIA_DEPOT="$HOME/.julia"
fi
export JULIA_DEPOT_PATH="${E2E_JULIA_DEPOT_PATH:-$DEFAULT_JULIA_DEPOT}"
mkdir -p "$MPLCONFIGDIR" "$JULIA_DEPOT_PATH"
if [ "$JULIA_DEPOT_PATH" = "/tmp/sciona-julia-depot" ]; then
    info "Julia depot: $JULIA_DEPOT_PATH (isolated default; set E2E_USE_HOME_JULIA_DEPOT=true or E2E_JULIA_DEPOT_PATH=... to override)"
else
    info "Julia depot: $JULIA_DEPOT_PATH (override)"
fi
info "Matplotlib config: $MPLCONFIGDIR"
info "Python proof/runtime: $SCIONA_PYTHON_PATH"
info "Python juliacall init: $PYTHON_JULIACALL_INIT"
if [ -n "$PROFILE_DATASET_VARS" ]; then
    info "Profile dataset vars: $PROFILE_DATASET_VARS"
fi
if [ -n "$EVAL_SPEC_PATH" ]; then
    info "Profile evaluation spec: $EVAL_SPEC_PATH"
fi
info "Profile objective: $PROFILE_METRIC"

# Ground truth: essential atoms that should appear in matched function names.
if [ -n "$_gt_json" ]; then
    # Parse from YAML config
    _gt_count=$("$BENCHMARK_PYTHON" -c "import json; print(len(json.loads('$_gt_json')))")
    GROUND_TRUTH_PATTERNS=()
    for _i in $(seq 0 $((_gt_count - 1))); do
        GROUND_TRUTH_PATTERNS+=("$("$BENCHMARK_PYTHON" -c "import json; print(json.loads('$_gt_json')[$_i])")")
    done
else
    fail "Goal config must declare ground_truth_patterns"
    exit 2
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
elapsed_ms() {
    local start="$1" end="$2"
    "$BENCHMARK_PYTHON" -c "print(int(($end - $start) * 1000))"
}

run_logged_command() {
    local label="$1"
    local log_path="$2"
    local timeout_s="$3"
    local ms_var="$4"
    local rc_var="$5"
    shift 5

    local _start _end _elapsed _rc _timed_out _pid _deadline _now
    _start=$("$BENCHMARK_PYTHON" -c "import time; print(time.time())")
    (
        "$@" 2>&1
    ) | tee "$log_path" &
    _pid=$!
    _rc=0
    _timed_out=0
    _deadline=$("$BENCHMARK_PYTHON" -c "import time; print(time.time() + float('$timeout_s'))")
    while kill -0 "$_pid" 2>/dev/null; do
        _now=$("$BENCHMARK_PYTHON" -c "import time; print(time.time())")
        if "$BENCHMARK_PYTHON" -c "import sys; sys.exit(0 if float('$_now') < float('$_deadline') else 1)"; then
            sleep 1
            continue
        fi
        _timed_out=1
        warn "$label exceeded ${timeout_s}s; terminating benchmark wrapper"
        kill "$_pid" 2>/dev/null || true
        sleep 2
        kill -9 "$_pid" 2>/dev/null || true
        break
    done
    if [ "$_timed_out" -eq 0 ]; then
        wait "$_pid"
        _rc=$?
    else
        wait "$_pid" 2>/dev/null || true
        _rc=124
    fi
    _end=$("$BENCHMARK_PYTHON" -c "import time; print(time.time())")
    _elapsed=$(elapsed_ms "$_start" "$_end")

    printf -v "$ms_var" '%s' "$_elapsed"
    printf -v "$rc_var" '%s' "$_rc"
}

check_ground_truth() {
    local matches_json="$1"
    local label="$2"
    local hit=0
    local miss=0
    local missed_patterns=()

    for pattern in "${GROUND_TRUTH_PATTERNS[@]}"; do
        if "$BENCHMARK_PYTHON" -c "
import json, re, sys
matches = json.load(open('$matches_json'))
names = []
for m in matches:
    vm = m.get('verified_match')
    if vm and vm.get('verified'):
        decl = vm.get('candidate', {}).get('declaration', {})
        names.append(decl.get('name', ''))
    if '$label' == 'raw_llm':
        for c in m.get('all_candidates', []):
            names.append(c.get('declaration', {}).get('name', ''))
pattern = r'$pattern'
found = any(re.search(pattern, n, re.IGNORECASE) for n in names)
sys.exit(0 if found else 1)
" 2>/dev/null; then
            hit=$((hit + 1))
        else
            miss=$((miss + 1))
            missed_patterns+=("$pattern")
        fi
    done

    local total=${#GROUND_TRUTH_PATTERNS[@]}
    echo -e "  ${BOLD}$label${NC}: $hit/$total ground truth atoms found"
    if [ "$miss" -gt 0 ]; then
        warn "$label: missing patterns: ${missed_patterns[*]}"
    else
        ok "$label: all ground truth atoms covered"
    fi
    echo "$hit" > "$OUTPUT_DIR/${label}_hits.txt"
}

# run_pipeline MODE OUTPUT_DIR — runs sciona run, captures latency, tolerates failure
# Uses the project interpreter to invoke sciona.cli.main directly (avoids sandbox issues with
# native library loading when using the sciona entry point script).
run_pipeline() {
    local mode="$1"
    local out_dir="$2"
    local ms_var="${3}"  # name of variable to set with elapsed ms
    mkdir -p "$out_dir"

    info "Running pipeline ($mode): $GOAL"
    local elapsed rc
    run_logged_command \
        "$mode" \
        "$out_dir/stdout.txt" \
        "$MODE_TIMEOUT_S" \
        elapsed \
        rc \
        "$BENCHMARK_PYTHON" -c "
import sys; sys.argv = ['sciona', 'run', '$GOAL', '--prover', '$PROVER', '--mode', '$mode', '--llm-provider', '$LLM_PROVIDER', '--llm-model', '$LLM_MODEL', '--output', '$out_dir']
from sciona.cli import main; main()
"

    # Export latency for caller
    eval "$ms_var=$elapsed"

    if [ "$rc" -eq 124 ] && [ -f "$out_dir/matches.json" ]; then
        warn "$mode timed out after ${elapsed}ms but produced matches.json"
    elif [ "$rc" -ne 0 ]; then
        fail "$mode crashed (exit $rc) after ${elapsed}ms — see $out_dir/stdout.txt"
    else
        info "  $mode completed in ${elapsed}ms"
    fi
    return "$rc"
}

run_pipeline_postprocess() {
    local mode="$1"
    local mode_dir="$2"
    local synth_out="$mode_dir/verified.py"
    local export_dir="$mode_dir/export_python_pkg"
    local post_json="$mode_dir/postprocess.json"
    local synth_log="$mode_dir/synthesize.stdout.txt"
    local export_log="$mode_dir/export.stdout.txt"
    local profile_log="$mode_dir/profile.stdout.txt"
    local synth_ms=0
    local synth_rc=99
    local export_ms=0
    local export_rc=99
    local profile_ms=0
    local profile_rc=99
    local synth_exists=false
    local synth_compiled_ok=false
    local export_exists=false
    local profile_has_gradients=false
    local profile_attempted=false

    if [ ! -f "$mode_dir/cdg.json" ] || [ ! -f "$mode_dir/matches.json" ]; then
        warn "$mode postprocess skipped: missing cdg.json or matches.json"
        cat > "$post_json" <<EOF
{
  "attempted": true,
  "mode": "$mode",
  "dataset": "$PROFILE_DATASET",
  "skipped": "missing_artifacts"
}
EOF
        return
    fi

    info "Running synthesize ($mode)"
    run_logged_command \
        "synthesize:$mode" \
        "$synth_log" \
        "$SYNTH_TIMEOUT_S" \
        synth_ms \
        synth_rc \
        "$BENCHMARK_PYTHON" -c "
import sys; sys.argv = ['sciona', 'synthesize', '$mode_dir/cdg.json', '$mode_dir/matches.json', '--prover', '$PROVER', '--mode', '$mode', '--llm-provider', '$LLM_PROVIDER', '--llm-model', '$LLM_MODEL', '--output', '$synth_out']
from sciona.cli import main; main()
"
    if [ -f "$synth_out" ]; then
        synth_exists=true
    fi
    if [ -f "$synth_log" ] && rg -q "Compiled OK: True" "$synth_log"; then
        synth_compiled_ok=true
    fi
    if [ "$synth_rc" -eq 0 ]; then
        info "  synthesize ($mode) completed in ${synth_ms}ms"
    elif [ "$synth_rc" -eq 124 ] && [ "$synth_exists" = true ]; then
        warn "synthesize ($mode) timed out after ${synth_ms}ms but produced verified.py"
    else
        fail "synthesize ($mode) failed (exit $synth_rc) after ${synth_ms}ms"
    fi

    if [ "$synth_exists" = true ]; then
        info "Running export ($mode)"
        run_logged_command \
            "export:$mode" \
            "$export_log" \
            "$EXPORT_TIMEOUT_S" \
            export_ms \
            export_rc \
            "$BENCHMARK_PYTHON" -c "
import sys; sys.argv = ['sciona', 'export', '$synth_out', '--target', 'python-pkg', '--prover', '$PROVER', '--output-dir', '$export_dir']
from sciona.cli import main; main()
"
        if [ -d "$export_dir" ]; then
            export_exists=true
        fi
        if [ "$export_rc" -eq 0 ]; then
            info "  export ($mode) completed in ${export_ms}ms"
        elif [ "$export_rc" -eq 124 ] && [ "$export_exists" = true ]; then
            warn "export ($mode) timed out after ${export_ms}ms but produced output"
        else
            fail "export ($mode) failed (exit $export_rc) after ${export_ms}ms"
        fi
    fi

    if [ -f "$PROFILE_DATASET" ] && [ "$synth_exists" = true ]; then
        profile_attempted=true
        info "Running profile ($mode)"
        run_logged_command \
            "profile:$mode" \
            "$profile_log" \
            "$PROFILE_TIMEOUT_S" \
            profile_ms \
            profile_rc \
            "$BENCHMARK_PYTHON" -c "
import os, sys
argv = ['sciona', 'profile', '--cdg', '$mode_dir/cdg.json', '--artifact', '$synth_out', '--dataset', '$PROFILE_DATASET', '--metric', '$PROFILE_METRIC']
for item in filter(None, os.environ.get('E2E_PROFILE_DATASET_VARS', '').split(',')):
    argv.extend(['--dataset-var', item])
if '$EVAL_SPEC_PATH':
    argv.extend(['--eval-spec', '$EVAL_SPEC_PATH'])
sys.argv = argv
from sciona.cli import main; main()
"
        if [ -f "$profile_log" ] && rg -q "=== Profiling Results ===" "$profile_log"; then
            profile_has_gradients=true
        fi
        if [ "$profile_rc" -eq 0 ]; then
            info "  profile ($mode) completed in ${profile_ms}ms"
        else
            fail "profile ($mode) failed (exit $profile_rc) after ${profile_ms}ms"
        fi
    elif [ ! -f "$PROFILE_DATASET" ]; then
        warn "profile ($mode) skipped: dataset not found at $PROFILE_DATASET"
    fi

    cat > "$post_json" <<EOF
{
  "attempted": true,
  "mode": "$mode",
  "dataset": "$PROFILE_DATASET",
  "synthesize": {
    "latency_ms": $synth_ms,
    "exit_code": $synth_rc,
    "output_path": "$synth_out",
    "output_exists": $synth_exists,
    "compiled_ok": $synth_compiled_ok
  },
  "export": {
    "latency_ms": $export_ms,
    "exit_code": $export_rc,
    "output_dir": "$export_dir",
    "output_exists": $export_exists
  },
  "profile": {
    "attempted": $profile_attempted,
    "latency_ms": $profile_ms,
    "exit_code": $profile_rc,
    "has_gradients": $profile_has_gradients
  }
}
EOF
}

# ---------------------------------------------------------------------------
# 1. Pipeline: rapid mode
# ---------------------------------------------------------------------------
RAPID_DIR="$OUTPUT_DIR/pipeline_rapid"
RAPID_MS=0
run_pipeline rapid "$RAPID_DIR" RAPID_MS || true

# ---------------------------------------------------------------------------
# 2. Pipeline: structured mode
# ---------------------------------------------------------------------------
STRUCTURED_DIR="$OUTPUT_DIR/pipeline_structured"
STRUCTURED_MS=0
run_pipeline structured "$STRUCTURED_DIR" STRUCTURED_MS || true

# ---------------------------------------------------------------------------
# 3. Pipeline: verified mode
# ---------------------------------------------------------------------------
VERIFIED_DIR="$OUTPUT_DIR/pipeline_verified"
VERIFIED_MS=0
run_pipeline verified "$VERIFIED_DIR" VERIFIED_MS || true

# ---------------------------------------------------------------------------
# 4. Raw LLM baseline — single-shot function identification
# ---------------------------------------------------------------------------
info "Running raw LLM baseline: $LLM_PROVIDER"
RAW_DIR="$OUTPUT_DIR/raw_llm"
export RAW_DIR
mkdir -p "$RAW_DIR"

# Build function list from index (declarations stored in msgpack)
"$BENCHMARK_PYTHON" -c "
import msgpack
from pathlib import Path

msgpack_path = Path('data/index/declarations.msgpack')
if not msgpack_path.exists():
    print('ERROR: index declarations.msgpack not found — run sciona index build first')
    exit(1)

with open(msgpack_path, 'rb') as f:
    data = msgpack.unpackb(f.read(), raw=False, strict_map_key=False)

names = []
for _id, entry in sorted(data.items(), key=lambda x: int(x[0])):
    name = entry.get('name', '')
    sig = entry.get('type_signature', '')
    if name:
        line = f'{name} : {sig}' if sig else name
        names.append(line)

with open('$RAW_DIR/function_list.txt', 'w') as f:
    f.write('\n'.join(names))
print(f'Exported {len(names)} function signatures')
" 2>&1

FUNC_COUNT=$(wc -l < "$RAW_DIR/function_list.txt" | tr -d ' ')
info "  $FUNC_COUNT functions in library index"

# Build the raw LLM prompt
cat > "$RAW_DIR/system_prompt.txt" <<'SYSPROMPT'
You are an expert algorithm engineer. Given a goal and a library of available functions, identify which functions from the library are needed to accomplish the goal. Return a JSON object with a 'functions' key containing an array of the exact function names needed, in pipeline order.
SYSPROMPT

cat > "$RAW_DIR/user_prompt.txt" <<USERPROMPT
Goal: $GOAL

Available functions (name : type_signature):
$(cat "$RAW_DIR/function_list.txt")

Return JSON: {"functions": ["function1", "function2", ...]}
USERPROMPT

RAW_RUNNER="$RAW_DIR/raw_llm_runner.py"
cat > "$RAW_RUNNER" <<'PYEOF'
import asyncio
import json
import os
import re
import sys

RAW_DIR = os.environ.get("RAW_DIR", "")


async def run():
    from sciona.config import AgeomConfig
    from sciona.hunter.llm import create_llm_client

    config = AgeomConfig()
    provider = os.environ.get("LLM_PROVIDER", "codex_shim")
    model = os.environ.get("LLM_MODEL", "gpt-5.3-codex")
    raw_provider = provider[:-5] + "_cli" if provider.endswith("_shim") else provider

    def make_client():
        return create_llm_client(
            provider=raw_provider,
            model=model,
            max_tokens=config.llm_max_tokens,
            anthropic_api_key=config.anthropic_api_key,
            openai_api_key=config.openai_api_key,
            openai_base_url=config.openai_base_url,
            llama_cpp_base_url=config.llama_cpp_base_url,
            llama_cpp_api_key=config.llama_cpp_api_key,
            allow_legacy_subprocess=True,
        )

    llm = make_client()

    with open(f"{RAW_DIR}/system_prompt.txt") as f:
        system = f.read()
    with open(f"{RAW_DIR}/user_prompt.txt") as f:
        user = f.read()

    last_exc = None
    response = ""
    for attempt in range(2):
        try:
            response = await llm.complete(system, user)
            break
        except Exception as exc:
            last_exc = exc
            if attempt == 1:
                raise
            llm = make_client()
    if not response and last_exc is not None:
        raise last_exc
    with open(f"{RAW_DIR}/response.txt", "w") as f:
        f.write(response)

    functions = []
    try:
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            end = len(lines) - 1 if lines[-1].strip().startswith("```") else len(lines)
            text = "\n".join(lines[1:end])
        parsed = json.loads(text)
        functions = parsed.get("functions", [])
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                functions = parsed.get("functions", [])
            except json.JSONDecodeError:
                pass
        if not functions:
            print("WARNING: could not parse JSON from LLM response", file=sys.stderr)

    matches = []
    for fn in functions:
        matches.append(
            {
                "verified_match": None,
                "all_candidates": [
                    {
                        "declaration": {"name": fn},
                        "score": 1.0,
                        "retrieval_method": "raw_llm",
                    }
                ],
                "all_verifications": [],
            }
        )
    with open(f"{RAW_DIR}/matches.json", "w") as f:
        json.dump(matches, f, indent=2)
    print(f"Raw LLM identified {len(functions)} functions: {functions}")


asyncio.run(run())
PYEOF

RAW_MS=0
run_logged_command \
    "raw_llm" \
    "$RAW_DIR/stdout.txt" \
    "$RAW_TIMEOUT_S" \
    RAW_MS \
    RAW_RC \
    "$BENCHMARK_PYTHON" "$RAW_RUNNER"
if [ "$RAW_RC" -eq 124 ] && [ -f "$RAW_DIR/matches.json" ]; then
    warn "raw LLM timed out after ${RAW_MS}ms but produced matches.json"
elif [ "$RAW_RC" -ne 0 ]; then
    fail "raw LLM crashed (exit $RAW_RC) after ${RAW_MS}ms — see $RAW_DIR/stdout.txt"
else
    info "  raw LLM completed in ${RAW_MS}ms"
fi

# ---------------------------------------------------------------------------
# 5. Synthesis/export/profile for pipeline modes
# ---------------------------------------------------------------------------
if [[ "$(printf '%s' "$INCLUDE_SYNTHESIS" | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
    echo ""
    info "Running synthesize/export/profile phases..."
    run_pipeline_postprocess rapid "$RAPID_DIR"
    run_pipeline_postprocess structured "$STRUCTURED_DIR"
    run_pipeline_postprocess verified "$VERIFIED_DIR"
fi

# ---------------------------------------------------------------------------
# 6. Ground truth evaluation
# ---------------------------------------------------------------------------
echo ""
info "Evaluating ground truth coverage..."
echo ""

for label_dir in \
    "rapid:$RAPID_DIR" \
    "structured:$STRUCTURED_DIR" \
    "verified:$VERIFIED_DIR" \
    "raw_llm:$RAW_DIR"; do
    label="${label_dir%%:*}"
    dir="${label_dir#*:}"
    if [ -f "$dir/matches.json" ]; then
        check_ground_truth "$dir/matches.json" "$label"
    else
        warn "$label: no matches.json found (run may have crashed)"
        echo "0" > "$OUTPUT_DIR/${label}_hits.txt"
    fi
done

# ---------------------------------------------------------------------------
# 7. Summary report
# ---------------------------------------------------------------------------
echo ""
info "Generating summary..."

OUTPUT_DIR_FOR_SUMMARY="$OUTPUT_DIR" \
RAPID_DIR_FOR_SUMMARY="$RAPID_DIR" \
STRUCTURED_DIR_FOR_SUMMARY="$STRUCTURED_DIR" \
VERIFIED_DIR_FOR_SUMMARY="$VERIFIED_DIR" \
RAW_DIR_FOR_SUMMARY="$RAW_DIR" \
PROFILE_DATASET_FOR_SUMMARY="$PROFILE_DATASET" \
RAPID_MS_FOR_SUMMARY="$RAPID_MS" \
STRUCTURED_MS_FOR_SUMMARY="$STRUCTURED_MS" \
VERIFIED_MS_FOR_SUMMARY="$VERIFIED_MS" \
RAW_MS_FOR_SUMMARY="$RAW_MS" \
TOTAL_GT_FOR_SUMMARY="${#GROUND_TRUTH_PATTERNS[@]}" \
GOAL_FOR_SUMMARY="$GOAL" \
PROVER_FOR_SUMMARY="$PROVER" \
LLM_PROVIDER_FOR_SUMMARY="$LLM_PROVIDER" \
"$BENCHMARK_PYTHON" <<'PYEOF' 2>&1 | tee "$OUTPUT_DIR/summary_table.txt"
import json
import os
from pathlib import Path

from sciona.principal.e2e_benchmark_policy import evaluate_e2e_benchmark_report

output_dir = Path(os.environ['OUTPUT_DIR_FOR_SUMMARY'])
total_gt = int(os.environ['TOTAL_GT_FOR_SUMMARY'])

def read_hits(label):
    p = output_dir / f'{label}_hits.txt'
    return int(p.read_text().strip()) if p.exists() else 0

def read_matches(label_dir):
    p = Path(label_dir) / 'matches.json'
    if not p.exists():
        return 0, 0
    matches = json.load(open(p))
    total = len(matches)
    verified = sum(1 for m in matches
                   if m.get('verified_match') and m['verified_match'].get('verified'))
    return total, verified

def read_postprocess(label_dir):
    p = Path(label_dir) / 'postprocess.json'
    if not p.exists():
        return None
    return json.load(open(p))

def is_executable(postprocess):
    if not isinstance(postprocess, dict) or not postprocess.get('attempted'):
        return False
    synth = postprocess.get('synthesize') or {}
    export = postprocess.get('export') or {}
    profile = postprocess.get('profile') or {}
    return bool(
        synth.get('exit_code') == 0
        and synth.get('compiled_ok') is True
        and synth.get('output_exists') is True
        and export.get('exit_code') == 0
        and export.get('output_exists') is True
        and profile.get('attempted') is True
        and profile.get('exit_code') == 0
    )

rapid_total, rapid_verified = read_matches(os.environ['RAPID_DIR_FOR_SUMMARY'])
structured_total, structured_verified = read_matches(os.environ['STRUCTURED_DIR_FOR_SUMMARY'])
verified_total, verified_verified = read_matches(os.environ['VERIFIED_DIR_FOR_SUMMARY'])
raw_total, raw_verified = read_matches(os.environ['RAW_DIR_FOR_SUMMARY'])
rapid_post = read_postprocess(os.environ['RAPID_DIR_FOR_SUMMARY'])
structured_post = read_postprocess(os.environ['STRUCTURED_DIR_FOR_SUMMARY'])
verified_post = read_postprocess(os.environ['VERIFIED_DIR_FOR_SUMMARY'])

postprocess = None
if any(item is not None for item in (rapid_post, structured_post, verified_post)):
    postprocess = {
        'enabled': True,
        'dataset': os.environ['PROFILE_DATASET_FOR_SUMMARY'],
        'rapid': rapid_post,
        'structured': structured_post,
        'verified': verified_post,
    }

shortcut_flags = {
    'curated_signal_event_rate_shortcut': False,
    'generic_only_keyword_path': (
        str(os.environ.get('E2E_GENERIC_ONLY', '')).strip().lower() in {'1', 'true', 'yes'}
    ),
    'forced_semantic_backend_faiss': False,
}

variants = {
    'rapid': {
        'mode_dir': os.environ['RAPID_DIR_FOR_SUMMARY'],
        'latency_ms': int(os.environ['RAPID_MS_FOR_SUMMARY']),
        'matches_total': rapid_total,
        'matches_verified': rapid_verified,
        'ground_truth_hits': read_hits('rapid'),
        'executable': is_executable(rapid_post),
    },
    'structured': {
        'mode_dir': os.environ['STRUCTURED_DIR_FOR_SUMMARY'],
        'latency_ms': int(os.environ['STRUCTURED_MS_FOR_SUMMARY']),
        'matches_total': structured_total,
        'matches_verified': structured_verified,
        'ground_truth_hits': read_hits('structured'),
        'executable': is_executable(structured_post),
    },
    'verified': {
        'mode_dir': os.environ['VERIFIED_DIR_FOR_SUMMARY'],
        'latency_ms': int(os.environ['VERIFIED_MS_FOR_SUMMARY']),
        'matches_total': verified_total,
        'matches_verified': verified_verified,
        'ground_truth_hits': read_hits('verified'),
        'executable': is_executable(verified_post),
    },
    'raw_llm': {
        'mode_dir': os.environ['RAW_DIR_FOR_SUMMARY'],
        'latency_ms': int(os.environ['RAW_MS_FOR_SUMMARY']),
        'matches_total': raw_total,
        'matches_verified': 0,
        'ground_truth_hits': read_hits('raw_llm'),
        'executable': False,
    },
}

report = evaluate_e2e_benchmark_report(
    goal=os.environ['GOAL_FOR_SUMMARY'],
    prover=os.environ['PROVER_FOR_SUMMARY'],
    llm_provider=os.environ['LLM_PROVIDER_FOR_SUMMARY'],
    total_ground_truth=total_gt,
    variants=variants,
    shortcut_flags=shortcut_flags,
    declared_shortcuts=[],
    postprocess=postprocess,
)

with open(output_dir / 'summary.json', 'w') as f:
    json.dump(report, f, indent=2)
(output_dir / 'benchmark_passed.txt').write_text(
    'true\n' if report['benchmark_policy']['passed'] else 'false\n'
)

# Print table
print()
print('variant | latency | matches | verified | GT coverage')
print('--- | ---: | ---: | ---: | ---:')
for variant in ['rapid', 'structured', 'verified', 'raw_llm']:
    r = report['results'][variant]
    lat = f"{r['latency_ms']}ms"
    gt = f"{r['ground_truth_hits']}/{total_gt}"
    cov = f"{r['ground_truth_coverage']:.0%}"
    print(f"{variant} | {lat} | {r['matches_total']} | {r['matches_verified']} | {gt} ({cov})")
print()
print('variant | artifacts | anti-shortcut | behavioral | enriched-cdg | asset-migration')
print('--- | --- | --- | --- | --- | ---')
for variant in ['rapid', 'structured', 'verified', 'raw_llm']:
    policy = report['results'][variant]['policy']
    artifacts_ok = policy['required_artifacts']['passed']
    anti_shortcut_ok = policy['anti_shortcut']['passed']
    behavioral_ok = policy['behavioral']['passed']
    enriched_ok = policy['enriched_cdg']['passed']
    migration_ok = policy['asset_migration']['passed']
    print(
        f"{variant} | {artifacts_ok} | {anti_shortcut_ok} | {behavioral_ok} | "
        f"{enriched_ok} | {migration_ok}"
    )
if report.get('postprocess', {}).get('enabled'):
    print()
    print('postprocess variant | synth rc | compiled_ok | export rc | profile rc | gradients')
    print('--- | ---: | --- | ---: | ---: | ---')
    for variant in ['rapid', 'structured', 'verified']:
        data = report['postprocess'].get(variant) or {}
        synth = data.get('synthesize') or {}
        export = data.get('export') or {}
        profile = data.get('profile') or {}
        print(
            f"{variant} | {synth.get('exit_code', '')} | {synth.get('compiled_ok', '')} | "
            f"{export.get('exit_code', '')} | {profile.get('exit_code', '')} | {profile.get('has_gradients', '')}"
        )
print()
PYEOF

# ---------------------------------------------------------------------------
# 8. Final verdict
# ---------------------------------------------------------------------------
echo ""
info "Results saved to $OUTPUT_DIR/"
ls -lh "$OUTPUT_DIR/"

VERIFIED_HITS=$(cat "$OUTPUT_DIR/verified_hits.txt" 2>/dev/null || echo "0")
RAW_HITS=$(cat "$OUTPUT_DIR/raw_llm_hits.txt" 2>/dev/null || echo "0")
TOTAL_GT=${#GROUND_TRUTH_PATTERNS[@]}

echo ""
if [ "$VERIFIED_HITS" -ge "$RAW_HITS" ] && [ "$VERIFIED_HITS" -eq "$TOTAL_GT" ]; then
    ok "Pipeline (verified) achieves full ground truth coverage"
elif [ "$VERIFIED_HITS" -ge "$RAW_HITS" ]; then
    ok "Pipeline (verified) matches or beats raw LLM ($VERIFIED_HITS/$TOTAL_GT vs $RAW_HITS/$TOTAL_GT)"
elif [ "$VERIFIED_HITS" -lt "$RAW_HITS" ]; then
    warn "Raw LLM outperforms pipeline ($RAW_HITS/$TOTAL_GT vs $VERIFIED_HITS/$TOTAL_GT) — investigate decomposition quality"
fi

echo ""
if [ "$(cat "$OUTPUT_DIR/benchmark_passed.txt" 2>/dev/null || echo false)" != "true" ]; then
    fail "E2E benchmark policy failed"
    exit 1
fi
ok "E2E benchmark complete"
