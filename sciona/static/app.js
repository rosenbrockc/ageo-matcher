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

  if (window.lucide && typeof window.lucide.createIcons === "function") {
    window.lucide.createIcons({ attrs: { "aria-hidden": "true" } });
  }

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

  var TABULAR_ATOMS = "sciona.atoms.ml.tabular.supervised_classification.";

  function buildTabularTutorialGraph(fitPrimitive, baseline) {
    var fitNode = baseline ? {
      node_id: "fit",
      name: "Fit class-prior baseline",
      description: "Establish a deterministic empirical-prior baseline before adding model complexity.",
      concept_type: "ml_model_selection",
      status: "atomic",
      matched_primitive: TABULAR_ATOMS + "fit_prior_probability",
      inputs: [{ name: "y_train", type_desc: "NDArray[int64]" }],
      outputs: [{ name: "class_probability", type_desc: "float" }],
      depth: 1
    } : {
      node_id: "fit",
      name: fitPrimitive.indexOf("cross_validated") !== -1 ? "Select regularization by cross-validation" : "Fit mixed-type logistic model",
      description: fitPrimitive.indexOf("cross_validated") !== -1
        ? "Choose regularization using stratified held-in log loss."
        : "Impute numeric and categorical features, one-hot encode categories, and fit logistic regression.",
      concept_type: "ml_model_selection",
      status: "atomic",
      matched_primitive: TABULAR_ATOMS + fitPrimitive,
      inputs: [
        { name: "X_train", type_desc: "DataFrame" },
        { name: "y_train", type_desc: "NDArray[int64]" }
      ],
      outputs: [{ name: "model", type_desc: "estimator" }],
      depth: 1
    };
    var predictNode = baseline ? {
      node_id: "predict",
      name: "Predict prior probabilities",
      description: "Emit the class-prior probability for every held-out row.",
      concept_type: "ml_model_selection",
      status: "atomic",
      matched_primitive: TABULAR_ATOMS + "predict_prior_probabilities",
      inputs: [
        { name: "class_probability", type_desc: "float" },
        { name: "X_test", type_desc: "DataFrame" },
        { name: "y_test", type_desc: "NDArray[int64]" }
      ],
      outputs: [
        { name: "probabilities", type_desc: "NDArray[float64]" },
        { name: "targets", type_desc: "NDArray[int64]" }
      ],
      depth: 1
    } : {
      node_id: "predict",
      name: "Predict held-out probabilities",
      description: "Produce positive-class probabilities and aligned held-out labels for evaluation.",
      concept_type: "ml_model_selection",
      status: "atomic",
      matched_primitive: TABULAR_ATOMS + "predict_binary_probabilities",
      inputs: [
        { name: "model", type_desc: "estimator" },
        { name: "X_test", type_desc: "DataFrame" },
        { name: "y_test", type_desc: "NDArray[int64]" }
      ],
      outputs: [
        { name: "probabilities", type_desc: "NDArray[float64]" },
        { name: "targets", type_desc: "NDArray[int64]" }
      ],
      depth: 1
    };
    var fitInputEdges = baseline
      ? [{ source_id: "split", target_id: "fit", output_name: "y_train", input_name: "y_train", source_type: "NDArray[int64]", target_type: "NDArray[int64]" }]
      : [
          { source_id: "split", target_id: "fit", output_name: "X_train", input_name: "X_train", source_type: "DataFrame", target_type: "DataFrame" },
          { source_id: "split", target_id: "fit", output_name: "y_train", input_name: "y_train", source_type: "NDArray[int64]", target_type: "NDArray[int64]" }
        ];
    return {
      nodes: [
        {
          node_id: "split",
          name: "Stratified train/test split",
          description: "Treat the final column as a binary target and create a reproducible held-out partition.",
          concept_type: "data_assembly",
          status: "atomic",
          matched_primitive: TABULAR_ATOMS + "stratified_tabular_split",
          inputs: [{ name: "dataset", type_desc: "DataFrame" }],
          outputs: [
            { name: "X_train", type_desc: "DataFrame" },
            { name: "X_test", type_desc: "DataFrame" },
            { name: "y_train", type_desc: "NDArray[int64]" },
            { name: "y_test", type_desc: "NDArray[int64]" }
          ],
          depth: 1
        },
        fitNode,
        predictNode
      ],
      edges: fitInputEdges.concat([
        { source_id: "fit", target_id: "predict", output_name: baseline ? "class_probability" : "model", input_name: baseline ? "class_probability" : "model", source_type: baseline ? "float" : "estimator", target_type: baseline ? "float" : "estimator" },
        { source_id: "split", target_id: "predict", output_name: "X_test", input_name: "X_test", source_type: "DataFrame", target_type: "DataFrame" },
        { source_id: "split", target_id: "predict", output_name: "y_test", input_name: "y_test", source_type: "NDArray[int64]", target_type: "NDArray[int64]" }
      ]),
      metadata: {
        goal: "Build and refine a deterministic mixed-type binary classifier",
        paradigm: "ml_model_selection",
        repo: "showcase/public-tabular-classification"
      }
    };
  }

  function buildTutorialBEvolution() {
    var initial = buildTabularTutorialGraph("", true);
    var expanded = buildTabularTutorialGraph("fit_one_hot_logistic", false);
    var refined = buildTabularTutorialGraph("fit_cross_validated_logistic", false);
    return {
      schema_version: "1.0",
      goal: "Build and refine a deterministic mixed-type binary classifier",
      objective: "log_loss",
      versions: [
        { version_id: "tabular-initial", label: "Prior baseline", phase: "initial_match", loss: null, graph: initial },
        { version_id: "tabular-expanded", label: "Model expansion", phase: "expansion", loss: null, graph: expanded },
        { version_id: "tabular-refined", label: "CV refinement", phase: "refinement", loss: null, graph: refined }
      ],
      transitions: [
        {
          transition_id: "tabular-initial--tabular-expanded", source_version_id: "tabular-initial", target_version_id: "tabular-expanded",
          operation: "mixed_type_model_expansion", status: "accepted", baseline_loss: null, candidate_loss: null,
          loss_delta: null, rules_applied: ["add_imputation", "add_one_hot_encoding", "fit_logistic_classifier"],
          graph_diff: { added_nodes: [], removed_nodes: [], changed_nodes: [{ node_id: "fit" }, { node_id: "predict" }], added_edges: [], removed_edges: [] }
        },
        {
          transition_id: "tabular-expanded--tabular-refined", source_version_id: "tabular-expanded", target_version_id: "tabular-refined",
          operation: "cross_validated_regularization", status: "accepted", baseline_loss: null, candidate_loss: null,
          loss_delta: null, rules_applied: ["select_regularization_by_held_in_log_loss"],
          graph_diff: { added_nodes: [], removed_nodes: [], changed_nodes: [{ node_id: "fit" }], added_edges: [], removed_edges: [] }
        }
      ]
    };
  }

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
  var compositionControls = null;
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
    },
    onDevelopComponent: function (operation, node) {
      if (compositionControls) compositionControls.open(operation, node);
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
    onHistoricalRunLoaded: function (snapshot) {
      var metadata = snapshot.metadata || {};
      var evaluation = snapshot.evaluation || null;
      var versionId = (evaluation && evaluation.version_id) || metadata.version_id || "historical-run";
      var statusText = document.getElementById("status-text");

      if (snapshot.cdg && evolutionControls) {
        var currentTrace = evolutionControls.getTrace();
        var existingVersion = currentTrace && currentTrace.versions
          ? currentTrace.versions.find(function (version) { return version.version_id === versionId; })
          : null;
        if (existingVersion) {
          existingVersion.graph = snapshot.cdg;
          existingVersion.run_id = snapshot.run_id;
          if (evaluation) evolutionControls.setVersionEvaluation(versionId, evaluation, snapshot.run_id);
          evolutionControls.selectVersionById(versionId);
        } else {
          evolutionControls.loadTrace({
            schema_version: "1.0",
            goal: (snapshot.cdg.metadata || {}).goal || "",
            objective: evaluation ? evaluation.objective : "",
            versions: [{
              version_id: versionId,
              label: "Historical run",
              phase: "execution_history",
              loss: evaluation ? evaluation.loss : null,
              evaluation: evaluation,
              graph: snapshot.cdg,
              run_id: snapshot.run_id,
              status: metadata.status || "completed"
            }],
            transitions: []
          });
        }
      }

      if (statusText) {
        statusText.textContent = snapshot.replayable
          ? "Loaded historical run " + snapshot.run_id
          : "Loaded legacy run artifacts; graph and loss were not persisted for this run.";
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
          run_id: runnerControls ? runnerControls.getActiveRunId() : "",
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
      var guidanceRunId = version.run_id || activeEvolutionRunId;
      if (guidanceRunId) {
        fetch("/api/dashboard/runs/" + encodeURIComponent(guidanceRunId) + "/guidance", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            version_id: version.version_id,
            action: guidance.action,
            note: guidance.note || "",
            node_id: detailControls.getSelectedNodeId() || ""
          })
        }).catch(function () {});
      }
    }
  });

  var loadEvolutionTrace = evolutionControls.loadTrace;
  compositionControls = window.initCompositionWorkspace({
    getTrace: function () { return evolutionControls.getTrace(); },
    getActiveVersion: function () { return evolutionControls.getActiveVersion(); },
    getRunId: function () { return runnerControls ? runnerControls.getActiveRunId() : ""; },
    setRunId: function (runId) { if (runnerControls && runId) runnerControls.setActiveRunId(runId); },
    getFamilyId: function () {
      var runId = runnerControls ? runnerControls.getActiveRunId() : "";
      return "run-" + String(runId || "local").split("--")[0].replace(/[^a-zA-Z0-9_-]/g, "-");
    },
    loadTrace: loadEvolutionTrace,
    selectVersion: function (versionId) { evolutionControls.selectVersionById(versionId); },
    loadFamily: function (familyId) {
      return fetch("/api/cdg/workspaces/" + encodeURIComponent(familyId)).then(function (response) {
        if (response.status === 404) return null;
        if (!response.ok) throw new Error("Workspace family could not be loaded.");
        return response.json();
      });
    },
    saveFamily: function (familyId, family) {
      return fetch("/api/cdg/workspaces/" + encodeURIComponent(familyId), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(family)
      }).then(function (response) {
        if (!response.ok) throw new Error("Workspace family could not be saved.");
        return response.json();
      });
    },
    compose: function (request) {
      return fetch("/api/cdg/compose", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request)
      }).then(function (response) {
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (payload) {
            throw new Error(payload.detail || "CDG composition failed.");
          });
        }
        return response.json();
      });
    },
    recordParentTransition: function (graph, metadata) {
      return evolutionControls.recordTransition(graph, metadata);
    },
    setStatus: function (message) {
      evolutionControls.setGuidanceStatus(message);
    }
  });

  evolutionControls.loadTrace = function (trace) {
    loadEvolutionTrace(trace);
    compositionControls.resetParent(trace, trace.goal || "Primary CDG");
  };

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
    statusShapes: STATUS_SHAPES,
    getLocalComparands: function () {
      var trace = evolutionControls ? evolutionControls.getTrace() : null;
      var items = [];
      if (trace && Array.isArray(trace.versions) && trace.versions.length) {
        items = trace.versions.map(function (version) {
          var count = version.graph && Array.isArray(version.graph.nodes)
            ? version.graph.nodes.length
            : 0;
          var loss = typeof version.loss === "number" && Number.isFinite(version.loss)
            ? " - loss " + version.loss.toFixed(6)
            : "";
          var status = version.status === "rejected" ? " - rejected" : "";
          return {
            key: "version:" + version.version_id,
            label: (version.label || version.version_id) + " (" + count + " nodes)" + loss + status,
            graph: version.graph,
            runId: version.run_id || "",
            source: "evolution"
          };
        });
      }
      var current = graphControls ? graphControls.getCurrentData() : null;
      var currentIsVersion = current && trace && trace.versions && trace.versions.some(function (version) {
        return version.graph === current;
      });
      if (current && !currentIsVersion) {
        var repo = current.metadata && current.metadata.repo ? current.metadata.repo : "open-graph";
        items.unshift({
          key: "open:" + repo,
          label: repo + " (" + (current.nodes || []).length + " nodes)",
          graph: current,
          runId: runnerControls ? runnerControls.getActiveRunId() : "",
          source: "open"
        });
      }
      return items;
    }
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
      activeEvolutionRunId = "";
      evolutionControls.loadTrace(buildTutorialBEvolution());
      if (tutorialsModal) tutorialsModal.classList.add("hidden");
      guidedTourControls.start();
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
      evolutionControls.loadTrace(buildTutorialBEvolution());
    } else if (queryRepo === "showcase/public-tabular-classification") {
      evolutionControls.loadTrace(buildTutorialBEvolution());
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
