(function () {
  "use strict";

  // ─── DOM refs ───
  const runList = document.getElementById("run-list");
  const summary = document.getElementById("summary");
  const metaBlock = document.getElementById("meta-block");
  const stagesTable = document.getElementById("stages-table");
  const promptsTable = document.getElementById("prompts-table");
  const inflightTable = document.getElementById("inflight-table");
  const stateFilter = document.getElementById("state-filter");
  const btnRefresh = document.getElementById("btn-refresh");
  const lastRefresh = document.getElementById("last-refresh");
  const tabBar = document.getElementById("tab-bar");
  const timelineList = document.getElementById("timeline-list");
  const tlRoundFilter = document.getElementById("tl-round-filter");
  const tlTypeFilter = document.getElementById("tl-type-filter");
  const tlSearch = document.getElementById("tl-search");
  const tlLive = document.getElementById("tl-live");
  const sseDot = document.getElementById("sse-dot");
  const tlCount = document.getElementById("tl-count");
  const tlMoreWrap = document.getElementById("tl-more-wrap");
  const tlLoadMore = document.getElementById("tl-load-more");
  const coverageSummary = document.getElementById("coverage-summary");
  const coverageTable = document.getElementById("coverage-table");
  const errorSummary = document.getElementById("error-summary");
  const errorList = document.getElementById("error-list");
  const btnOpenEvolution = document.getElementById("btn-open-evolution");

  // Deep link: ?run=abc123 selects a run on load
  const _urlParams = new URLSearchParams(window.location.search);
  let selectedRunId = _urlParams.get("run") || "";
  let latestRuns = [];
  let activeTab = "overview";
  let activeEventSource = null;
  let timelineOffset = 0;
  const TIMELINE_PAGE = 200;

  // ─── Utilities ───
  function fmtTime(ts) {
    if (!ts) return "--";
    try { return new Date(ts * 1000).toLocaleTimeString(); } catch (_) { return "--"; }
  }

  function fmtTimeFull(ts) {
    if (!ts) return "--";
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString() + "." + String(d.getMilliseconds()).padStart(3, "0");
    } catch (_) { return "--"; }
  }

  function fmtAgeSec(ts) {
    if (!ts) return "--";
    const delta = Math.max(0, Date.now() / 1000 - ts);
    return delta.toFixed(1) + "s";
  }

  function fmtDuration(startedAt, endedAt) {
    if (!startedAt) return "--";
    const end = endedAt || Date.now() / 1000;
    return Math.max(0, end - startedAt).toFixed(1) + "s";
  }

  function runStatusClass(status) {
    if (status === "completed") return "s-completed";
    if (status === "failed") return "s-failed";
    return "s-running";
  }

  function phaseClass(round) {
    if (round === "architect") return "phase-architect";
    if (round === "hunter") return "phase-hunter";
    if (round === "synthesizer") return "phase-synthesizer";
    if (round === "llm") return "phase-llm";
    if (round === "ingester") return "phase-ingester";
    return "";
  }

  function esc(s) {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  // ─── Tab switching ───
  tabBar.addEventListener("click", (e) => {
    const tab = e.target.dataset.tab;
    if (!tab) return;
    activeTab = tab;
    tabBar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.toggle("active", c.id === "tab-" + tab));
    if (tab === "timeline") loadTimeline();
    if (tab === "coverage") loadCoverage();
    if (tab === "errors") loadErrors();
    if (tab !== "timeline") closeSSE();
  });

  // ─── API helpers ───
  async function fetchRuns() {
    const state = encodeURIComponent(stateFilter.value || "all");
    const res = await fetch(`/api/dashboard/runs?limit=100&state=${state}`);
    if (!res.ok) throw new Error(`dashboard api error ${res.status}`);
    const payload = await res.json();
    return payload.runs || [];
  }

  async function fetchRun(runId) {
    const res = await fetch(`/api/dashboard/runs/${encodeURIComponent(runId)}`);
    if (!res.ok) throw new Error(`dashboard run error ${res.status}`);
    return await res.json();
  }

  async function fetchEvents(runId, offset, limit, filters) {
    const params = new URLSearchParams({ offset, limit });
    if (filters.round) params.set("round", filters.round);
    if (filters.event_type) params.set("event_type", filters.event_type);
    if (filters.prompt_key) params.set("prompt_key", filters.prompt_key);
    if (filters.has_error !== undefined) params.set("has_error", filters.has_error);
    const res = await fetch(`/api/dashboard/runs/${encodeURIComponent(runId)}/events?${params}`);
    if (!res.ok) throw new Error(`events api error ${res.status}`);
    return await res.json();
  }

  async function fetchCoverage(runId) {
    const res = await fetch(`/api/dashboard/runs/${encodeURIComponent(runId)}/coverage`);
    if (!res.ok) throw new Error(`coverage api error ${res.status}`);
    return await res.json();
  }

  async function fetchErrorDrilldown(runId) {
    const res = await fetch(`/api/dashboard/runs/${encodeURIComponent(runId)}/errors`);
    if (!res.ok) throw new Error(`errors api error ${res.status}`);
    return await res.json();
  }

  // ─── Run list (unchanged) ───
  function renderRunList(rows) {
    runList.innerHTML = "";
    if (!rows.length) {
      runList.innerHTML = '<div class="run-meta" style="padding:10px;">No telemetry runs yet.</div>';
      return;
    }
    rows.forEach((r) => {
      const div = document.createElement("div");
      div.className = "run-item" + (r.run_id === selectedRunId ? " active" : "");
      div.dataset.runId = r.run_id || "";
      const hung = r.is_hung ? " <span class=\"bad\">hung</span>" : "";
      const architect = r.architect_summary || {};
      const retrieval = r.retrieval_summary || {};
      const execution = r.execution_summary || {};
      const routing = (r.routing_summary || {}).architect || {};
      const hunter = r.hunter_summary || {};
      const complexity = r.provider_complexity || {};
      const catalog = r.catalog_alignment_summary || {};
      const benchmark = r.benchmark_summary || {};
      const catalogValidation = r.catalog_validation_summary || {};
      const catalogValidationAlignment = catalogValidation.alignment || {};
      const benchmarkRuntime = benchmark.runtime_complexity || {};
      const benchmarkRuntimeModes = benchmarkRuntime.by_mode || {};
      const singleAgentComparison = benchmark.single_agent_comparison || {};
      const singleAgent = r.single_agent_summary || {};
      const singleAgentPolicy = singleAgent.policy || {};
      const singleAgentTools = singleAgent.tool_metrics || [];
      const singleAgentEscalations = singleAgent.escalation_events || [];
      const singleAgentToolSummary = singleAgentTools.length
        ? singleAgentTools.map((row) => `${row.name}=${Number(row.dispatches || 0)}/${Number(row.avg_latency_ms || 0).toFixed(1)}ms`).join(" ")
        : "";
      const singleAgentEscalationSummary = singleAgentEscalations.length
        ? singleAgentEscalations.map((row) => `${row.from}->${row.to}`).join(" ")
        : "";
      const singleAgentComparisonSummary = benchmark.single_agent_comparison_summary || "";
      const flowPromptCalls = benchmark.flow_avg_prompt_calls || {};
      const flowPlannerTools = benchmark.flow_avg_planner_tool_dispatches || {};
      const flowPlannerEscalations = benchmark.flow_avg_planner_escalations || {};
      const flowLatency = benchmark.flow_avg_latency_ms || {};
      const flowPromptCallSummary = Object.keys(flowPromptCalls).length
        ? Object.entries(flowPromptCalls).map(([k, v]) => `${k}=${Number(v).toFixed(1)}`).join(" ")
        : "";
      const flowPlannerToolSummary = Object.keys(flowPlannerTools).length
        ? Object.entries(flowPlannerTools).map(([k, v]) => `${k}=${Number(v).toFixed(1)}`).join(" ")
        : "";
      const flowPlannerEscalationSummary = Object.keys(flowPlannerEscalations).length
        ? Object.entries(flowPlannerEscalations).map(([k, v]) => `${k}=${Number(v).toFixed(1)}`).join(" ")
        : "";
      const flowLatencySummary = Object.keys(flowLatency).length
        ? Object.entries(flowLatency).map(([k, v]) => `${k}=${Number(v).toFixed(1)}ms`).join(" ")
        : "";
      const topSources = (catalog.top_sources || []).map((row) => `${row.source}=${Number(row.added || 0)}`).join(" ");
      const topMerges = (catalog.top_merges || []).map((row) => `${row.candidate}->${row.incumbent}`).join(" ");
      const shared = r.shared_context_summary || {};
      const optimize = r.optimize_summary || {};
      const unresolved = Number(architect.unresolved_leaf_count || 0);
      const blocked = Number(architect.blocked_count || 0);
      const retrievalBand = retrieval.confidence_band || "--";
      const activeOverrides = Number(routing.active_count || 0);
      const sharedContexts = Number(shared.active_context_count || shared.context_count || 0);
      const sharedSearches = Number(shared.total_searches || 0);
      const sharedTemplates = Number(shared.total_template_hits || 0);
      const sharedFailures = Number(shared.total_failure_hits || 0);
      const critiqueRejects = Number(architect.critique_reject_total || 0);
      const verifiedMatches = Number(hunter.verified_matches || 0);
      const benchmarkStatus = benchmark.status || "--";
      const singleAgentLine = singleAgent.termination_reason
        ? `<div class="run-meta">single_agent=${singleAgent.termination_reason} steps=${Number(singleAgent.steps_used || 0)}/${Number(singleAgent.step_budget || 0)} retrieval=${singleAgentPolicy.retrieval_intensity || "--"} tools=${Number(singleAgent.tool_dispatch_count_total || 0)} latency=${Number(singleAgent.tool_latency_ms_total || 0).toFixed(1)}ms artifacts=${Number(singleAgent.artifact_count || 0)}</div>`
        : "";
      const singleAgentToolLine = singleAgentToolSummary
        ? `<div class="run-meta">single_agent_tools ${singleAgentToolSummary}</div>`
        : "";
      const singleAgentEscalationLine = singleAgentEscalationSummary
        ? `<div class="run-meta">single_agent_escalations ${singleAgentEscalationSummary}</div>`
        : "";
      const singleAgentBenchmarkLine = singleAgentComparisonSummary
        ? `<div class="run-meta">single_agent_bench ${singleAgentComparisonSummary}</div>`
        : "";
      const singleAgentDecisionLine = singleAgentComparison.dominant_planner_termination_reason || singleAgentComparison.planner_action_summary
        ? `<div class="run-meta">single_agent_route term=${singleAgentComparison.dominant_planner_termination_reason || "--"} actions=${singleAgentComparison.planner_action_summary || "--"}</div>`
        : "";
      const singleAgentOverheadLine = singleAgentComparison.overhead_driver
        ? `<div class="run-meta">single_agent_overhead driver=${singleAgentComparison.overhead_driver} prune=${singleAgentComparison.prune_recommendation || "keep_current"} tools=${Number(singleAgentComparison.avg_planner_tool_dispatches || 0).toFixed(1)} escalations=${Number(singleAgentComparison.avg_planner_escalations || 0).toFixed(1)}</div>`
        : "";
      const singleAgentPruneLine = benchmark.top_warning_subcheck === "single_agent_prune"
        ? `<div class="run-meta">single_agent_prune ${benchmark.top_warning || "--"}</div>`
        : "";
      const runtimeModeSummary = Object.keys(benchmarkRuntimeModes).length
        ? Object.entries(benchmarkRuntimeModes)
            .map(([mode, row]) => `${mode}=${Number(row.provider_count || 0)}/${Number(row.transport_count || 0)}`)
            .join(" ")
        : "";
      const releaseWarningLine = benchmark.release_warning_summary
        ? `<div class="run-meta">release_warn runtime=${Number(benchmark.release_runtime_warning_count || 0)} catalog=${Number(benchmark.release_catalog_warning_count || 0)} benchmark=${Number(benchmark.release_benchmark_warning_count || 0)}</div>`
        : "";
      const validationLine = (benchmark.prompt_results || benchmark.flow_results)
        ? `<div class="run-meta">bench=${benchmarkStatus} prompt=${Number(benchmark.prompt_results || 0)} flow=${Number(benchmark.flow_results || 0)} runtime_providers=${Number(benchmarkRuntime.provider_count || 0)}${benchmark.release_status ? ` release=${benchmark.release_status}` : ""}</div>`
        : "";
      const optimizeLine = optimize.trials_run
        ? `<div class="run-meta">optimize objective=${optimize.objective || "--"} best=${Number(optimize.best_loss || 0).toFixed(6)} trials=${Number(optimize.trials_run || 0)}/${Number(optimize.max_trials || 0)} params=${Number(optimize.parameterized_trials || 0)} variants=${Number(optimize.primitive_change_trials || 0)} proposals=${Number(optimize.proposal_selection_trials || 0)} rollbacks=${Number(optimize.rollback_trials || 0)} reuse=${Number(optimize.cached_reuse_trials || 0)} delta=${Number(optimize.mean_expansion_loss_delta || 0).toFixed(3)}</div>`
        : "";
      const proposalCounts = optimize.selected_proposal_counts || {};
      const proposalSummary = Object.keys(proposalCounts).length
        ? Object.entries(proposalCounts).map(([label, count]) => `${label}=${Number(count || 0)}`).join(" ")
        : "";
      const proposalLine = optimize.proposal_selection_trials
        ? `<div class="run-meta">optimize_proposals selected=${proposalSummary || "--"} rejected=${Number(optimize.proposal_rejected_trials || 0)} gain=${Number(optimize.mean_selected_proposal_improvement || 0).toFixed(3)}</div>`
        : "";
      const skeletonLine = optimize.skeleton_proposal_trials
        ? `<div class="run-meta">optimize_skeletons trials=${Number(optimize.skeleton_proposal_trials || 0)} accepted=${Number(optimize.accepted_skeleton_proposals || 0)} rejected=${Number(optimize.rejected_skeleton_proposals || 0)} penalty=${Number(optimize.mean_skeleton_complexity_penalty || 0).toFixed(3)} gain=${Number(optimize.mean_skeleton_objective_gain || 0).toFixed(3)} retain=${Number(optimize.skeleton_retention_rate || 0).toFixed(2)}</div>`
        : "";
      const reuseLine = optimize.cached_reuse_trials
        ? `<div class="run-meta">optimize_reuse reused=${Number(optimize.cached_reuse_trials || 0)} avoided=${Number(optimize.cached_reruns_avoided || 0)}</div>`
        : "";
      div.innerHTML = [
        '<div class="run-line">',
        `<span class="run-id">${(r.run_id || "").slice(0, 12)}</span>`,
        `<span class="pill ${runStatusClass(r.status || "running")}">${r.status || "running"}</span>`,
        '</div>',
        `<div class="run-meta">${r.pipeline || "run"}${r.label ? " · " + esc(r.label) : ""} · updated ${fmtAgeSec(r.last_update_at)} ago${hung}</div>`,
        `<div class="run-meta">mode=${execution.mode || "--"} path=${execution.path || "--"}</div>`,
        `<div class="run-meta">dispatches=${r.prompt_dispatches || 0} inflight=${r.prompt_inflight || 0} unresolved=${unresolved} blocked=${blocked}</div>`,
        `<div class="run-meta">critique_rejects=${critiqueRejects} retries=${Number(architect.retry_total || 0)} rewrites=${Number(architect.rewrite_action_count || 0)}</div>`,
        `<div class="run-meta">searches=${Number(hunter.search_iterations || 0)} candidates=${Number(hunter.new_candidates_total || 0)} verified=${verifiedMatches}</div>`,
        `<div class="run-meta">retrieval=${retrievalBand} architect_overrides=${activeOverrides} providers=${Number(complexity.provider_count || 0)}</div>`,
        `<div class="run-meta">catalog=${Number(catalog.catalog_size || 0)} added=${Number(catalog.added || 0)} merged=${Number(catalog.merged || 0)}</div>`,
        ((catalogValidation.status || catalogValidationAlignment.source_count)
          ? `<div class="run-meta">catalog_validate=${catalogValidation.status || "--"} drift=${Number(catalogValidationAlignment.registry_only_total || 0)}/${Number(catalogValidationAlignment.ast_only_total || 0)}</div>`
          : ""),
        (topSources ? `<div class="run-meta">catalog_sources ${topSources}</div>` : ""),
        (topMerges ? `<div class="run-meta">catalog_merges ${topMerges}</div>` : ""),
        `<div class="run-meta">shared_ctx=${sharedContexts} searches=${sharedSearches} template_hits=${sharedTemplates} failure_hits=${sharedFailures}</div>`,
        optimizeLine,
        proposalLine,
        skeletonLine,
        reuseLine,
        (flowPromptCallSummary ? `<div class="run-meta">flow_prompts ${flowPromptCallSummary}</div>` : ""),
        (flowPlannerToolSummary ? `<div class="run-meta">flow_tools ${flowPlannerToolSummary}</div>` : ""),
        (flowPlannerEscalationSummary ? `<div class="run-meta">flow_escalations ${flowPlannerEscalationSummary}</div>` : ""),
        (flowLatencySummary ? `<div class="run-meta">flow_latency ${flowLatencySummary}</div>` : ""),
        (runtimeModeSummary ? `<div class="run-meta">runtime_modes ${runtimeModeSummary}</div>` : ""),
        singleAgentLine, singleAgentToolLine, singleAgentEscalationLine, singleAgentBenchmarkLine,
        singleAgentDecisionLine, singleAgentOverheadLine, singleAgentPruneLine,
        releaseWarningLine, validationLine
      ].join("");
      div.addEventListener("click", () => {
        selectedRunId = r.run_id || "";
        const u = new URL(window.location);
        if (selectedRunId) u.searchParams.set("run", selectedRunId);
        else u.searchParams.delete("run");
        history.replaceState(null, "", u);
        renderRunList(latestRuns);
        loadSelected();
      });
      runList.appendChild(div);
    });
  }

  // ─── Overview tab (existing) ───
  function setSummary(run) {
    const stages = run.stages || {};
    const stageCount = Object.keys(stages).length;
    const staleCount = (run.stale_stages || []).length;
    const architect = run.architect_summary || {};
    const retrieval = run.retrieval_summary || {};
    const execution = run.execution_summary || {};
    const architectRouting = (run.routing_summary || {}).architect || {};
    const hunterRouting = (run.routing_summary || {}).hunter || {};
    const hunter = run.hunter_summary || {};
    const complexity = run.provider_complexity || {};
    const catalog = run.catalog_alignment_summary || {};
    const catalogValidation = run.catalog_validation_summary || {};
    const catalogValidationAlignment = catalogValidation.alignment || {};
    const topCatalogDrift = (run.catalog_validation_summary?.top_drift_sources || [])
      .map((row) => `${row.source}:${row.severity || "--"}:${Number(row.registry_only_count || 0)}/${Number(row.ast_only_count || 0)}`)
      .join(", ");
    const benchmark = run.benchmark_summary || {};
    const benchmarkRuntime = benchmark.runtime_complexity || {};
    const benchmarkRuntimeModes = benchmarkRuntime.by_mode || {};
    const benchmarkOverridePolicy = benchmarkRuntime.override_policy || {};
    const benchmarkFlowPaths = benchmark.flow_execution_paths || {};
    const singleAgentComparison = benchmark.single_agent_comparison || {};
    const singleAgent = run.single_agent_summary || {};
    const singleAgentPolicy = singleAgent.policy || {};
    const singleAgentTools = singleAgent.tool_metrics || [];
    const singleAgentEscalations = singleAgent.escalation_events || [];
    const singleAgentComparisonSummary = benchmark.single_agent_comparison_summary || "";
    const flowPromptCalls = benchmark.flow_avg_prompt_calls || {};
    const flowPlannerTools = benchmark.flow_avg_planner_tool_dispatches || {};
    const flowPlannerEscalations = benchmark.flow_avg_planner_escalations || {};
    const flowLatency = benchmark.flow_avg_latency_ms || {};
    const promptLatency = benchmark.prompt_avg_latency_ms || {};
    const topSources = (catalog.top_sources || []).map((row) => `${row.source}=${Number(row.added || 0)}`).join(", ");
    const topMerges = (catalog.top_merges || []).map((row) => `${row.candidate}->${row.incumbent}@${Number(row.similarity || 0).toFixed(2)}`).join(", ");
    const shared = run.shared_context_summary || {};
    const optimize = run.optimize_summary || {};
    const evolutionVersions = ((optimize.evolution || {}).versions || []);
    if (btnOpenEvolution) {
      btnOpenEvolution.classList.toggle("is-hidden", evolutionVersions.length === 0);
    }
    const metrics = [
      ["pipeline", run.pipeline || "--"],
      ["label", run.label || "--"],
      ["status", run.status || "--"],
      ["mode/path", `${execution.mode || "--"} / ${execution.path || "--"}`],
      ["duration", fmtDuration(run.started_at, run.ended_at)],
      ["dispatches", String(run.prompt_dispatches || 0)],
      ["inflight", String(run.prompt_inflight || 0)],
      ["stages", String(stageCount)],
      ["events", String(run.events_count || 0)],
      ["stale", String(staleCount)],
      ["success", String(run.prompt_successes || 0)],
      ["failures", String(run.prompt_failures || 0)],
      ["unresolved", String(architect.unresolved_leaf_count || 0)],
      ["blocked", String(architect.blocked_count || 0)],
      ["blocked reason", architect.blocked_reason || "--"],
      ["last node", architect.last_node_name || "--"],
      ["critique rejects", String(architect.critique_reject_total || 0)],
      ["retry total", String(architect.retry_total || 0)],
      ["rewrite actions", String(architect.rewrite_action_count || 0)],
      ["any ports", `${(Number(architect.any_port_pct || 0) * 100).toFixed(1)}%`],
      ["any edges", `${(Number(architect.any_edge_pct || 0) * 100).toFixed(1)}%`],
      ["hunter searches", String(hunter.search_iterations || 0)],
      ["hunter candidates", `${Number(hunter.new_candidates_total || 0)} / ${Number(hunter.candidate_pool_size || 0)}`],
      ["hunter verify", `${Number(hunter.verified_candidates_total || 0)} / ${Number(hunter.verify_batches || 0)}`],
      ["hunter success/fail", `${Number(hunter.verification_success_total || 0)} / ${Number(hunter.verification_failure_total || 0)}`],
      ["hunter reformulate", `${Number(hunter.reformulations || 0)} / ${Number(hunter.reformulate_fallbacks || 0)}`],
      ["hunter analyses", String(hunter.failure_analyses || 0)],
      ["hunter query count", String(hunter.query_count || 0)],
      ["last query", hunter.last_query || "--"],
      ["last verified", hunter.last_verified_candidate || "--"],
      ["retrieval", `${retrieval.confidence_band || "--"} / ${retrieval.semantic_backend || "--"}`],
      ["architect llm", `${architectRouting.default || "--"} +${architectRouting.active_count || 0}`],
      ["hunter llm", `${hunterRouting.default || "--"} +${hunterRouting.active_count || 0}`],
      ["providers", `${Number(complexity.provider_count || 0)} / ${Number(complexity.provider_model_count || 0)}`],
      ["transports", (complexity.transports || []).join(", ") || "--"],
      ["catalog", `${Number(catalog.catalog_size || 0)} / ${Number(catalog.total_candidates || 0)}`],
      ["catalog add/merge", `${Number(catalog.added || 0)} / ${Number(catalog.merged || 0)}`],
      ["catalog sources", `live=${Number(catalog.live_registry || 0)} ast=${Number(catalog.ast_fallback || 0)} cdg=${Number(catalog.cdg_matched || 0)}`],
      ["catalog top sources", topSources || "--"],
      ["catalog top merges", topMerges || "--"],
      ["catalog validate", `${catalogValidation.status || "--"} ${Number(catalogValidation.resolved_sources || 0)}/${Number(catalogValidation.configured_sources || 0)} src ${Number(catalogValidation.source_added || 0)}/${Number(catalogValidation.source_candidates || 0)}`],
      ["catalog coverage", catalogValidation.coverage_summary || "--"],
      ["catalog warnings", catalogValidation.warning_summary || "--"],
      ["catalog issues", (catalogValidation.violations || []).join(", ") || "--"],
      ["catalog warn src", `${(catalogValidation.high_severity_sources || []).join(", ") || "--"} | ${(catalogValidation.medium_severity_sources || []).join(", ") || "--"}`],
      ["catalog drift", `${Number(catalogValidationAlignment.registry_only_total || 0)} registry-only / ${Number(catalogValidationAlignment.ast_only_total || 0)} ast-only`],
      ["catalog align sum", catalogValidation.alignment_summary || "--"],
      ["catalog severity", `${catalogValidationAlignment.highest_severity || "--"} ${JSON.stringify(catalogValidationAlignment.severity_counts || {})}`],
      ["catalog drift src", (catalogValidationAlignment.drift_sources || []).join(", ") || "--"],
      ["catalog drift top", topCatalogDrift || "--"],
      ["shared ctx", `${Number(shared.active_context_count || 0)}/${Number(shared.context_count || 0)}`],
      ["ctx search/hit", `${Number(shared.total_searches || 0)}/${Number(shared.total_hits || 0)}`],
      ["ctx puts", String(Number(shared.total_puts || 0))],
      ["ctx injected", String(Number(shared.total_injected_blocks || 0))],
      ["template reuse", `${Number(shared.total_template_hits || 0)}/${Number(shared.total_template_searches || 0)}`],
      ["template writes", String(Number(shared.total_template_puts || 0))],
      ["failure reuse", `${Number(shared.total_failure_hits || 0)}/${Number(shared.total_failure_searches || 0)}`],
      ["failure writes", String(Number(shared.total_failure_puts || 0))],
      ["opt objective", optimize.objective || "--"],
      ["opt metric", optimize.execution_metric || "--"],
      ["opt best loss", optimize.best_loss != null ? Number(optimize.best_loss).toFixed(6) : "--"],
      ["opt trials", optimize.trials_run ? `${Number(optimize.trials_run || 0)}/${Number(optimize.max_trials || 0)}` : "--"],
      ["opt param trials", String(Number(optimize.parameterized_trials || 0))],
      ["opt variant trials", String(Number(optimize.primitive_change_trials || 0))],
      ["opt rollback trials", String(Number(optimize.rollback_trials || 0))],
      ["opt proposal trials", String(Number(optimize.proposal_selection_trials || 0))],
      ["opt proposal rejected", String(Number(optimize.proposal_rejected_trials || 0))],
      ["opt cached reuse", String(Number(optimize.cached_reuse_trials || 0))],
      ["opt reruns avoided", String(Number(optimize.cached_reruns_avoided || 0))],
      ["opt proposal counts", Object.keys(optimize.selected_proposal_counts || {}).length
        ? Object.entries(optimize.selected_proposal_counts || {}).map(([label, count]) => `${label}=${Number(count || 0)}`).join(", ")
        : "--"],
      ["opt skeleton trials", String(Number(optimize.skeleton_proposal_trials || 0))],
      ["opt skeleton accept/reject", `${Number(optimize.accepted_skeleton_proposals || 0)} / ${Number(optimize.rejected_skeleton_proposals || 0)}`],
      ["opt skeleton penalty mean", Number(optimize.mean_skeleton_complexity_penalty || 0).toFixed(3)],
      ["opt skeleton gain mean", Number(optimize.mean_skeleton_objective_gain || 0).toFixed(3)],
      ["opt skeleton retention", Number(optimize.skeleton_retention_rate || 0).toFixed(2)],
      ["opt proposal gain mean", Number(optimize.mean_selected_proposal_improvement || 0).toFixed(3)],
      ["opt proposal gain best", Number(optimize.best_selected_proposal_improvement || 0).toFixed(3)],
      ["opt expansion delta mean", Number(optimize.mean_expansion_loss_delta || 0).toFixed(3)],
      ["opt expansion delta worst", Number(optimize.worst_expansion_loss_delta || 0).toFixed(3)],
      ["opt topologies", `${Number(optimize.unique_topologies || 0)} / ${Number(optimize.unique_primitive_signatures || 0)}`],
      ["opt best trial", optimize.best_trial ? String(Number(optimize.best_trial || 0)) : "--"],
      ["opt history", optimize.trial_history_path || "--"],
      ["single-agent", singleAgent.termination_reason || "--"],
      ["single-agent verify", singleAgent.verification_status || "--"],
      ["single-agent budget", `${Number(singleAgent.steps_used || 0)}/${Number(singleAgent.step_budget || 0)}`],
      ["single-agent policy", `${singleAgentPolicy.decomposition_mode || "--"} / ${singleAgentPolicy.retrieval_intensity || "--"} / ${singleAgentPolicy.repair_policy || "--"}`],
      ["single-agent tools", `${Number(singleAgent.tool_dispatch_count_total || 0)} / ${Number(singleAgent.tool_latency_ms_total || 0).toFixed(1)}ms`],
      ["single-agent tool detail", singleAgentTools.length
        ? singleAgentTools.map((row) => `${row.name}:${Number(row.dispatches || 0)}@${Number(row.avg_latency_ms || 0).toFixed(1)}ms`).join(", ")
        : "--"],
      ["single-agent escalations", singleAgentEscalations.length
        ? singleAgentEscalations.map((row) => `${row.from}->${row.to}:${row.reason || "--"}`).join(", ")
        : "--"],
      ["single-agent artifacts", String(Number(singleAgent.artifact_count || 0))],
      ["single-agent manifest", singleAgent.artifact_manifest_path || "--"],
      ["single-agent failures", (singleAgent.open_failures || []).join(", ") || "--"],
      ["single-agent attempts", (singleAgent.attempt_history || []).join(", ") || "--"],
      ["single-agent artifact paths", (singleAgent.artifacts || []).map((row) => `${row.name}:${row.path}`).join(", ") || "--"],
      ["prompt bench", `${Number(benchmark.prompt_results || 0)}/${Number(benchmark.prompt_cases || 0)}`],
      ["benchmark status", benchmark.status || "--"],
      ["prompt stable", benchmark.prompt_stability_summary || "--"],
      ["prompt regress", `${Number(benchmark.prompt_tuned_failures || 0)} / ${Number(benchmark.prompt_tuned_unstable_groups || 0)}`],
      ["flow bench", `${Number(benchmark.flow_results || 0)}/${Number(benchmark.flow_cases || 0)}`],
      ["flow stable", benchmark.flow_stability_summary || "--"],
      ["flow gate summary", benchmark.flow_gate_summary || "--"],
      ["flow paths", Object.keys(benchmarkFlowPaths).length
        ? Object.entries(benchmarkFlowPaths.observed || {}).map(([variant, paths]) => `${variant}=${(paths || []).join("|")}`).join(", ")
        : "--"],
      ["flow path summary", benchmark.flow_execution_path_summary || "--"],
      ["flow prompt volume", benchmark.flow_prompt_volume_summary || "--"],
      ["runtime budget", `${Number(benchmarkRuntime.provider_count || 0)} providers / ${Number(benchmarkRuntime.transport_count || 0)} transports / ${Number((benchmarkRuntime.violations || []).length || 0)} violations`],
      ["runtime policy", `${Number((benchmarkOverridePolicy.missing_required_overrides || []).length || 0)} missing / ${Number((benchmarkOverridePolicy.unexpected_active_overrides || []).length || 0)} unexpected`],
      ["runtime policy summary", benchmark.runtime_override_policy_summary || "--"],
      ["benchmark health", benchmark.health_summary || "--"],
      ["benchmark warnings", benchmark.warning_summary || "--"],
      ["benchmark warn check", benchmark.top_warning_subcheck || "--"],
      ["benchmark top warn", benchmark.top_warning || "--"],
      ["single-agent prune warn", benchmark.top_warning_subcheck === "single_agent_prune" ? (benchmark.top_warning || "--") : "--"],
      ["benchmark failures", benchmark.failure_summary || "--"],
      ["benchmark subcheck", benchmark.top_failed_subcheck || "--"],
      ["benchmark top fail", benchmark.top_failure || "--"],
      ["release warnings", benchmark.release_warning_summary || "--"],
      ["release warning counts", `${Number(benchmark.release_runtime_warning_count || 0)} runtime / ${Number(benchmark.release_catalog_warning_count || 0)} catalog / ${Number(benchmark.release_benchmark_warning_count || 0)} benchmark`],
      ["release top warn", `${benchmark.release_top_runtime_warning || "--"} | ${benchmark.release_top_catalog_warning || "--"} | ${benchmark.release_top_benchmark_warning_subcheck || "--"}:${benchmark.release_top_benchmark_warning || "--"}`],
      ["release failures", benchmark.release_failure_summary || "--"],
      ["release failed check", benchmark.release_top_failed_check || "--"],
      ["release bench check", benchmark.release_top_benchmark_subcheck || "--"],
      ["release top fail", `${benchmark.release_top_benchmark_failure || "--"} | ${benchmark.release_top_runtime_failure || "--"} | ${benchmark.release_top_catalog_failure || "--"}`],
      ["runtime modes", Object.keys(benchmarkRuntimeModes).length
        ? Object.entries(benchmarkRuntimeModes).map(([mode, row]) => `${mode}=${Number(row.provider_count || 0)}/${Number(row.provider_model_count || 0)}/${Number(row.transport_count || 0)}`).join(", ")
        : "--"],
      ["flow prompts", Object.keys(flowPromptCalls).length
        ? Object.entries(flowPromptCalls).map(([k, v]) => `${k}=${Number(v).toFixed(1)}`).join(", ")
        : "--"],
      ["flow planner tools", Object.keys(flowPlannerTools).length
        ? Object.entries(flowPlannerTools).map(([k, v]) => `${k}=${Number(v).toFixed(1)}`).join(", ")
        : "--"],
      ["flow planner escalations", Object.keys(flowPlannerEscalations).length
        ? Object.entries(flowPlannerEscalations).map(([k, v]) => `${k}=${Number(v).toFixed(1)}`).join(", ")
        : "--"],
      ["flow latency", Object.keys(flowLatency).length
        ? Object.entries(flowLatency).map(([k, v]) => `${k}=${Number(v).toFixed(1)}ms`).join(", ")
        : "--"],
      ["prompt latency", Object.keys(promptLatency).length
        ? Object.entries(promptLatency).map(([k, v]) => `${k}=${Number(v).toFixed(1)}ms`).join(", ")
        : "--"],
      ["flow gate", `${(benchmark.flow_required_variants || []).join(",") || "--"} | ${Number(benchmark.flow_mode_failures || 0)} / ${Number(benchmark.flow_mode_unstable_groups || 0)}`],
      ["flow compare", `${(benchmark.flow_comparison_variants || []).join(",") || "--"} | ${Number(benchmark.flow_comparison_failures || 0)} / ${Number(benchmark.flow_comparison_unstable_groups || 0)}`],
      ["single-agent bench", singleAgentComparisonSummary || "--"],
      ["single-agent route", singleAgentComparison.dominant_planner_termination_reason
        ? `${singleAgentComparison.dominant_planner_termination_reason} / ${singleAgentComparison.planner_action_summary || "--"}`
        : "--"],
      ["single-agent route signature", singleAgentComparison.dominant_planner_action_signature || "--"],
      ["single-agent overhead", singleAgentComparison.overhead_driver
        ? `${singleAgentComparison.overhead_driver} / ${singleAgentComparison.prune_recommendation || "keep_current"} / ${Number(singleAgentComparison.avg_planner_tool_dispatches || 0).toFixed(1)} tools / ${Number(singleAgentComparison.avg_planner_escalations || 0).toFixed(1)} escalations`
        : "--"],
      ["release", benchmark.release_status || "--"],
      ["started", fmtTime(run.started_at)],
      ["updated", fmtTime(run.last_update_at)]
    ];
    summary.innerHTML = "";
    metrics.forEach(([k, v]) => {
      const m = document.createElement("div");
      m.className = "metric";
      m.innerHTML = `<div class="k">${k}</div><div class="v">${v}</div>`;
      summary.appendChild(m);
    });

    const metadata = JSON.stringify(run.metadata || {}, null, 2);
    const stale = (run.stale_stages || []).map((s) => `- ${s.stage}: ${Number(s.heartbeat_age_sec || 0).toFixed(1)}s (${s.message || ""})`).join("\n");
    metaBlock.textContent =
      `run_id: ${run.run_id || ""}\n` +
      `error: ${run.error || "(none)"}\n` +
      `hung: ${run.is_hung ? "yes" : "no"}\n` +
      `provider_complexity: ${JSON.stringify(run.provider_complexity || {}, null, 2)}\n` +
      `architect_summary: ${JSON.stringify(run.architect_summary || {}, null, 2)}\n` +
      `hunter_summary: ${JSON.stringify(run.hunter_summary || {}, null, 2)}\n` +
      `single_agent_summary: ${JSON.stringify(run.single_agent_summary || {}, null, 2)}\n` +
      `catalog_alignment_summary: ${JSON.stringify(run.catalog_alignment_summary || {}, null, 2)}\n` +
      `shared_context_summary: ${JSON.stringify(run.shared_context_summary || {}, null, 2)}\n` +
      `optimize_summary: ${JSON.stringify(run.optimize_summary || {}, null, 2)}\n` +
      `benchmark_summary: ${JSON.stringify(run.benchmark_summary || {}, null, 2)}\n` +
      `\nmetadata:\n${metadata}` +
      (stale ? `\n\nstale_stages:\n${stale}` : "");
  }

  if (btnOpenEvolution) {
    btnOpenEvolution.addEventListener("click", () => {
      if (!selectedRunId) return;
      window.open(`/?evolution_run=${encodeURIComponent(selectedRunId)}`, "_blank");
    });
  }

  function setStages(run) {
    const rows = Object.entries(run.stages || {});
    rows.sort((a, b) => Number((a[1] || {}).started_at || 0) - Number((b[1] || {}).started_at || 0));
    const header = "<thead><tr><th>stage</th><th>status</th><th>progress</th><th>heartbeat</th><th>message</th></tr></thead>";
    const body = rows.map(([name, row]) => {
      const status = String((row || {}).status || "running");
      const cls = status === "completed" ? "ok" : status === "failed" ? "bad" : "warn";
      const completed = Number((row || {}).completed || 0);
      const total = Number((row || {}).total || 0);
      const progress = total > 0 ? `${completed}/${total}` : String(completed);
      return `<tr><td class="mono">${name}</td><td class="${cls}">${status}</td><td class="mono">${progress}</td><td class="mono">${fmtAgeSec(Number((row || {}).last_heartbeat_at || 0))}</td><td>${String((row || {}).message || "")}</td></tr>`;
    }).join("");
    stagesTable.innerHTML = header + "<tbody>" + body + "</tbody>";
  }

  function setPrompts(run) {
    const prompts = Object.entries(run.prompt_by_key || {});
    prompts.sort((a, b) => Number((b[1] || {}).dispatched || 0) - Number((a[1] || {}).dispatched || 0));
    const header = "<thead><tr><th>prompt key</th><th>provider/model</th><th>dispatched</th><th>ok</th><th>fail</th><th>inflight</th><th>avg ms</th></tr></thead>";
    const body = prompts.map(([key, row]) => {
      const providerModel = [String((row||{}).provider||""), String((row||{}).model||"")].filter(Boolean).join("/") || "--";
      return `<tr><td class="mono">${key}</td><td class="mono">${providerModel}</td><td class="mono">${Number((row||{}).dispatched||0)}</td><td class="ok mono">${Number((row||{}).succeeded||0)}</td><td class="bad mono">${Number((row||{}).failed||0)}</td><td class="warn mono">${Number((row||{}).inflight||0)}</td><td class="mono">${Number((row||{}).avg_latency_ms||0).toFixed(1)}</td></tr>`;
    }).join("");
    promptsTable.innerHTML = header + "<tbody>" + body + "</tbody>";
  }

  function setInflight(run) {
    const inflight = Object.values(run.inflight_prompts || {});
    inflight.sort((a, b) => Number((a||{}).started_at||0) - Number((b||{}).started_at||0));
    const header = "<thead><tr><th>dispatch</th><th>prompt key</th><th>provider/model</th><th>stage</th><th>age</th></tr></thead>";
    const body = inflight.map((row) => {
      const providerModel = [row.provider||"", row.model||""].filter(Boolean).join("/") || "--";
      return `<tr><td class="mono">${String(row.dispatch_id||"").slice(0,10)}</td><td class="mono">${String(row.prompt_key||"")}</td><td class="mono">${providerModel}</td><td class="mono">${String(row.stage||"")}</td><td class="mono">${fmtAgeSec(Number(row.started_at||0))}</td></tr>`;
    }).join("");
    inflightTable.innerHTML = header + "<tbody>" + body + "</tbody>";
  }

  // ─── Timeline tab ───
  function renderTimelineEvent(ev) {
    const div = document.createElement("div");
    const isError = ev.event_type && (ev.event_type.includes("ERROR") || ev.event_type.includes("FAIL"));
    div.className = "timeline-event " + phaseClass(ev.round || "") + (isError ? " is-error" : "");
    const parts = [
      `<span class="tl-ts">${fmtTimeFull(ev.timestamp)}</span>`,
      `<span class="pill ${runStatusClass(ev.round === "llm" ? "running" : "completed")}" style="font-size:10px;padding:1px 6px;">${esc(ev.round || "")}</span>`,
      `<span class="tl-type">${esc(ev.event_type || "")}</span>`,
    ];
    if (ev.prompt_key) parts.push(`<span class="tl-detail">${esc(ev.prompt_key)}</span>`);
    if (ev.node_id) parts.push(`<span class="tl-detail">node=${esc(ev.node_id)}</span>`);
    if (ev.provider) parts.push(`<span class="tl-detail">${esc(ev.provider)}${ev.model ? "/" + esc(ev.model) : ""}</span>`);
    if (ev.duration_ms) parts.push(`<span class="tl-duration">${Number(ev.duration_ms).toFixed(1)}ms</span>`);
    let html = `<div class="tl-header">${parts.join(" ")}</div>`;
    if (ev.payload && Object.keys(ev.payload).length) {
      const payloadStr = JSON.stringify(ev.payload, null, 2);
      if (payloadStr.length > 4) {
        html += `<div class="tl-payload" onclick="this.classList.toggle('expanded')">${esc(payloadStr)}</div>`;
      }
    }
    div.innerHTML = html;
    return div;
  }

  function getTimelineFilters() {
    const search = tlSearch.value.trim();
    const filters = {};
    if (tlRoundFilter.value) filters.round = tlRoundFilter.value;
    if (tlTypeFilter.value) filters.event_type = tlTypeFilter.value;
    if (search) filters.prompt_key = search;
    return filters;
  }

  async function loadTimeline(append) {
    if (!selectedRunId) {
      btnOpenEvolution.classList.add("is-hidden");
      timelineList.innerHTML = '<div class="run-meta" style="padding:10px;">Select a run.</div>';
      return;
    }
    if (!append) {
      timelineOffset = 0;
      timelineList.innerHTML = "";
    }
    try {
      const data = await fetchEvents(selectedRunId, timelineOffset, TIMELINE_PAGE, getTimelineFilters());
      const events = data.events || [];
      events.forEach((ev) => timelineList.appendChild(renderTimelineEvent(ev)));
      timelineOffset += events.length;
      tlCount.textContent = `${timelineOffset} / ${data.total} events`;
      tlMoreWrap.style.display = timelineOffset < data.total ? "" : "none";

      // Populate type filter from first page
      if (!append && events.length) {
        const types = new Set(events.map((e) => e.event_type).filter(Boolean));
        const current = tlTypeFilter.value;
        tlTypeFilter.innerHTML = '<option value="">all types</option>';
        [...types].sort().forEach((t) => {
          const opt = document.createElement("option");
          opt.value = t;
          opt.textContent = t;
          if (t === current) opt.selected = true;
          tlTypeFilter.appendChild(opt);
        });
      }
    } catch (err) {
      timelineList.innerHTML = `<div class="run-meta bad" style="padding:10px;">${esc(err.message)}</div>`;
    }
  }

  tlLoadMore.addEventListener("click", () => loadTimeline(true));
  tlRoundFilter.addEventListener("change", () => loadTimeline());
  tlTypeFilter.addEventListener("change", () => loadTimeline());
  let tlSearchTimer;
  tlSearch.addEventListener("input", () => {
    clearTimeout(tlSearchTimer);
    tlSearchTimer = setTimeout(() => loadTimeline(), 300);
  });

  // ─── SSE live streaming ───
  function closeSSE() {
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }
    sseDot.classList.remove("connected");
    tlLive.checked = false;
  }

  function openSSE() {
    closeSSE();
    if (!selectedRunId) return;
    const url = `/api/dashboard/runs/${encodeURIComponent(selectedRunId)}/stream`;
    activeEventSource = new EventSource(url);
    sseDot.classList.add("connected");

    activeEventSource.addEventListener("PROMPT_DISPATCH_START", handleSSEEvent);
    activeEventSource.addEventListener("PROMPT_DISPATCH_DONE", handleSSEEvent);
    activeEventSource.addEventListener("PROMPT_DISPATCH_ERROR", handleSSEEvent);
    activeEventSource.addEventListener("event", handleSSEEvent);
    // Catch any event type
    activeEventSource.onmessage = handleSSEEvent;

    activeEventSource.addEventListener("done", () => {
      sseDot.classList.remove("connected");
    });

    activeEventSource.onerror = () => {
      sseDot.classList.remove("connected");
    };
  }

  function handleSSEEvent(e) {
    try {
      const ev = JSON.parse(e.data);
      // Apply filters
      const filters = getTimelineFilters();
      if (filters.round && ev.round !== filters.round) return;
      if (filters.event_type && ev.event_type !== filters.event_type) return;
      if (filters.prompt_key && !(ev.prompt_key || "").includes(filters.prompt_key) && !(ev.node_id || "").includes(filters.prompt_key)) return;

      const el = renderTimelineEvent(ev);
      timelineList.appendChild(el);
      // Auto-scroll if near bottom
      const tabBody = timelineList.closest(".tab-body");
      if (tabBody && tabBody.scrollHeight - tabBody.scrollTop - tabBody.clientHeight < 100) {
        el.scrollIntoView({ behavior: "smooth", block: "end" });
      }
    } catch (_) {}
  }

  tlLive.addEventListener("change", () => {
    if (tlLive.checked) {
      openSSE();
    } else {
      closeSSE();
    }
  });

  // ─── Coverage tab ───
  async function loadCoverage() {
    if (!selectedRunId) {
      coverageSummary.innerHTML = "";
      coverageTable.innerHTML = "";
      return;
    }
    try {
      const data = await fetchCoverage(selectedRunId);
      const pct = data.overall_deterministic_pct || 0;
      const pctColor = pct >= 60 ? "ok" : pct >= 30 ? "warn" : "bad";
      coverageSummary.innerHTML = [
        `<div><div class="coverage-big ${pctColor}">${pct.toFixed(1)}%</div><div class="coverage-label">deterministic</div></div>`,
        `<div><div class="coverage-big">${data.total_dispatches || 0}</div><div class="coverage-label">total dispatches</div></div>`,
      ].join("");

      const header = "<thead><tr><th>prompt key</th><th>total</th><th>deterministic</th><th>llm fallback</th><th>det %</th><th>coverage</th><th>avg ms</th><th>errors</th></tr></thead>";
      const body = (data.prompt_keys || []).map((row) => {
        const p = row.deterministic_pct || 0;
        const cls = p >= 80 ? "ok" : p >= 40 ? "warn" : "bad";
        return `<tr>
          <td class="mono">${esc(row.prompt_key)}</td>
          <td class="mono">${row.total_dispatches}</td>
          <td class="mono ok">${row.deterministic_count}</td>
          <td class="mono warn">${row.llm_fallback_count}</td>
          <td class="mono ${cls}">${p.toFixed(1)}%</td>
          <td><div class="coverage-bar-bg"><div class="coverage-bar-fill" style="width:${p}%"></div></div></td>
          <td class="mono">${row.avg_latency_ms.toFixed(1)}</td>
          <td class="mono ${row.error_count ? 'bad' : ''}">${row.error_count}</td>
        </tr>`;
      }).join("");
      coverageTable.innerHTML = header + "<tbody>" + body + "</tbody>";
    } catch (err) {
      coverageSummary.innerHTML = `<div class="bad">${esc(err.message)}</div>`;
    }
  }

  // ─── Errors tab ───
  async function loadErrors() {
    if (!selectedRunId) {
      errorSummary.innerHTML = "";
      errorList.innerHTML = "";
      return;
    }
    try {
      const data = await fetchErrorDrilldown(selectedRunId);
      const count = data.error_count || 0;
      errorSummary.innerHTML = `<span class="${count ? 'bad' : 'ok'}">${count} error${count !== 1 ? "s" : ""}</span> in run <span class="mono">${esc((selectedRunId || "").slice(0, 12))}</span>`;

      if (!count) {
        errorList.innerHTML = '<div class="run-meta" style="padding:10px;">No errors recorded.</div>';
        return;
      }
      errorList.innerHTML = "";
      (data.errors || []).forEach((err) => {
        const card = document.createElement("div");
        card.className = "error-card";
        let html = `<div class="error-header">
          <span class="tl-ts">${fmtTimeFull(err.timestamp)}</span>
          <span class="pill s-failed" style="font-size:10px;padding:1px 6px;">${esc(err.event_type)}</span>
          <span class="mono">${esc(err.prompt_key || "")}</span>
          <span class="tl-detail">${esc(err.provider || "")}${err.model ? "/" + esc(err.model) : ""}</span>
          <span class="tl-detail">stage=${esc(err.stage || "")}</span>
        </div>`;
        html += `<div class="error-msg">${esc(err.error_message)}</div>`;
        if (err.retry_count > 0) {
          const retries = (err.retry_history || []).map((r) =>
            `attempt ${r.attempt}: ${fmtTimeFull(r.timestamp)} - ${esc(r.error || "ok")}`
          ).join("\n");
          html += `<div class="error-retry">${err.retry_count} retries:\n${retries}</div>`;
        }
        card.innerHTML = html;
        errorList.appendChild(card);
      });
    } catch (err) {
      errorSummary.innerHTML = `<div class="bad">${esc(err.message)}</div>`;
    }
  }

  // ─── Load selected run ───
  async function loadSelected() {
    if (!selectedRunId) {
      if (btnOpenEvolution) btnOpenEvolution.classList.add("is-hidden");
      summary.innerHTML = "";
      metaBlock.textContent = "Select a run to inspect details.";
      stagesTable.innerHTML = "";
      promptsTable.innerHTML = "";
      inflightTable.innerHTML = "";
      return;
    }
    try {
      const run = await fetchRun(selectedRunId);
      setSummary(run);
      setStages(run);
      setPrompts(run);
      setInflight(run);
    } catch (err) {
      metaBlock.textContent = `Failed to load run ${selectedRunId}: ${err.message}`;
    }
    // Reload active tab data
    if (activeTab === "timeline") loadTimeline();
    if (activeTab === "coverage") loadCoverage();
    if (activeTab === "errors") loadErrors();
    // Close SSE if run changed
    if (activeEventSource && tlLive.checked) openSSE();
  }

  // ─── Refresh loop ───
  async function refresh() {
    try {
      latestRuns = await fetchRuns();
      if (!selectedRunId && latestRuns.length) {
        selectedRunId = latestRuns[0].run_id || "";
      }
      if (selectedRunId && !latestRuns.some((r) => r.run_id === selectedRunId)) {
        selectedRunId = latestRuns.length ? (latestRuns[0].run_id || "") : "";
      }
      renderRunList(latestRuns);
      await loadSelected();
      lastRefresh.textContent = `refresh: ${new Date().toLocaleTimeString()}`;
    } catch (err) {
      lastRefresh.textContent = `refresh error: ${err.message}`;
    }
  }

  btnRefresh.addEventListener("click", refresh);
  stateFilter.addEventListener("change", refresh);

  refresh();
  setInterval(refresh, 2000);
})();
