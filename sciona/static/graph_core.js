(function (global) {
  "use strict";

  global.initVisualizerGraph = function initVisualizerGraph(options) {
    var metaGoal = document.getElementById("meta-goal");
    var metaParadigm = document.getElementById("meta-paradigm");
    var metaNodes = document.getElementById("meta-nodes");
    var metaEdges = document.getElementById("meta-edges");
    var metaThread = document.getElementById("meta-thread");
    var statusText = document.getElementById("status-text");
    var btnFit = document.getElementById("btn-fit");
    var btnReset = document.getElementById("btn-reset");
    var layoutInputs = Array.prototype.slice.call(
      document.querySelectorAll('input[name="graph-layout"]')
    );
    var graphViewControls = document.getElementById("graph-view-controls");
    var graphViewMenu = document.getElementById("graph-view-menu");
    var btnGraphMenu = document.getElementById("btn-graph-menu");
    var cyContainer = document.getElementById("cy-container");
    var dropZone = document.getElementById("drop-zone");
    var graphSearch = document.getElementById("graph-search");
    var legendPanel = document.getElementById("legend-panel");
    var btnLegend = document.getElementById("btn-legend");
    var breadcrumbBar = document.getElementById("breadcrumb-bar");
    var breadcrumbContent = document.getElementById("breadcrumb-content");

    var cy = null;
    var styles = window.createVisualizerGraphStyles({
      familyColors: options.familyColors,
      familyLabels: options.familyLabels
    });
    var state = window.createVisualizerGraphState({
      breadcrumbBar: breadcrumbBar,
      breadcrumbContent: breadcrumbContent
    });

    function selectedLayout() {
      var selected = layoutInputs.find(function (input) { return input.checked; });
      return selected ? selected.value : "dagre";
    }

    function setGraphControlsEnabled(enabled) {
      layoutInputs.forEach(function (input) { input.disabled = !enabled; });
      if (btnFit) btnFit.disabled = !enabled;
      if (btnReset) btnReset.disabled = !enabled;
    }

    function closeGraphViewMenu(returnFocus) {
      if (!graphViewMenu || !btnGraphMenu) return;
      graphViewMenu.classList.add("hidden");
      btnGraphMenu.setAttribute("aria-expanded", "false");
      if (returnFocus) btnGraphMenu.focus();
    }

    function openGraphViewMenu() {
      if (!graphViewMenu || !btnGraphMenu) return;
      graphViewMenu.classList.remove("hidden");
      btnGraphMenu.setAttribute("aria-expanded", "true");
      var selected = layoutInputs.find(function (input) { return input.checked && !input.disabled; });
      var focusTarget = selected || btnLegend;
      if (focusTarget) focusTarget.focus();
    }

    function isInsideGraphViewControls(target) {
      var current = target;
      while (current) {
        if (current === graphViewControls) return true;
        current = current.parentNode;
      }
      return false;
    }

    function setStatus(text) {
      if (statusText) statusText.textContent = text;
    }

    function rebuildVisibleGraph() {
      var currentData = state.getCurrentData();
      if (!cy || !currentData) return;
      var visibleNodeIds = state.computeVisibleNodeIds();

      cy.nodes().forEach(function (n) {
        if (visibleNodeIds[n.id()]) {
          n.removeClass("collapsed-hidden");
        } else {
          n.addClass("collapsed-hidden");
        }
        var nd = n.data("_nodeData");
        if (nd && state.isDecomposed(n.id())) {
          if (state.isExpanded(n.id())) {
            n.data("label", nd.name + " [-]");
          } else {
            n.data("label", nd.name + " [" + (nd.children ? nd.children.length : 0) + "]");
          }
        }
      });

      cy.edges().forEach(function (e) {
        var src = e.data("source");
        var tgt = e.data("target");
        if (visibleNodeIds[src] && visibleNodeIds[tgt]) {
          e.removeClass("collapsed-hidden");
        } else {
          e.addClass("collapsed-hidden");
        }
      });

      state.renderBreadcrumb(
        function () { state.collapseAll(rebuildVisibleGraph); },
        function (nodeId, depth) { state.collapseTo(nodeId, depth, rebuildVisibleGraph); }
      );

      var visible = cy.elements().not(".collapsed-hidden");
      if (visible.length > 0) {
        visible.layout(styles.getLayoutConfig(selectedLayout())).run();
      }

      setStatus(Object.keys(visibleNodeIds).length + " of " + currentData.nodes.length + " nodes visible");
    }

    function onNodeMouseOver(e) {
      var node = e.target;
      var upstream = node.predecessors();
      var downstream = node.successors();
      var connected = upstream.union(downstream).union(node);

      cy.elements().not(connected).addClass("dimmed");
      upstream.edges().addClass("upstream-highlight");
      downstream.edges().addClass("downstream-highlight");
      upstream.nodes().addClass("upstream-highlight");
      downstream.nodes().addClass("downstream-highlight");
      node.addClass("hover-focus");
    }

    function onNodeMouseOut() {
      cy.elements().removeClass("dimmed upstream-highlight downstream-highlight hover-focus");
    }

    function onEdgeMouseOver(e) {
      var edge = e.target;
      var d = edge.data();
      if (d.edgeType === "dataflow" && d.outputName) {
        var tip = d.source + "." + d.outputName +
          (d.sourceType ? " (" + d.sourceType + ")" : "") +
          " \u2192 " + d.target + "." + d.inputName +
          (d.targetType ? " (" + d.targetType + ")" : "");
        if (d.mismatchDetail) {
          tip = "⚠️ CONTRACT MISMATCH:\n" + d.mismatchDetail + "\n\n" + tip;
        }
        edge.data("_tipLabel", tip);
        edge.addClass("edge-tooltip");
      }
    }

    function onEdgeMouseOut(e) {
      e.target.removeClass("edge-tooltip");
      e.target.removeData("_tipLabel");
    }

    function panToSearchMatch() {
      var nodeId = state.getActiveSearchMatch();
      if (!cy || !nodeId) return;
      var node = cy.getElementById(nodeId);
      if (node.length) {
        cy.animate({
          center: { eles: node },
          zoom: Math.max(cy.zoom(), 1.2)
        }, { duration: 200 });
      }
    }

    function runSearch(raw) {
      var currentData = state.getCurrentData();
      if (!cy) return;

      state.resetSearch();

      if (!raw || !raw.trim()) {
        cy.elements().removeClass("search-match search-dimmed");
        setStatus(currentData ? currentData.nodes.length + " nodes, " + currentData.edges.length + " data-flow edges" : "No data loaded");
        return;
      }

      var query = state.parseSearchQuery(raw);
      var matchIds = [];
      cy.nodes().forEach(function (node) {
        var nd = node.data("_nodeData");
        if (nd && state.nodeMatchesQuery(nd, query)) {
          matchIds.push(node.id());
        }
      });

      if (matchIds.length === 0) {
        cy.elements().addClass("search-dimmed");
        setStatus("No matches");
        return;
      }

      var matchSelector = matchIds.map(function (id) { return 'node[id="' + id + '"]'; }).join(", ");
      var matchNodes = cy.nodes(matchSelector);

      cy.elements().addClass("search-dimmed");
      matchNodes.removeClass("search-dimmed").addClass("search-match");
      matchNodes.edgesWith(matchNodes).removeClass("search-dimmed");

      state.updateSearchMatches(matchIds);
      setStatus(matchIds.length + " match" + (matchIds.length === 1 ? "" : "es"));
      panToSearchMatch();
    }

    function cycleSearchMatch() {
      var active = state.advanceSearchMatch();
      if (!active) return;
      setStatus("Match " + (active.index + 1) + " of " + active.total);
      panToSearchMatch();
    }

    function buildGraph(data) {
      setGraphControlsEnabled(false);
      if (cy) {
        cy.destroy();
        cy = null;
      }

      state.setCurrentData(data);
      if (graphSearch) graphSearch.value = "";

      var meta = data.metadata || {};
      if (metaGoal) metaGoal.textContent = "Goal: " + (meta.goal || "—");
      if (metaParadigm) metaParadigm.textContent = "Paradigm: " + (meta.paradigm || "—");
      if (metaNodes) metaNodes.textContent = "Nodes: " + data.nodes.length;
      if (metaEdges) metaEdges.textContent = "Edges: " + data.edges.length;
      if (metaThread) metaThread.textContent = "Thread: " + (meta.thread_id ? meta.thread_id.substring(0, 12) : "—");

      cy = cytoscape({
        container: cyContainer,
        elements: state.buildElements({
          getNodeColors: options.getNodeColors,
          statusShapes: options.statusShapes
        }),
        style: styles.getCytoscapeStyle(),
        layout: { name: "preset" },
        wheelSensitivity: 0.3,
        maxZoom: 3
      });

      var visible = cy.elements().not(".collapsed-hidden");
      if (visible.length > 0) {
        visible.layout(styles.getLayoutConfig(selectedLayout())).run();
      }
      setGraphControlsEnabled(true);

      cy.on("tap", function (e) {
        if (e.target === cy) {
          if (options.onCanvasTapped) options.onCanvasTapped();
        } else if (e.target.isNode()) {
          if (options.onNodeSelected) options.onNodeSelected(e.target.data("_nodeData"));
        }
      });

      cy.on("dbltap", "node", function (e) {
        if (state.isDecomposed(e.target.id())) {
          state.toggleExpand(e.target.id(), rebuildVisibleGraph);
        }
      });

      cy.on("mouseover", "node", onNodeMouseOver);
      cy.on("mouseout", "node", onNodeMouseOut);
      cy.on("mouseover", "edge", onEdgeMouseOver);
      cy.on("mouseout", "edge", onEdgeMouseOut);

      setStatus(data.nodes.filter(function (n) { return state.computeVisibleNodeIds()[n.node_id]; }).length + " of " + data.nodes.length + " nodes visible (double-click to expand)");
      state.renderBreadcrumb(
        function () { state.collapseAll(rebuildVisibleGraph); },
        function (nodeId, depth) { state.collapseTo(nodeId, depth, rebuildVisibleGraph); }
      );
    }

    function runGhostSimulation(data) {
      if (!options.isApiAvailable || !options.isApiAvailable()) return;

      fetch("/api/cdg/ghost_sim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data)
      })
      .then(function (res) {
        if (!res.ok) throw new Error("Ghost simulation request failed");
        return res.json();
      })
      .then(function (report) {
        if (!cy) return;

        cy.edges().removeClass("mismatch-edge");
        cy.nodes().removeClass("exec-failed");

        if (report.ran && !report.passed) {
          if (report.mismatch_edges) {
            report.mismatch_edges.forEach(function (mEdge) {
              var selector = 'edge[source="' + mEdge.source_id + '"][target="' + mEdge.target_id + '"]';
              cy.edges(selector).forEach(function (edge) {
                edge.addClass("mismatch-edge");
                edge.data("mismatchDetail", report.error);
                var originalLabel = edge.data("original_label") || edge.data("label") || "";
                if (!edge.data("original_label")) {
                  edge.data("original_label", originalLabel);
                }
                edge.data("label", "⚠️ " + originalLabel);
              });
            });
          }

          if (report.error_node) {
            cy.nodes().forEach(function (node) {
              var nd = node.data("_nodeData");
              if (nd && (nd.name === report.error_node || nd.node_id === report.error_node)) {
                node.addClass("exec-failed");
                nd.mismatchDetail = report.error;
              }
            });
          }

          var logContent = document.getElementById("repair-log-content");
          if (logContent) {
            logContent.textContent = "[ERROR] Ghost Witness Simulation Failed!\n" +
              "Node: " + report.error_node + " (" + report.error_function + ")\n" +
              "Detail: " + report.error;
          }
        } else if (report.ran && report.passed) {
          cy.edges().forEach(function (edge) {
            if (edge.data("original_label")) {
              edge.data("label", edge.data("original_label"));
            }
          });
          
          var logContent = document.getElementById("repair-log-content");
          if (logContent) {
            logContent.textContent = "[INFO] Ghost Witness Simulation passed successfully.\n" +
              "Simulated " + report.node_count + " nodes.\n" +
              "Trace: " + report.trace.join(" -> ");
          }
        }
      })
      .catch(function (err) {
        console.error("Ghost simulation error:", err);
      });
    }

    function validateAndLoad(data) {
      if (!data.nodes || !Array.isArray(data.nodes)) {
        setStatus("Error: JSON must contain a 'nodes' array");
        return;
      }
      if (!data.edges || !Array.isArray(data.edges)) {
        data.edges = [];
      }
      if (dropZone) dropZone.classList.add("hidden");
      buildGraph(data);
      runGhostSimulation(data);
      if (options.onCDGLoaded) {
        options.onCDGLoaded();
      }
    }

    function tryLoadDefault() {
      fetch("default_cdg.json")
        .then(function (res) {
          if (!res.ok) throw new Error("not found");
          return res.json();
        })
        .then(function (data) {
          validateAndLoad(data);
        })
        .catch(function () {});
    }

    function focusNode(nodeId) {
      if (!cy) return;
      var cyNode = cy.getElementById(nodeId);
      if (cyNode.length) {
        cy.animate({ center: { eles: cyNode } }, { duration: 200 });
        if (options.onNodeSelected) options.onNodeSelected(cyNode.data("_nodeData"));
      }
    }

    document.body.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (dropZone) dropZone.classList.add("drag-active");
    });

    document.body.addEventListener("dragleave", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (dropZone) dropZone.classList.remove("drag-active");
    });

    styles.buildLegend();
    setGraphControlsEnabled(false);

    if (btnGraphMenu && graphViewMenu) {
      btnGraphMenu.addEventListener("click", function () {
        if (graphViewMenu.classList.contains("hidden")) {
          openGraphViewMenu();
        } else {
          closeGraphViewMenu(true);
        }
      });
      document.addEventListener("click", function (event) {
        if (
          !graphViewMenu.classList.contains("hidden") &&
          !isInsideGraphViewControls(event.target)
        ) {
          closeGraphViewMenu(false);
        }
      });
      document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !graphViewMenu.classList.contains("hidden")) {
          event.preventDefault();
          closeGraphViewMenu(true);
        }
      });
    }

    if (btnLegend && legendPanel) {
      btnLegend.addEventListener("click", function () {
        var visible = legendPanel.classList.toggle("visible");
        btnLegend.setAttribute("aria-checked", visible ? "true" : "false");
        closeGraphViewMenu(false);
      });
    }

    layoutInputs.forEach(function (input) {
      input.addEventListener("change", function () {
        if (input.checked && cy) cy.layout(styles.getLayoutConfig(input.value)).run();
      });
    });

    if (btnFit) {
      btnFit.addEventListener("click", function () {
        if (cy) cy.fit(undefined, 30);
        closeGraphViewMenu(false);
      });
    }

    if (btnReset) {
      btnReset.addEventListener("click", function () {
        if (cy) {
          cy.layout(styles.getLayoutConfig(selectedLayout())).run();
          cy.fit(undefined, 30);
        }
        closeGraphViewMenu(false);
      });
    }

    if (graphSearch) {
      var searchDebounce = null;
      graphSearch.addEventListener("input", function () {
        clearTimeout(searchDebounce);
        searchDebounce = setTimeout(function () {
          runSearch(graphSearch.value);
        }, 200);
      });

      graphSearch.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
          e.preventDefault();
          if (state.hasSearchMatches()) {
            cycleSearchMatch();
          } else {
            runSearch(graphSearch.value);
          }
        }
        if (e.key === "Escape") {
          graphSearch.value = "";
          runSearch("");
          graphSearch.blur();
        }
      });
    }

    if (window.ResizeObserver && cyContainer) {
      var resizeObserver = new ResizeObserver(function () {
        if (cy) {
          cy.resize();
        }
      });
      resizeObserver.observe(cyContainer);
    }

    return {
      validateAndLoad: validateAndLoad,
      tryLoadDefault: tryLoadDefault,
      getCurrentData: function () { return state.getCurrentData(); },
      getNodeById: function (nodeId) { return state.getNodeById(nodeId); },
      getCy: function () { return cy; },
      getCytoscapeStyle: styles.getCytoscapeStyle,
      focusNode: focusNode
    };
  };
})(window);
