(function () {
  "use strict";

  var CONCEPT_FAMILY = {
    sorting: "math", searching: "math", divide_and_conquer: "math",
    greedy: "math", dynamic_programming: "math", combinatorics: "math",
    algebra: "math", analysis: "math", arithmetic: "math",
    number_theory: "math", geometry: "math", set_theory: "math",
    sampler: "prob", log_prob: "prob", posterior_update: "prob",
    variational_inference: "prob", prior_init: "prob",
    prior_distribution: "prob", likelihood_evaluation: "prob",
    probabilistic_oracle: "prob", oracle_gradient: "prob",
    mcmc_kernel: "prob", mcmc_proposal: "prob", vi_elbo: "prob",
    conjugate_update: "prob",
    signal_filter: "signal", signal_transform: "signal",
    graph_signal_processing: "signal", sequential_filter: "signal",
    smc_reweight: "signal",
    state_init: "orch", data_assembly: "orch",
    conditional_routing: "orch", data_extraction: "orch",
    visualization: "pres", observability: "pres",
    custom: "other", external_tool: "other",
    message_passing: "other", neural_network: "other"
  };

  var FAMILY_COLORS = {
    math:   { bg: "#bbdefb", border: "#1976d2", text: "#0d47a1" },
    prob:   { bg: "#e1bee7", border: "#8e24aa", text: "#4a148c" },
    signal: { bg: "#b2dfdb", border: "#00897b", text: "#004d40" },
    orch:   { bg: "#ffe0b2", border: "#f57c00", text: "#e65100" },
    pres:   { bg: "#c8e6c9", border: "#43a047", text: "#1b5e20" },
    other:  { bg: "#e0e0e0", border: "#757575", text: "#424242" }
  };

  var FAMILY_LABELS = {
    math: "Math / Algo",
    prob: "Probabilistic",
    signal: "Signal",
    orch: "Orchestration",
    pres: "Presentation",
    other: "Other"
  };

  var STATUS_SHAPES = {
    atomic: "ellipse",
    decomposed: "round-rectangle",
    external: "diamond",
    pending: "ellipse",
    rejected: "cut-rectangle",
    high_risk: "triangle"
  };

  var btnOpen = document.getElementById("btn-open");
  var btnDashboard = document.getElementById("btn-dashboard");
  var fileInput = document.getElementById("file-input");

  function getNodeColors(conceptType) {
    var family = CONCEPT_FAMILY[conceptType] || "other";
    return FAMILY_COLORS[family] || FAMILY_COLORS.other;
  }

  function localApiAvailable() {
    return Boolean(window.location && window.location.protocol !== "file:");
  }

  function handleFile(file, graphControls) {
    var reader = new FileReader();
    reader.onload = function (e) {
      try {
        graphControls.validateAndLoad(JSON.parse(e.target.result));
      } catch (err) {
        var statusText = document.getElementById("status-text");
        if (statusText) statusText.textContent = "Error: invalid JSON — " + err.message;
      }
    };
    reader.readAsText(file);
  }

  var TUTORIAL_A_CDG = {
    nodes: [
      {
        node_id: "condition",
        name: "Condition Sampled Waveform",
        description: "Remove drift and high-frequency noise from a sampled waveform.",
        concept_type: "signal_filter",
        status: "atomic",
        matched_primitive: "sciona.atoms.signal_processing.biosppy.ecg.bandpass_filter",
        inputs: [
          { name: "signal", type_desc: "np.ndarray", constraints: "one-dimensional" },
          { name: "sampling_rate", type_desc: "float", constraints: "positive" }
        ],
        outputs: [{ name: "filtered", type_desc: "np.ndarray", constraints: "sample aligned" }],
        depth: 1
      },
      {
        node_id: "detect",
        name: "Detect Recurring Events",
        description: "Locate recurring waveform events using the selected detector atom.",
        concept_type: "signal_transform",
        status: "atomic",
        matched_primitive: "sciona.atoms.signal_processing.biosppy.ecg_detectors.atoms.hamilton_segmentation",
        inputs: [
          { name: "signal", type_desc: "np.ndarray", constraints: "sample aligned" },
          { name: "sampling_rate", type_desc: "float", constraints: "positive" }
        ],
        outputs: [{ name: "events", type_desc: "np.ndarray", constraints: "monotonic sample indices" }],
        depth: 1
      },
      {
        node_id: "measure",
        name: "Measure Event Rate",
        description: "Convert recurring event intervals into aligned instantaneous rates.",
        concept_type: "signal_transform",
        status: "atomic",
        matched_primitive: "sciona.atoms.signal_processing.biosppy.ecg.heart_rate_computation",
        inputs: [
          { name: "rpeaks", type_desc: "np.ndarray", constraints: "monotonic sample indices" },
          { name: "sampling_rate", type_desc: "float", constraints: "positive" }
        ],
        outputs: [
          { name: "indices", type_desc: "np.ndarray", constraints: "sample indices" },
          { name: "rate", type_desc: "np.ndarray", constraints: "events per minute" }
        ],
        depth: 1
      }
    ],
    edges: [
      {
        source_id: "condition",
        target_id: "detect",
        output_name: "filtered",
        input_name: "signal",
        source_type: "np.ndarray",
        target_type: "np.ndarray"
      },
      {
        source_id: "detect",
        target_id: "measure",
        output_name: "events",
        input_name: "rpeaks",
        source_type: "np.ndarray",
        target_type: "np.ndarray"
      }
    ],
    metadata: {
      goal: "Infer recurring event cadence from a sampled waveform",
      paradigm: "signal_detect_measure",
      repo: "showcase/public-waveform-rate",
      dataset_id: "physionet-mitdb-100",
      catalog_artifact: "cdg.sciona_atoms_signal.ecg_heart_rate_biosppy"
    }
  };

  function buildTutorialAEvolution() {
    var initial = JSON.parse(JSON.stringify(TUTORIAL_A_CDG));
    initial.nodes = initial.nodes.filter(function (node) { return node.node_id !== "condition"; });
    initial.nodes.forEach(function (node) { node.matched_primitive = ""; });
    initial.nodes.find(function (node) { return node.node_id === "detect"; }).inputs[0].name = "signal";
    initial.nodes.find(function (node) { return node.node_id === "measure"; }).inputs[0].name = "events";
    initial.edges = [{
      source_id: "detect", target_id: "measure", output_name: "events", input_name: "events",
      source_type: "np.ndarray", target_type: "np.ndarray"
    }];
    initial.metadata.catalog_artifact = "cdg.skeleton.signal_detect_measure";

    var refined = JSON.parse(JSON.stringify(TUTORIAL_A_CDG));
    refined.nodes.find(function (node) { return node.node_id === "measure"; }).matched_primitive =
      "sciona.atoms.signal_processing.biosppy.ecg.heart_rate_computation_median_smoothed";
    refined.nodes.find(function (node) { return node.node_id === "measure"; }).name =
      "Robust Event Rate";

    return {
      schema_version: "1.0",
      goal: TUTORIAL_A_CDG.metadata.goal,
      objective: "reference accuracy",
      versions: [
        { version_id: "ecg-initial", label: "Initial match", phase: "initial_match", loss: null, graph: initial },
        { version_id: "ecg-expanded", label: "Principal expansion", phase: "expansion", loss: null, graph: TUTORIAL_A_CDG },
        { version_id: "ecg-refined", label: "Robust refinement", phase: "refinement", loss: null, graph: refined }
      ],
      transitions: [
        {
          transition_id: "ecg-initial--ecg-expanded", source_version_id: "ecg-initial", target_version_id: "ecg-expanded",
          operation: "catalog_solution_expansion", status: "accepted", baseline_loss: null, candidate_loss: null,
          loss_delta: null, rules_applied: ["insert_conditioning_before_detection"],
          graph_diff: { added_nodes: [TUTORIAL_A_CDG.nodes[0]], removed_nodes: [], changed_nodes: TUTORIAL_A_CDG.nodes.slice(1), added_edges: [], removed_edges: [] }
        },
        {
          transition_id: "ecg-expanded--ecg-refined", source_version_id: "ecg-expanded", target_version_id: "ecg-refined",
          operation: "robust_rate_refinement", status: "accepted", baseline_loss: null, candidate_loss: null,
          loss_delta: null, rules_applied: ["replace_rate_with_robust_variant"],
          graph_diff: { added_nodes: [], removed_nodes: [], changed_nodes: [{ node_id: "measure" }], added_edges: [], removed_edges: [] }
        }
      ]
    };
  }

  var TUTORIAL_B_CDG = {
    nodes: [
      {
        node_id: "data_prep",
        name: "data_assembly",
        description: "Prepares features and labels.",
        concept_type: "data_assembly",
        status: "atomic",
        matched_primitive: "pandas.read_csv",
        inputs: [],
        outputs: [{ name: "X", type_desc: "ndarray" }],
        depth: 1
      },
      {
        node_id: "fit_est",
        name: "fit estimator",
        description: "model_training",
        concept_type: "ml_model_selection",
        status: "atomic",
        matched_primitive: "sklearn.linear_model.LogisticRegression.fit",
        inputs: [{ name: "X", type_desc: "ndarray" }],
        outputs: [{ name: "model", type_desc: "estimator" }],
        depth: 1
      },
      {
        node_id: "score_val",
        name: "score validation split",
        description: "prediction_ensemble",
        concept_type: "ml_model_selection",
        status: "atomic",
        matched_primitive: "sklearn.metrics.accuracy_score",
        inputs: [{ name: "model", type_desc: "estimator" }],
        outputs: [{ name: "score", type_desc: "float" }],
        depth: 1
      },
      {
        node_id: "kfold_ensemble",
        name: "k-fold cross validated ensemble",
        description: "Perform ensembling using K-fold CV.",
        concept_type: "ml_model_selection",
        status: "atomic",
        matched_primitive: null,
        inputs: [],
        outputs: [],
        depth: 1
      },
      {
        node_id: "stacking_meta",
        name: "stacking meta learner",
        description: "Use stacking ensemble classifier.",
        concept_type: "ml_model_selection",
        status: "atomic",
        matched_primitive: null,
        inputs: [],
        outputs: [],
        depth: 1
      }
    ],
    edges: [
      {
        source_id: "data_prep",
        target_id: "fit_est",
        output_name: "X",
        input_name: "X",
        source_type: "ndarray",
        target_type: "ndarray"
      },
      {
        source_id: "fit_est",
        target_id: "score_val",
        output_name: "model",
        input_name: "model",
        source_type: "estimator",
        target_type: "estimator"
      }
    ],
    metadata: {
      goal: "Demonstrate Delta Planner ensembling quick-fixes on Tabular ML CDGs",
      paradigm: "ml_model_selection",
      repo: "sklearn/tabular_ml"
    }
  };

  var TUTORIAL_C_CDG = {
    nodes: [
      {
        node_id: "data_load",
        name: "High-Dimensional Input",
        description: "Loads the raw high-dimensional dataset.",
        concept_type: "data_assembly",
        status: "atomic",
        matched_primitive: "pandas.read_csv",
        inputs: [],
        outputs: [{ name: "data", type_desc: "np.ndarray" }],
        depth: 1
      },
      {
        node_id: "pca_projection",
        name: "PCA Pre-reduction",
        description: "Reduces dimension to 50 using PCA to speed up downstream projection.",
        concept_type: "signal_transform",
        status: "atomic",
        matched_primitive: "sklearn.decomposition.PCA",
        inputs: [{ name: "data", type_desc: "np.ndarray" }],
        outputs: [{ name: "reduced", type_desc: "np.ndarray" }],
        depth: 1
      },
      {
        node_id: "umap_layout",
        name: "UMAP Projection",
        description: "Computes UMAP 2D coordinates.",
        concept_type: "signal_transform",
        status: "atomic",
        matched_primitive: "umap.UMAP",
        inputs: [{ name: "reduced", type_desc: "np.ndarray" }],
        outputs: [{ name: "projection", type_desc: "np.ndarray" }],
        depth: 1
      }
    ],
    edges: [
      {
        source_id: "data_load",
        target_id: "pca_projection",
        output_name: "data",
        input_name: "data",
        source_type: "np.ndarray",
        target_type: "np.ndarray"
      },
      {
        source_id: "pca_projection",
        target_id: "umap_layout",
        output_name: "reduced",
        input_name: "reduced",
        source_type: "np.ndarray",
        target_type: "np.ndarray"
      }
    ],
    metadata: {
      goal: "Structured composition and layout of UMAP scientific computing pipeline",
      paradigm: "dimensionality_reduction",
      repo: "umap/scientific_computing"
    }
  };

  var detailControls = null;
  var isoControls = null;
  var runnerControls = null;
  var evolutionControls = null;
  var evolutionDAGControls = null;
  var guidedTourControls = null;
  var activeEvolutionRunId = "";

  detailControls = window.initVisualizerDetailPanel({
    conceptFamily: CONCEPT_FAMILY,
    familyColors: FAMILY_COLORS,
    getCy: function () { return graphControls.getCy(); },
    getNodeById: function (nodeId) { return graphControls.getNodeById(nodeId); },
    getNodeColors: getNodeColors,
    focusNode: function (nodeId) { graphControls.focusNode(nodeId); },
    getRunId: function () { return runnerControls ? runnerControls.getActiveRunId() : null; },
    isApiAvailable: localApiAvailable,
    getCurrentData: function () { return graphControls ? graphControls.getCurrentData() : null; },
    validateAndLoad: function (data) { if (graphControls) graphControls.validateAndLoad(data); },
    onGraphRewritten: function (data, transition) {
      if (evolutionControls) {
        evolutionControls.recordTransition(data, transition);
      } else if (graphControls) {
        graphControls.validateAndLoad(data);
      }
    }
  });

  var graphControls = window.initVisualizerGraph({
    familyColors: FAMILY_COLORS,
    familyLabels: FAMILY_LABELS,
    getNodeColors: getNodeColors,
    statusShapes: STATUS_SHAPES,
    onNodeSelected: function (nodeData) {
      detailControls.handleNodeSelected(nodeData);
      if (isoControls) isoControls.updateButtonVisibility(nodeData);
    },
    onCanvasTapped: function () {
      detailControls.hide();
    },
    onCDGLoaded: function () {
      if (detailControls && detailControls.fetchQuickFixes) {
        detailControls.fetchQuickFixes(null);
      }
    },
    isApiAvailable: localApiAvailable
  });

  runnerControls = window.initVisualizerRunner({
    getCy: function () { return graphControls.getCy(); },
    getCurrentData: function () { return graphControls.getCurrentData(); },
    isApiAvailable: localApiAvailable,
    detailControls: detailControls,
    getEvolutionTrace: function () {
      return evolutionControls ? evolutionControls.getTrace() : null;
    },
    getActiveEvolutionVersion: function () {
      return evolutionControls ? evolutionControls.getActiveVersion() : null;
    },
    onVersionEvaluated: function (versionId, evaluation, runId) {
      if (evolutionControls) {
        evolutionControls.setVersionEvaluation(versionId, evaluation, runId);
      }
    },
    onExecutionComplete: function () {
      if (guidedTourControls && guidedTourControls.getActiveIndex() === 1) {
        guidedTourControls.next();
      }
    }
  });

  // Intercept validateAndLoad to trigger runner panel repo session sync
  var originalValidateAndLoad = graphControls.validateAndLoad;
  graphControls.validateAndLoad = function (data) {
    originalValidateAndLoad(data);
    if (data && data.metadata && data.metadata.repo && runnerControls) {
      runnerControls.setRepo(data.metadata.repo);
    }
  };

  evolutionControls = window.initEvolutionWorkspace({
    loadGraph: function (data) {
      originalValidateAndLoad(data);
      if (data && data.metadata && data.metadata.repo && runnerControls) {
        runnerControls.setRepo(data.metadata.repo);
      }
    },
    onVersionSelected: function (version) {
      if (runnerControls && version.run_id) runnerControls.setActiveRunId(version.run_id);
    },
    onRefineRequest: function (version, note) {
      return fetch("/api/cdg/refine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cdg: version.graph,
          source_version_id: version.version_id,
          guidance: note || "",
          selected_node_id: detailControls.getSelectedNodeId() || ""
        })
      }).then(function (response) {
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            throw new Error(payload.detail || "Refinement request failed.");
          });
        }
        return response.json();
      });
    },
    onVersionCreated: function (version) {
      if (!runnerControls) return;
      runnerControls.evaluateVersion(version).catch(function (error) {
        evolutionControls.setGuidanceStatus(
          error.message || "Candidate evaluation failed."
        );
      });
    },
    onGuidance: function (version, guidance) {
      var statusText = document.getElementById("status-text");
      if (statusText) {
        statusText.textContent = guidance.action === "reject"
          ? "Branch reset to " + version.label
          : "Guidance recorded on " + version.label;
      }
      if (activeEvolutionRunId) {
        fetch("/api/dashboard/runs/" + encodeURIComponent(activeEvolutionRunId) + "/guidance", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            version_id: version.version_id,
            action: guidance.action,
            note: guidance.note || ""
          })
        }).catch(function () {});
      }
    }
  });

  evolutionDAGControls = window.initEvolutionDAG({
    getTrace: function () { return evolutionControls.getTrace(); },
    selectVersion: function (versionId) {
      evolutionControls.selectVersionById(versionId);
      var statusText = document.getElementById("status-text");
      if (statusText) statusText.textContent = "Branch point selected: " + versionId;
    }
  });

  guidedTourControls = window.initGuidedTour({
    steps: [
      {
        id: "objective",
        target: "#metadata-bar",
        title: "Start from the objective",
        description: "Confirm the goal, paradigm, and active run. Every graph revision should be judged against the same intended behavior."
      },
      {
        id: "inputs",
        target: "#run-modal .iso-modal-content",
        title: "Provide evaluation data",
        description: "Root inputs make the graph executable. Use representative data and references so the objective can produce comparable loss for each revision."
      },
      {
        id: "graph",
        target: "#cy-container",
        title: "Read the computation graph",
        description: "Follow data flow from inputs through atomic operations. Select any node to inspect its contract, implementation, and place in the chain."
      },
      {
        id: "node-evidence",
        target: "#detail-panel",
        title: "Inspect intermediate evidence",
        description: "The Execution tab exposes the selected node's concrete inputs and outputs. This is where domain intuition can identify a failing stage before final loss does."
      },
      {
        id: "compare",
        target: "#evolution-workspace",
        title: "Compare graph revisions",
        description: "Move between version tabs and review the operation, loss delta, and structural diff. A lower loss is evidence, not permission to ignore graph intent."
      },
      {
        id: "refine",
        target: ".evolution-guidance-controls",
        title: "Direct the next refinement",
        description: "Describe the next direction from the selected version, or reject a dead end and branch from its parent. Guidance remains attached to the evolution trace."
      }
    ],
    prepareStep: function (step) {
      if (step.id === "inputs") {
        runnerControls.openInputDialog();
        return;
      }
      runnerControls.closeInputDialog();
      if (step.id === "node-evidence") {
        var cy = graphControls.getCy();
        if (cy && cy.nodes().length) {
          var selected = cy.nodes()[0];
          detailControls.handleNodeSelected(selected.data("_nodeData"));
          detailControls.activateTab("execution");
        }
      }
    },
    onFinish: function () {
      runnerControls.closeInputDialog();
    }
  });

  var browserControls = window.initVisualizerBrowser({
    conceptFamily: CONCEPT_FAMILY,
    familyColors: FAMILY_COLORS,
    familyLabels: FAMILY_LABELS,
    setStatus: function (text) {
      var statusText = document.getElementById("status-text");
      if (statusText) statusText.textContent = text;
    },
    validateAndLoad: graphControls.validateAndLoad
  });

  var compareControls = window.initVisualizerCompare({
    cyContainer: document.getElementById("cy-container"),
    detailPanel: detailControls.getPanel(),
    getNodeColors: getNodeColors,
    getCytoscapeStyle: graphControls.getCytoscapeStyle,
    statusShapes: STATUS_SHAPES
  });

  isoControls = window.initVisualizerIsomorphism({
    getSelectedNodeId: function () {
      return detailControls.getSelectedNodeId();
    },
    getCurrentData: function () {
      return graphControls.getCurrentData();
    },
    getNodeById: function (nodeId) {
      return graphControls.getNodeById(nodeId);
    },
    isApiAvailable: function () {
      return browserControls && browserControls.isApiAvailable();
    },
    activateTab: detailControls.activateTab,
    openInCompare: function (currentRepo, matchRepo) {
      compareControls.openInCompare(currentRepo, matchRepo);
    },
    conceptFamily: CONCEPT_FAMILY,
    familyColors: FAMILY_COLORS,
    familyLabels: FAMILY_LABELS
  });

  document.body.addEventListener("drop", function (e) {
    e.preventDefault();
    e.stopPropagation();
    var dropZone = document.getElementById("drop-zone");
    if (dropZone) dropZone.classList.remove("drag-active");
    if (e.dataTransfer.files.length > 0) {
      handleFile(e.dataTransfer.files[0], graphControls);
    }
  });

  if (btnOpen) {
    btnOpen.addEventListener("click", function () {
      fileInput.click();
    });
  }

  if (btnDashboard) {
    btnDashboard.addEventListener("click", function () {
      window.open("/dashboard.html", "_blank");
    });
  }

  var btnTutorials = document.getElementById("btn-tutorials");
  var btnGuidedTour = document.getElementById("btn-guided-tour");
  var btnTutorialsClose = document.getElementById("btn-tutorials-close");
  var tutorialsModal = document.getElementById("tutorials-modal");

  if (btnTutorials && tutorialsModal) {
    btnTutorials.addEventListener("click", function () {
      tutorialsModal.classList.remove("hidden");
    });
  }

  if (btnGuidedTour) {
    btnGuidedTour.addEventListener("click", function () {
      var currentGraph = graphControls.getCurrentData();
      if (!currentGraph) {
        activeEvolutionRunId = "";
        evolutionControls.loadTrace(buildTutorialAEvolution());
      } else if (!evolutionControls.getTrace().versions.length) {
        evolutionControls.start(currentGraph, { label: "Current graph" });
      }
      guidedTourControls.start();
    });
  }

  if (btnTutorialsClose && tutorialsModal) {
    btnTutorialsClose.addEventListener("click", function () {
      tutorialsModal.classList.add("hidden");
    });
  }

  // Tutorial tab switching
  var tutorialTabButtons = document.querySelectorAll("#tutorials-tabs button");
  tutorialTabButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      tutorialTabButtons.forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      
      var selectedTut = btn.getAttribute("data-tutorial");
      var panes = document.querySelectorAll(".tutorial-pane");
      panes.forEach(function (pane) {
        if (pane.id === "tut-" + selectedTut) {
          pane.classList.remove("hidden");
        } else {
          pane.classList.add("hidden");
        }
      });
    });
  });

  // Load tutorial buttons
  var btnLoadTutA = document.getElementById("btn-load-tutorial-a");
  var btnLoadTutB = document.getElementById("btn-load-tutorial-b");
  var btnLoadTutC = document.getElementById("btn-load-tutorial-c");

  if (btnLoadTutA) {
    btnLoadTutA.addEventListener("click", function () {
      activeEvolutionRunId = "";
      evolutionControls.loadTrace(buildTutorialAEvolution());
      if (tutorialsModal) tutorialsModal.classList.add("hidden");
      guidedTourControls.start();
    });
  }
  if (btnLoadTutB) {
    btnLoadTutB.addEventListener("click", function () {
      graphControls.validateAndLoad(TUTORIAL_B_CDG);
      if (tutorialsModal) tutorialsModal.classList.add("hidden");
    });
  }
  if (btnLoadTutC) {
    btnLoadTutC.addEventListener("click", function () {
      graphControls.validateAndLoad(TUTORIAL_C_CDG);
      if (tutorialsModal) tutorialsModal.classList.add("hidden");
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      if (fileInput.files.length > 0) {
        handleFile(fileInput.files[0], graphControls);
        fileInput.value = "";
      }
    });
  }

  var flowOffset = 0;
  function animateFlow() {
    flowOffset = (flowOffset + 0.3) % 18;
    var cy = graphControls.getCy();
    if (cy) {
      cy.edges("[edgeType='dataflow']").not(".collapsed-hidden").style("line-dash-offset", -flowOffset);
    }
    requestAnimationFrame(animateFlow);
  }

  requestAnimationFrame(animateFlow);
  // Startup URL-based loader
  var queryRepo = null;
  var search = window.location ? window.location.search : "";
  var match = RegExp("[?&]repo=([^&]*)").exec(search);
  if (match) {
    queryRepo = decodeURIComponent(match[1].replace(/\+/g, " "));
  }

  if (queryRepo) {
    var statusText = document.getElementById("status-text");
    if (statusText) statusText.textContent = "Loading " + queryRepo + "...";
    if (queryRepo === "showcase/public-waveform-rate" || queryRepo === "biosppy/ecg_mismatch") {
      evolutionControls.loadTrace(buildTutorialAEvolution());
    } else if (queryRepo === "sklearn/tabular_ml") {
      graphControls.validateAndLoad(TUTORIAL_B_CDG);
    } else if (queryRepo === "umap/scientific_computing") {
      graphControls.validateAndLoad(TUTORIAL_C_CDG);
    } else {
      fetch("/api/cdg?repo=" + encodeURIComponent(queryRepo))
        .then(function (res) {
          if (!res.ok) throw new Error("CDG not found");
          return res.json();
        })
        .then(function (data) {
          graphControls.validateAndLoad(data);
        })
        .catch(function (err) {
          var statusText = document.getElementById("status-text");
          if (statusText) statusText.textContent = "Error: " + err.message;
          graphControls.tryLoadDefault();
        });
    }
  } else {
    graphControls.tryLoadDefault();
  }

  var evolutionRunMatch = RegExp("[?&]evolution_run=([^&]*)").exec(search);
  if (evolutionRunMatch && evolutionControls) {
    var evolutionRunId = decodeURIComponent(evolutionRunMatch[1].replace(/\+/g, " "));
    activeEvolutionRunId = evolutionRunId;
    fetch("/api/dashboard/runs/" + encodeURIComponent(evolutionRunId) + "/evolution")
      .then(function (res) {
        if (!res.ok) throw new Error("Evolution trace not found");
        return res.json();
      })
      .then(function (trace) { evolutionControls.loadTrace(trace); })
      .catch(function (err) {
        var statusText = document.getElementById("status-text");
        if (statusText) statusText.textContent = "Error: " + err.message;
      });
  }
})();
