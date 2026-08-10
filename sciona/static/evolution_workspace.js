(function (global) {
  "use strict";

  function asNumber(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function graphDiff(before, after) {
    var oldNodes = {};
    var newNodes = {};
    (before.nodes || []).forEach(function (node) { oldNodes[node.node_id] = node; });
    (after.nodes || []).forEach(function (node) { newNodes[node.node_id] = node; });
    var added = Object.keys(newNodes).filter(function (id) { return !oldNodes[id]; });
    var removed = Object.keys(oldNodes).filter(function (id) { return !newNodes[id]; });
    var changed = Object.keys(newNodes).filter(function (id) {
      if (!oldNodes[id]) return false;
      return ["name", "status", "concept_type", "matched_primitive", "type_signature"].some(function (key) {
        return oldNodes[id][key] !== newNodes[id][key];
      });
    });
    return {
      added_nodes: added.map(function (id) { return newNodes[id]; }),
      removed_nodes: removed.map(function (id) { return oldNodes[id]; }),
      changed_nodes: changed.map(function (id) { return { node_id: id }; }),
      added_edges: [],
      removed_edges: []
    };
  }

  global.initEvolutionWorkspace = function initEvolutionWorkspace(options) {
    var root = document.getElementById("evolution-workspace");
    var tabs = document.getElementById("evolution-tabs");
    var operation = document.getElementById("evolution-operation");
    var loss = document.getElementById("evolution-loss");
    var delta = document.getElementById("evolution-delta");
    var diff = document.getElementById("evolution-diff");
    var guidance = document.getElementById("evolution-guidance");
    var guidanceStatus = document.getElementById("evolution-guidance-status");
    var btnPrevious = document.getElementById("btn-evolution-previous");
    var btnNext = document.getElementById("btn-evolution-next");
    var btnRefine = document.getElementById("btn-evolution-refine");
    var btnReject = document.getElementById("btn-evolution-reject");
    var trace = { versions: [], transitions: [] };
    var activeIndex = -1;
    var refinementSequence = 0;

    function transitionForTarget(versionId) {
      return (trace.transitions || []).find(function (item) {
        return item.target_version_id === versionId;
      }) || null;
    }

    function parentVersionId(version) {
      if (version.parent_version_id) return version.parent_version_id;
      var transition = transitionForTarget(version.version_id);
      return transition ? transition.source_version_id : "";
    }

    function activeLineage() {
      if (activeIndex < 0) return [];
      var lineage = [];
      var current = trace.versions[activeIndex];
      var visited = {};
      while (current && !visited[current.version_id]) {
        visited[current.version_id] = true;
        lineage.unshift(current);
        var parentId = parentVersionId(current);
        current = trace.versions.find(function (item) {
          return item.version_id === parentId;
        });
      }
      return lineage;
    }

    function formatLoss(value) {
      var number = asNumber(value);
      return number == null ? "not evaluated" : number.toFixed(6);
    }

    function renderTransition(version) {
      var item = transitionForTarget(version.version_id);
      if (!item) {
        operation.textContent = version.phase === "initial_match" ? "Catalog match" : "Baseline";
        loss.textContent = "Loss " + formatLoss(version.loss);
        delta.textContent = "Baseline";
        delta.className = "evolution-metric";
        diff.textContent = "Initial graph";
        return;
      }
      var itemDelta = asNumber(item.loss_delta);
      operation.textContent = (item.operation || "refinement").replace(/_/g, " ");
      loss.textContent = "Loss " + formatLoss(item.candidate_loss);
      delta.textContent = itemDelta == null ? "Delta pending" : "Delta " + (itemDelta > 0 ? "+" : "") + itemDelta.toFixed(6);
      delta.className = "evolution-metric " + (itemDelta == null ? "" : itemDelta <= 0 ? "is-improved" : "is-regressed");
      var itemDiff = item.graph_diff || {};
      diff.textContent = "+" + (itemDiff.added_nodes || []).length + " nodes  -" +
        (itemDiff.removed_nodes || []).length + " nodes  " +
        (itemDiff.changed_nodes || []).length + " changed";
    }

    function renderTabs() {
      tabs.innerHTML = "";
      var lineage = activeLineage();
      lineage.forEach(function (version, lineageIndex) {
        var index = trace.versions.indexOf(version);
        var button = document.createElement("button");
        button.type = "button";
        button.className = "evolution-tab" + (index === activeIndex ? " active" : "") +
          (version.status === "rejected" ? " is-rejected" : "");
        button.setAttribute("data-version-id", version.version_id);
        var tabIndex = document.createElement("span");
        tabIndex.className = "evolution-tab-index";
        tabIndex.textContent = String(lineageIndex + 1);
        var tabCopy = document.createElement("span");
        tabCopy.className = "evolution-tab-copy";
        var tabLabel = document.createElement("strong");
        tabLabel.textContent = version.label || ("Version " + (index + 1));
        var tabLoss = document.createElement("small");
        tabLoss.textContent = formatLoss(version.loss);
        tabCopy.appendChild(tabLabel);
        tabCopy.appendChild(tabLoss);
        button.appendChild(tabIndex);
        button.appendChild(tabCopy);
        button.addEventListener("click", function () { selectVersion(index); });
        tabs.appendChild(button);
        if (lineageIndex < lineage.length - 1) {
          var connector = document.createElement("span");
          connector.className = "evolution-tab-connector";
          connector.textContent = ">";
          tabs.appendChild(connector);
        }
      });
    }

    function selectVersion(index) {
      if (index < 0 || index >= trace.versions.length) return;
      activeIndex = index;
      var version = trace.versions[index];
      options.loadGraph(version.graph);
      renderTabs();
      renderTransition(version);
      var lineage = activeLineage();
      var lineageIndex = lineage.indexOf(version);
      btnPrevious.disabled = lineageIndex <= 0;
      btnNext.disabled = lineageIndex < 0 || lineageIndex === lineage.length - 1;
      guidanceStatus.textContent = version.guidance_status || "";
      if (options.onVersionSelected) options.onVersionSelected(version);
    }

    function setVersionEvaluation(versionId, evaluation, runId) {
      var version = trace.versions.find(function (item) {
        return item.version_id === versionId;
      });
      if (!version) return;
      version.loss = asNumber(evaluation && evaluation.loss);
      version.evaluation = evaluation || null;
      if (version.status === "candidate") version.status = "accepted";
      version.guidance_status = version.loss == null
        ? "Evaluation completed without a scalar loss."
        : "Evaluation complete. Loss " + formatLoss(version.loss) + ".";
      if (runId) version.run_id = runId;
      (trace.transitions || []).forEach(function (item) {
        var source = trace.versions.find(function (candidate) {
          return candidate.version_id === item.source_version_id;
        });
        var target = trace.versions.find(function (candidate) {
          return candidate.version_id === item.target_version_id;
        });
        item.baseline_loss = source ? asNumber(source.loss) : null;
        item.candidate_loss = target ? asNumber(target.loss) : null;
        item.loss_delta = item.baseline_loss != null && item.candidate_loss != null
          ? item.candidate_loss - item.baseline_loss
          : null;
      });
      renderTabs();
      if (activeIndex >= 0) {
        renderTransition(trace.versions[activeIndex]);
        if (trace.versions[activeIndex].version_id === versionId) {
          guidanceStatus.textContent = version.guidance_status;
        }
      }
    }

    function loadTrace(nextTrace) {
      trace = nextTrace && Array.isArray(nextTrace.versions) ? nextTrace : { versions: [], transitions: [] };
      if (!trace.transitions) trace.transitions = [];
      trace.versions.forEach(function (version) {
        if (!version.parent_version_id) version.parent_version_id = parentVersionId(version);
        if (!version.status) version.status = "accepted";
      });
      if (!trace.versions.length) {
        root.classList.add("hidden");
        return;
      }
      root.classList.remove("hidden");
      selectVersion(trace.versions.length - 1);
    }

    function start(graph, metadata) {
      var meta = metadata || {};
      loadTrace({
        schema_version: "1.0",
        goal: (graph.metadata || {}).goal || "",
        objective: meta.objective || "",
        versions: [{
          version_id: meta.version_id || "initial-match",
          label: meta.label || "Initial match",
          phase: meta.phase || "initial_match",
          loss: asNumber(meta.loss),
          graph: graph
        }],
        transitions: []
      });
    }

    function recordTransition(graph, metadata) {
      if (!trace.versions.length) {
        start(graph, metadata);
        return;
      }
      var meta = metadata || {};
      var source = trace.versions[activeIndex];
      var versionId = meta.version_id || "version-" + (trace.versions.length + 1);
      var next = {
        version_id: versionId,
        label: meta.label || "Refinement " + trace.versions.length,
        phase: meta.phase || "refinement",
        loss: asNumber(meta.loss),
        parent_version_id: source.version_id,
        status: meta.status || "candidate",
        guidance: meta.human_guidance ? { action: "refine", note: meta.human_guidance } : null,
        graph: graph
      };
      trace.versions.push(next);
      trace.transitions.push({
        transition_id: source.version_id + "--" + versionId,
        source_version_id: source.version_id,
        target_version_id: versionId,
        operation: meta.operation || "human_guided_refinement",
        status: "accepted",
        baseline_loss: source.loss,
        candidate_loss: next.loss,
        loss_delta: asNumber(source.loss) != null && asNumber(next.loss) != null ? next.loss - source.loss : null,
        rules_applied: meta.rules_applied || [],
        selection_reason: meta.selection_reason || "",
        graph_diff: graphDiff(source.graph, graph),
        human_guidance: meta.human_guidance || guidance.value.trim()
      });
      guidance.value = "";
      activeIndex = trace.versions.length - 1;
      selectVersion(activeIndex);
      if (options.onVersionCreated) options.onVersionCreated(next);
      return next;
    }

    function requestRefinement() {
      if (activeIndex < 0 || !options.onRefineRequest) return;
      var source = trace.versions[activeIndex];
      var note = guidance.value.trim();
      btnRefine.disabled = true;
      guidanceStatus.textContent = "Generating a deterministic proposal...";
      Promise.resolve(options.onRefineRequest(source, note)).then(function (proposal) {
        refinementSequence += 1;
        var next = recordTransition(proposal.updated_cdg, {
          version_id: proposal.version_id || source.version_id + "-branch-" + refinementSequence,
          label: proposal.label || "Guided refinement " + refinementSequence,
          phase: "refinement",
          operation: proposal.operation || "human_guided_refinement",
          rules_applied: proposal.rules_applied || [],
          selection_reason: proposal.selection_reason || "",
          human_guidance: note,
          status: "candidate"
        });
        next.proposal_candidates = proposal.candidates || [];
        guidance.value = "";
        guidanceStatus.textContent = "Candidate created and queued for evaluation.";
      }).catch(function (error) {
        guidanceStatus.textContent = error.message || "Refinement request failed.";
      }).finally(function () {
        btnRefine.disabled = false;
      });
    }

    function recordGuidance(action) {
      if (activeIndex < 0) return;
      var version = trace.versions[activeIndex];
      var note = guidance.value.trim();
      version.guidance = { action: action, note: note };
      version.guidance_status = action === "reject"
        ? "Version rejected. Returned to its parent graph."
        : "Guidance recorded on this graph.";
      guidanceStatus.textContent = version.guidance_status;
      if (action === "reject" && activeIndex > 0) {
        version.status = "rejected";
        var transition = transitionForTarget(version.version_id);
        if (transition) transition.status = "rejected";
        var parentId = parentVersionId(version);
        var branchIndex = trace.versions.findIndex(function (candidate) {
          return candidate.version_id === parentId;
        });
        if (branchIndex >= 0) selectVersion(branchIndex);
      }
      if (options.onGuidance) options.onGuidance(version, version.guidance);
    }

    btnPrevious.addEventListener("click", function () {
      var lineage = activeLineage();
      var position = lineage.indexOf(trace.versions[activeIndex]);
      if (position > 0) selectVersion(trace.versions.indexOf(lineage[position - 1]));
    });
    btnNext.addEventListener("click", function () {
      var lineage = activeLineage();
      var position = lineage.indexOf(trace.versions[activeIndex]);
      if (position >= 0 && position < lineage.length - 1) {
        selectVersion(trace.versions.indexOf(lineage[position + 1]));
      }
    });
    btnRefine.addEventListener("click", requestRefinement);
    btnReject.addEventListener("click", function () { recordGuidance("reject"); });

    return {
      loadTrace: loadTrace,
      start: start,
      recordTransition: recordTransition,
      setVersionEvaluation: setVersionEvaluation,
      selectVersionById: function (versionId) {
        var index = trace.versions.findIndex(function (version) {
          return version.version_id === versionId;
        });
        if (index >= 0) selectVersion(index);
      },
      setGuidanceStatus: function (message) {
        guidanceStatus.textContent = message || "";
      },
      getTrace: function () { return trace; },
      getActiveVersion: function () { return activeIndex >= 0 ? trace.versions[activeIndex] : null; }
    };
  };
})(window);
