(function (global) {
  "use strict";

  function lossLabel(value) {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toFixed(4)
      : "pending";
  }

  global.createEvolutionDAGElements = function createEvolutionDAGElements(trace) {
    var versions = trace && Array.isArray(trace.versions) ? trace.versions : [];
    var transitions = trace && Array.isArray(trace.transitions) ? trace.transitions : [];
    var nodes = versions.map(function (version) {
      return {
        data: {
          id: version.version_id,
          label: (version.label || version.version_id) + "\n" + lossLabel(version.loss),
          status: version.status || "accepted",
          loss: version.loss
        }
      };
    });
    var edges = transitions.map(function (transition) {
      return {
        data: {
          id: transition.transition_id || transition.source_version_id + "--" + transition.target_version_id,
          source: transition.source_version_id,
          target: transition.target_version_id,
          label: (transition.operation || "refinement").replace(/_/g, " "),
          status: transition.status || "accepted"
        }
      };
    });
    return nodes.concat(edges);
  };

  global.initEvolutionDAG = function initEvolutionDAG(options) {
    var modal = document.getElementById("evolution-dag-modal");
    var openButton = document.getElementById("btn-evolution-dag");
    var closeButton = document.getElementById("evolution-dag-close");
    var selectButton = document.getElementById("evolution-dag-select");
    var selection = document.getElementById("evolution-dag-selection");
    var container = document.getElementById("evolution-dag-canvas");
    var cy = null;
    var selectedVersionId = "";

    function close() {
      modal.classList.add("hidden");
      if (cy) {
        cy.destroy();
        cy = null;
      }
    }

    function open() {
      var trace = options.getTrace();
      selectedVersionId = "";
      selectButton.disabled = true;
      selection.textContent = "Select a version to inspect its branch.";
      modal.classList.remove("hidden");
      if (cy) cy.destroy();
      cy = global.cytoscape({
        container: container,
        elements: global.createEvolutionDAGElements(trace),
        layout: { name: "dagre", rankDir: "LR", padding: 24, nodeSep: 36, rankSep: 120 },
        style: [
          {
            selector: "node",
            style: {
              "shape": "round-rectangle",
              "width": 150,
              "height": 54,
              "background-color": "#e8f0f2",
              "border-color": "#607d8b",
              "border-width": 2,
              "label": "data(label)",
              "font-size": 11,
              "text-valign": "center",
              "text-halign": "center",
              "text-wrap": "wrap",
              "text-max-width": 135,
              "color": "#263238"
            }
          },
          { selector: "node[status = 'rejected']", style: { "background-color": "#ffebee", "border-color": "#c62828", "opacity": 0.7 } },
          { selector: "node:selected", style: { "background-color": "#e3f2fd", "border-color": "#1565c0", "border-width": 3 } },
          { selector: "edge", style: { "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "#90a4ae", "target-arrow-color": "#90a4ae", "width": 2, "label": "data(label)", "font-size": 9, "text-wrap": "wrap", "text-max-width": 110, "text-background-color": "#fff", "text-background-opacity": 0.9 } },
          { selector: "edge[status = 'rejected']", style: { "line-style": "dashed", "line-color": "#c62828", "target-arrow-color": "#c62828" } }
        ]
      });
      cy.on("tap", "node", function (event) {
        selectedVersionId = event.target.id();
        var version = (trace.versions || []).find(function (item) {
          return item.version_id === selectedVersionId;
        });
        selectButton.disabled = false;
        selection.textContent = (version.label || selectedVersionId) +
          " selected. Refinement will branch from this version.";
      });
    }

    openButton.addEventListener("click", open);
    closeButton.addEventListener("click", close);
    var backdrop = modal.querySelector(".iso-modal-backdrop");
    if (backdrop) backdrop.addEventListener("click", close);
    selectButton.addEventListener("click", function () {
      if (!selectedVersionId) return;
      options.selectVersion(selectedVersionId);
      close();
    });

    return { open: open, close: close };
  };
})(window);
