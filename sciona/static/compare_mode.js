(function (global) {
  "use strict";

  var COMPARISON_FIELDS = [
    "name", "concept_type", "status", "matched_primitive",
    "type_signature", "inputs", "outputs"
  ];

  function comparableValue(node, field) {
    if (!node) return undefined;
    var value = node[field];
    if (value == null) return field === "inputs" || field === "outputs" ? [] : "";
    return value;
  }

  function changedNodeFields(leftNode, rightNode) {
    if (!leftNode || !rightNode) return COMPARISON_FIELDS.slice();
    return COMPARISON_FIELDS.filter(function (field) {
      return JSON.stringify(comparableValue(leftNode, field)) !== JSON.stringify(comparableValue(rightNode, field));
    });
  }

  function comparisonSignature(node) {
    var values = {};
    COMPARISON_FIELDS.forEach(function (field) { values[field] = comparableValue(node, field); });
    return JSON.stringify(values);
  }

  function classifyGraphDiff(leftGraph, rightGraph) {
    var left = {};
    var right = {};
    (leftGraph && leftGraph.nodes || []).forEach(function (node) { left[node.node_id] = node; });
    (rightGraph && rightGraph.nodes || []).forEach(function (node) { right[node.node_id] = node; });
    var result = { common: [], added: [], removed: [], changed: [] };
    Object.keys(left).forEach(function (nodeId) {
      if (!right[nodeId]) {
        result.removed.push(nodeId);
      } else if (comparisonSignature(left[nodeId]) === comparisonSignature(right[nodeId])) {
        result.common.push(nodeId);
      } else {
        result.changed.push(nodeId);
      }
    });
    Object.keys(right).forEach(function (nodeId) {
      if (!left[nodeId]) result.added.push(nodeId);
    });
    return result;
  }

  function describeNodeDiff(leftGraph, rightGraph, nodeId) {
    var leftNode = (leftGraph && leftGraph.nodes || []).find(function (node) { return node.node_id === nodeId; }) || null;
    var rightNode = (rightGraph && rightGraph.nodes || []).find(function (node) { return node.node_id === nodeId; }) || null;
    var state = !leftNode ? "added" : !rightNode ? "removed" :
      comparisonSignature(leftNode) === comparisonSignature(rightNode) ? "common" : "changed";
    return {
      nodeId: nodeId,
      state: state,
      leftNode: leftNode,
      rightNode: rightNode,
      changedFields: state === "changed" ? changedNodeFields(leftNode, rightNode) :
        state === "common" ? [] : COMPARISON_FIELDS.slice()
    };
  }

  function conciseNodeLabel(value) {
    var label = String(value || "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
    if (label.length <= 28) return label;
    var words = label.split(" ");
    if (words.length === 1) return label.slice(0, 25) + "...";
    var lastWord = words[words.length - 1];
    var prefix = "";
    var prefixLimit = Math.max(8, 23 - lastWord.length);
    for (var i = 0; i < words.length - 1; i += 1) {
      var candidate = prefix ? prefix + " " + words[i] : words[i];
      if (candidate.length > prefixLimit) break;
      prefix = candidate;
    }
    if (!prefix) prefix = words[0].slice(0, prefixLimit);
    return prefix + " ... " + lastWord;
  }

  global.classifyVisualizerGraphDiff = classifyGraphDiff;
  global.describeVisualizerNodeDiff = describeNodeDiff;
  global.conciseVisualizerNodeLabel = conciseNodeLabel;

  global.initVisualizerCompare = function initVisualizerCompare(options) {
    var btnCompare = document.getElementById("btn-compare");
    var compareBar = document.getElementById("compare-bar");
    var compareContainer = document.getElementById("compare-container");
    var compareLeftSelect = document.getElementById("compare-left-select");
    var compareRightSelect = document.getElementById("compare-right-select");
    var compareScore = document.getElementById("compare-score");
    var compareDiffSummary = document.getElementById("compare-diff-summary");
    var compareHoverRow = document.getElementById("compare-hover-row");
    var compareNodeName = document.getElementById("compare-node-name");
    var compareDiffModal = document.getElementById("compare-diff-modal");
    var compareDiffState = document.getElementById("compare-diff-state");
    var compareDiffTitle = document.getElementById("compare-diff-title");
    var compareDiffSubtitle = document.getElementById("compare-diff-subtitle");
    var compareDiffFields = document.getElementById("compare-diff-fields");
    var compareDiffClose = document.getElementById("compare-diff-close");
    var compareDiffTabs = document.getElementById("compare-diff-tabs");
    var compareInputVisuals = document.getElementById("compare-input-visuals");
    var compareOutputVisuals = document.getElementById("compare-output-visuals");
    var btnCompareClose = document.getElementById("btn-compare-close");
    var cyLeft = null;
    var cyRight = null;
    var graphLeft = null;
    var graphRight = null;
    var itemLeft = null;
    var itemRight = null;
    var selectedDiffNodeId = "";
    var valueRequestSerial = 0;
    var compareCharts = [];
    var comparands = {};

    function nodeCount(graph) {
      return graph && Array.isArray(graph.nodes) ? graph.nodes.length : 0;
    }

    function fieldLabel(field) {
      return field.replace(/_/g, " ").replace(/\b\w/g, function (letter) { return letter.toUpperCase(); });
    }

    function formatFieldValue(node, field) {
      if (!node) return "Not present";
      var value = comparableValue(node, field);
      if (field === "inputs" || field === "outputs") {
        if (!value.length) return "None";
        return value.map(function (port) {
          var detail = port.type_desc || port.type || "unspecified type";
          if (port.constraints) detail += " [" + port.constraints + "]";
          return (port.name || "unnamed") + ": " + detail;
        }).join("\n");
      }
      if (typeof value === "object") return JSON.stringify(value, null, 2);
      return String(value || "None");
    }

    function connectionsFor(graph, nodeId, direction) {
      var rows = (graph && graph.edges || []).filter(function (edge) {
        return direction === "incoming" ? edge.target_id === nodeId : edge.source_id === nodeId;
      }).map(function (edge) {
        var sourcePort = edge.output_name ? "." + edge.output_name : "";
        var targetPort = edge.input_name ? "." + edge.input_name : "";
        return edge.source_id + sourcePort + " -> " + edge.target_id + targetPort;
      });
      return rows.length ? rows.sort().join("\n") : "None";
    }

    function appendDiffRow(label, leftValue, rightValue, changed, topology) {
      if (!compareDiffFields) return;
      var row = document.createElement("tr");
      if (changed) row.classList.add("is-changed");
      if (topology) row.classList.add("is-topology");
      [label, leftValue, rightValue].forEach(function (value) {
        var cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      });
      compareDiffFields.appendChild(row);
    }

    function closeNodeDiff() {
      valueRequestSerial += 1;
      destroyCompareCharts();
      if (compareDiffModal) compareDiffModal.classList.add("hidden");
    }

    function openNodeDiff(nodeId) {
      if (!compareDiffModal || !graphLeft || !graphRight) return;
      var detail = describeNodeDiff(graphLeft, graphRight, nodeId);
      selectedDiffNodeId = nodeId;
      var displayNode = detail.rightNode || detail.leftNode || {};
      compareDiffState.textContent = fieldLabel(detail.state);
      compareDiffState.className = "compare-diff-state is-" + detail.state;
      compareDiffTitle.textContent = displayNode.name || nodeId;
      compareDiffSubtitle.textContent = detail.state === "changed"
        ? "Changed fields: " + detail.changedFields.map(fieldLabel).join(", ")
        : detail.state === "common" ? "No node field changes" :
          detail.state === "added" ? "Present only in the right graph" : "Present only in the left graph";
      compareDiffFields.innerHTML = "";
      appendDiffRow("Node ID", nodeId, nodeId, false, false);
      COMPARISON_FIELDS.forEach(function (field) {
        appendDiffRow(
          fieldLabel(field),
          formatFieldValue(detail.leftNode, field),
          formatFieldValue(detail.rightNode, field),
          detail.changedFields.indexOf(field) >= 0,
          false
        );
      });
      ["incoming", "outgoing"].forEach(function (direction) {
        var leftConnections = connectionsFor(graphLeft, nodeId, direction);
        var rightConnections = connectionsFor(graphRight, nodeId, direction);
        appendDiffRow(
          fieldLabel(direction + " connections"),
          leftConnections,
          rightConnections,
          leftConnections !== rightConnections,
          true
        );
      });
      activateCompareTab("details");
      compareDiffModal.classList.remove("hidden");
    }

    function hoverSummary(nodeId) {
      var detail = describeNodeDiff(graphLeft, graphRight, nodeId);
      var node = detail.rightNode || detail.leftNode || {};
      var prefix = fieldLabel(detail.state);
      if (detail.state === "changed") {
        return prefix + ": " + (node.name || nodeId) + " - " + detail.changedFields.map(fieldLabel).join(", ");
      }
      return prefix + ": " + (node.name || nodeId);
    }

    function destroyCompareCharts() {
      compareCharts.forEach(function (chart) {
        if (chart && typeof chart.destroy === "function") chart.destroy();
      });
      compareCharts = [];
    }

    function runLabel(item, fallback) {
      return item && item.label ? item.label.replace(/\s+\(\d+ nodes\).*$/, "") : fallback;
    }

    function fetchNodeValues(item, nodeId) {
      if (!item || !item.runId) return Promise.resolve({ available: false, inputs: {}, outputs: {} });
      var url = "/api/cdg/runs/" + encodeURIComponent(item.runId) +
        "/nodes/" + encodeURIComponent(nodeId) + "/values";
      return fetch(url).then(function (response) {
        if (!response.ok) throw new Error("Execution values unavailable");
        return response.json();
      }).then(function (data) {
        return { available: true, inputs: data.inputs || {}, outputs: data.outputs || {} };
      }).catch(function (error) {
        return { available: false, inputs: {}, outputs: {}, error: error.message };
      });
    }

    function fetchValueSlice(item, nodeId, name, isInput, meta) {
      if (!item || !item.runId || !meta) return Promise.resolve(null);
      var valueName = (isInput ? "in_" : "out_") + name;
      var url = "/api/cdg/runs/" + encodeURIComponent(item.runId) +
        "/nodes/" + encodeURIComponent(nodeId) + "/values/" +
        encodeURIComponent(valueName) + "/slice";
      return fetch(url).then(function (response) {
        if (!response.ok) throw new Error("Value preview unavailable");
        return response.json();
      }).catch(function (error) {
        return { type: "error", message: error.message };
      });
    }

    function valueMeta(meta) {
      if (!meta) return "not recorded";
      var type = meta.dtype || meta.type || "value";
      return meta.shape ? type + " " + JSON.stringify(meta.shape) : type;
    }

    function renderHeatmap(canvas, result) {
      var grid = result && result.data || [];
      var rows = grid.length;
      var cols = rows && Array.isArray(grid[0]) ? grid[0].length : 0;
      if (!rows || !cols) return;
      canvas.width = cols;
      canvas.height = rows;
      var context = canvas.getContext("2d");
      var image = context.createImageData(cols, rows);
      var min = Infinity;
      var max = -Infinity;
      grid.forEach(function (row) {
        row.forEach(function (value) {
          if (value < min) min = value;
          if (value > max) max = value;
        });
      });
      var range = max - min || 1;
      for (var r = 0; r < rows; r += 1) {
        for (var c = 0; c < cols; c += 1) {
          var normalized = (grid[r][c] - min) / range;
          var index = (r * cols + c) * 4;
          image.data[index] = Math.round(235 - normalized * 190);
          image.data[index + 1] = Math.round(245 - normalized * 85);
          image.data[index + 2] = Math.round(245 - normalized * 25);
          image.data[index + 3] = 255;
        }
      }
      context.putImageData(image, 0, 0);
    }

    function appendValueSide(container, label, result) {
      var side = document.createElement("div");
      side.className = "compare-value-side";
      var heading = document.createElement("strong");
      heading.textContent = label;
      side.appendChild(heading);
      if (!result) {
        side.appendChild(document.createTextNode("Not recorded"));
      } else if (result.type === "2d") {
        var canvas = document.createElement("canvas");
        canvas.className = "compare-heatmap";
        side.appendChild(canvas);
        renderHeatmap(canvas, result);
      } else {
        var pre = document.createElement("pre");
        if (result.type === "scalar") pre.textContent = String(result.data);
        else if (result.type === "json") pre.textContent = JSON.stringify(result.data, null, 2);
        else if (result.type === "error" || result.type === "nd") pre.textContent = result.message || "Preview unavailable";
        else pre.textContent = JSON.stringify(result.data, null, 2);
        side.appendChild(pre);
      }
      container.appendChild(side);
    }

    function renderValuePlot(container, name, leftResult, rightResult) {
      var oneDimensional = (leftResult && leftResult.type === "1d") || (rightResult && rightResult.type === "1d");
      if (oneDimensional && global.Chart) {
        var chartContainer = document.createElement("div");
        chartContainer.className = "compare-value-chart";
        var canvas = document.createElement("canvas");
        chartContainer.appendChild(canvas);
        container.appendChild(chartContainer);
        var datasets = [];
        var longest = 0;
        [[leftResult, runLabel(itemLeft, "Left graph"), "#00897b"],
          [rightResult, runLabel(itemRight, "Right graph"), "#ef6c00"]].forEach(function (entry) {
          if (!entry[0] || entry[0].type !== "1d") return;
          longest = Math.max(longest, entry[0].data.length);
          datasets.push({
            label: entry[1],
            data: entry[0].data,
            borderColor: entry[2],
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: entry[0].data.length > 100 ? 0 : 1.5
          });
        });
        compareCharts.push(new global.Chart(canvas.getContext("2d"), {
          type: "line",
          data: {
            labels: Array.from({ length: longest }, function (_, index) { return index; }),
            datasets: datasets
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            scales: {
              x: { ticks: { maxTicksLimit: 10, font: { size: 9 } } },
              y: { ticks: { font: { size: 9 } } }
            },
            plugins: { legend: { display: true, labels: { boxWidth: 14, font: { size: 10 } } } }
          }
        }));
        return;
      }
      var sides = document.createElement("div");
      sides.className = "compare-value-sides";
      appendValueSide(sides, runLabel(itemLeft, "Left graph"), leftResult);
      appendValueSide(sides, runLabel(itemRight, "Right graph"), rightResult);
      container.appendChild(sides);
    }

    function loadValueComparison(kind) {
      var container = kind === "inputs" ? compareInputVisuals : compareOutputVisuals;
      if (!container || !selectedDiffNodeId) return;
      valueRequestSerial += 1;
      var requestSerial = valueRequestSerial;
      destroyCompareCharts();
      container.innerHTML = '<div class="compare-value-empty">Loading persisted execution values...</div>';
      Promise.all([
        fetchNodeValues(itemLeft, selectedDiffNodeId),
        fetchNodeValues(itemRight, selectedDiffNodeId)
      ]).then(function (sides) {
        if (requestSerial !== valueRequestSerial) return;
        var leftValues = sides[0][kind] || {};
        var rightValues = sides[1][kind] || {};
        var names = {};
        Object.keys(leftValues).forEach(function (name) { names[name] = true; });
        Object.keys(rightValues).forEach(function (name) { names[name] = true; });
        var orderedNames = Object.keys(names).sort();
        container.innerHTML = "";
        if (!orderedNames.length) {
          var empty = document.createElement("div");
          empty.className = "compare-value-empty";
          empty.textContent = !itemLeft || !itemLeft.runId || !itemRight || !itemRight.runId
            ? "Execute both graph versions to compare persisted " + kind + "."
            : "No persisted " + kind + " were recorded for this node.";
          container.appendChild(empty);
          return;
        }
        orderedNames.forEach(function (name) {
          var section = document.createElement("section");
          section.className = "compare-value-section";
          var heading = document.createElement("h5");
          heading.textContent = name;
          var meta = document.createElement("div");
          meta.className = "compare-value-meta";
          meta.textContent = "Left: " + valueMeta(leftValues[name]) + " | Right: " + valueMeta(rightValues[name]);
          var plot = document.createElement("div");
          plot.className = "compare-value-plot";
          plot.innerHTML = '<div class="compare-value-empty">Loading preview...</div>';
          section.appendChild(heading);
          section.appendChild(meta);
          section.appendChild(plot);
          container.appendChild(section);
          Promise.all([
            fetchValueSlice(itemLeft, selectedDiffNodeId, name, kind === "inputs", leftValues[name]),
            fetchValueSlice(itemRight, selectedDiffNodeId, name, kind === "inputs", rightValues[name])
          ]).then(function (results) {
            if (requestSerial !== valueRequestSerial) return;
            plot.innerHTML = "";
            renderValuePlot(plot, name, results[0], results[1]);
          });
        });
      });
    }

    function activateCompareTab(name) {
      if (!compareDiffTabs) return;
      compareDiffTabs.querySelectorAll("button").forEach(function (button) {
        var active = button.getAttribute("data-compare-tab") === name;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", active ? "true" : "false");
      });
      ["details", "inputs", "outputs"].forEach(function (tabName) {
        var panel = document.getElementById("compare-tab-" + tabName);
        if (panel) panel.classList.toggle("active", tabName === name);
      });
      if (name === "inputs" || name === "outputs") loadValueComparison(name);
      else {
        valueRequestSerial += 1;
        destroyCompareCharts();
      }
    }

    function localComparands() {
      var items = options.getLocalComparands ? options.getLocalComparands() : [];
      return Array.isArray(items) ? items.filter(function (item) {
        return item && item.key && item.graph;
      }).map(function (item) {
        if (!item.label) item.label = item.key + " (" + nodeCount(item.graph) + " nodes)";
        return item;
      }) : [];
    }

    function remoteComparands(cdgs) {
      return cdgs.map(function (cdg) {
        return {
          key: cdg.repo,
          label: cdg.repo + " (" + cdg.node_count + " nodes)",
          repo: cdg.repo,
          source: "catalog"
        };
      });
    }

    function setComparands(items) {
      comparands = {};
      items.forEach(function (item) {
        if (!comparands[item.key]) comparands[item.key] = item;
      });
      populateCompareSelects(Object.keys(comparands).map(function (key) {
        return comparands[key];
      }));
    }

    function appendRemoteComparands(cdgs) {
      remoteComparands(cdgs).forEach(function (item) {
        if (!comparands[item.key]) comparands[item.key] = item;
      });
      populateCompareSelects(Object.keys(comparands).map(function (key) {
        return comparands[key];
      }));
    }

    function selectLatestPair(items) {
      if (!items.length) return;
      var right = items[items.length - 1];
      var left = items.length > 1 ? items[items.length - 2] : null;
      if (compareRightSelect) compareRightSelect.value = right.key;
      loadComparePane("right", right.key);
      if (left && compareLeftSelect) {
        compareLeftSelect.value = left.key;
        loadComparePane("left", left.key);
      }
    }

    function enterCompareMode(skipDefaultSelection) {
      if (!compareBar || !compareContainer) return;
      compareBar.classList.remove("hidden");
      if (compareHoverRow) compareHoverRow.classList.remove("hidden");
      if (compareNodeName) compareNodeName.textContent = "Hover a node to see its comparison summary.";
      compareContainer.classList.remove("hidden");
      options.cyContainer.style.display = "none";
      options.detailPanel.classList.remove("visible");

      var local = localComparands();
      setComparands(local);
      if (!skipDefaultSelection) selectLatestPair(local);
      if (compareScore) {
        compareScore.textContent = local.length
          ? "Loading catalog CDGs..."
          : "Loading CDGs...";
      }

      fetch("/api/cdgs")
        .then(function (res) {
          if (!res.ok) throw new Error("Catalog browser unavailable");
          return res.json();
        })
        .then(function (cdgs) {
          if (!Array.isArray(cdgs)) throw new Error("Invalid CDG list response");
          appendRemoteComparands(cdgs);
          if (cyLeft && cyRight) updateComparison();
          else if (compareScore) compareScore.textContent = "";
        })
        .catch(function () {
          if (!compareScore) return;
          if (cyLeft && cyRight) {
            updateComparison();
            return;
          }
          compareScore.textContent = local.length
            ? "Catalog browser unavailable; open versions remain available"
            : "No open versions; catalog browser unavailable";
        });
    }

    function exitCompareMode() {
      if (!compareBar || !compareContainer) return;
      compareBar.classList.add("hidden");
      if (compareHoverRow) compareHoverRow.classList.add("hidden");
      compareContainer.classList.add("hidden");
      options.cyContainer.style.display = "";
      if (cyLeft) { cyLeft.destroy(); cyLeft = null; }
      if (cyRight) { cyRight.destroy(); cyRight = null; }
      graphLeft = null;
      graphRight = null;
      itemLeft = null;
      itemRight = null;
      selectedDiffNodeId = "";
      valueRequestSerial += 1;
      if (compareDiffSummary) compareDiffSummary.classList.add("hidden");
      if (compareNodeName) compareNodeName.textContent = "Hover a node to see its comparison summary.";
      closeNodeDiff();
      if (compareScore) compareScore.textContent = "";
    }

    function populateCompareSelects(items) {
      [compareLeftSelect, compareRightSelect].forEach(function (sel) {
        if (!sel) return;
        var selectedValue = sel.value;
        sel.innerHTML = "";
        var placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Select graph version...";
        sel.appendChild(placeholder);
        items.forEach(function (item) {
          var opt = document.createElement("option");
          opt.value = item.key;
          opt.textContent = item.label;
          sel.appendChild(opt);
        });
        if (selectedValue && comparands[selectedValue]) sel.value = selectedValue;
      });
    }

    function loadComparePane(side, key) {
      if (!key) return;
      var container = document.getElementById("compare-" + side);
      var item = comparands[key];
      if (!item) return;
      var graphPromise = item.graph
        ? Promise.resolve(item.graph)
        : fetch("/api/cdg?repo=" + encodeURIComponent(item.repo)).then(function (res) {
          if (!res.ok) throw new Error("CDG not found");
          return res.json();
        });
      graphPromise
        .then(function (data) {
          var cyInstance = buildCompareGraph(container, data);
          if (side === "left") {
            if (cyLeft) cyLeft.destroy();
            cyLeft = cyInstance;
            graphLeft = data;
            itemLeft = item;
          } else {
            if (cyRight) cyRight.destroy();
            cyRight = cyInstance;
            graphRight = data;
            itemRight = item;
          }
          updateComparison();
        })
        .catch(function (error) {
          if (compareScore) compareScore.textContent = error.message || "Unable to load graph";
        });
    }

    function buildCompareGraph(container, data) {
      var elements = [];
      (data.nodes || []).forEach(function (node) {
        var conceptType = node.concept_type || "custom";
        var colors = options.getNodeColors(conceptType);
        var status = node.status || "pending";
        var shape = options.statusShapes[status] || "ellipse";
        elements.push({
          group: "nodes",
          data: {
            id: node.node_id,
            label: conciseNodeLabel(node.name),
            fullLabel: node.name || node.node_id,
            bgColor: colors.bg,
            borderColor: colors.border,
            textColor: colors.text,
            shape: shape,
            size: 34,
            labelWidth: "54px",
            conceptType: conceptType
          }
        });
      });
      (data.edges || []).forEach(function (edge, i) {
        elements.push({
          group: "edges",
          data: {
            id: "df_" + i + "_" + edge.source_id + "_" + edge.target_id,
            source: edge.source_id,
            target: edge.target_id,
            edgeType: "dataflow"
          }
        });
      });

      var compareStyles = options.getCytoscapeStyle().concat([
        {
          selector: "node",
          style: {
            "font-size": "8px",
            "font-weight": 500,
            "text-max-width": "data(labelWidth)"
          }
        },
        {
          selector: "node.compare-common",
          style: {
            "opacity": 0.28,
            "background-color": "#eceff1",
            "border-color": "#90a4ae",
            "color": "#607d8b"
          }
        },
        {
          selector: "node.compare-added",
          style: {
            "background-color": "#c8e6c9",
            "border-color": "#2e7d32",
            "color": "#1b5e20",
            "border-width": 4,
            "opacity": 1
          }
        },
        {
          selector: "node.compare-removed",
          style: {
            "background-color": "#ffcdd2",
            "border-color": "#c62828",
            "color": "#7f0000",
            "border-width": 4,
            "border-style": "dashed",
            "opacity": 1
          }
        },
        {
          selector: "node.compare-changed",
          style: {
            "background-color": "#fff9c4",
            "border-color": "#f9a825",
            "color": "#5d4037",
            "border-width": 4,
            "opacity": 1
          }
        }
      ]);
      var cy = cytoscape({
        container: container,
        elements: elements,
        style: compareStyles,
        layout: { name: "dagre", rankDir: "TB", nodeSep: 34, rankSep: 58, padding: 50 },
        minZoom: 0.2,
        maxZoom: 1.4,
        wheelSensitivity: 0.3
      });
      cy.on("mouseover", "node", function (event) {
        if (compareNodeName) compareNodeName.textContent = hoverSummary(event.target.id());
      });
      cy.on("mouseout", "node", function () {
        if (compareNodeName) compareNodeName.textContent = "Hover a node to see its comparison summary.";
      });
      cy.on("tap", "node", function (event) {
        openNodeDiff(event.target.id());
      });
      return cy;
    }

    function applyDiffClass(cy, ids, className) {
      ids.forEach(function (nodeId) {
        var node = cy.getElementById(nodeId);
        if (node && node.length) node.addClass(className);
      });
    }

    function setDiffCount(id, value) {
      var element = document.getElementById(id);
      if (element) element.textContent = String(value);
    }

    function updateComparison() {
      if (!compareScore) return;
      if (!cyLeft || !cyRight || !graphLeft || !graphRight) {
        compareScore.textContent = "";
        if (compareDiffSummary) compareDiffSummary.classList.add("hidden");
        return;
      }

      var diff = classifyGraphDiff(graphLeft, graphRight);
      cyLeft.nodes().removeClass("compare-common compare-added compare-removed compare-changed");
      cyRight.nodes().removeClass("compare-common compare-added compare-removed compare-changed");
      applyDiffClass(cyLeft, diff.common, "compare-common");
      applyDiffClass(cyRight, diff.common, "compare-common");
      applyDiffClass(cyLeft, diff.removed, "compare-removed");
      applyDiffClass(cyRight, diff.added, "compare-added");
      applyDiffClass(cyLeft, diff.changed, "compare-changed");
      applyDiffClass(cyRight, diff.changed, "compare-changed");
      setDiffCount("compare-common-count", diff.common.length);
      setDiffCount("compare-added-count", diff.added.length);
      setDiffCount("compare-removed-count", diff.removed.length);
      setDiffCount("compare-changed-count", diff.changed.length);
      if (compareDiffSummary) compareDiffSummary.classList.remove("hidden");

      var leftTypes = {};
      var rightTypes = {};
      cyLeft.nodes().forEach(function (n) {
        var ct = n.data("conceptType") || "custom";
        leftTypes[ct] = (leftTypes[ct] || 0) + 1;
      });
      cyRight.nodes().forEach(function (n) {
        var ct = n.data("conceptType") || "custom";
        rightTypes[ct] = (rightTypes[ct] || 0) + 1;
      });

      var allTypes = {};
      Object.keys(leftTypes).forEach(function (k) { allTypes[k] = true; });
      Object.keys(rightTypes).forEach(function (k) { allTypes[k] = true; });

      var intersection = 0;
      var union = 0;
      Object.keys(allTypes).forEach(function (k) {
        var l = leftTypes[k] || 0;
        var r = rightTypes[k] || 0;
        intersection += Math.min(l, r);
        union += Math.max(l, r);
      });

      var jaccard = union > 0 ? (intersection / union) : 0;
      compareScore.textContent = "Type similarity " + jaccard.toFixed(3);
    }

    function openInCompare(currentRepo, matchRepo) {
      if (!currentRepo || !matchRepo) return;

      enterCompareMode(true);

      var waitForSelects = setInterval(function () {
        if (compareLeftSelect && comparands[currentRepo] && comparands[matchRepo]) {
          clearInterval(waitForSelects);
          compareLeftSelect.value = currentRepo;
          compareRightSelect.value = matchRepo;
          loadComparePane("left", currentRepo);
          loadComparePane("right", matchRepo);
        }
      }, 100);

      setTimeout(function () { clearInterval(waitForSelects); }, 5000);
    }

    if (btnCompare) {
      btnCompare.addEventListener("click", function () {
        enterCompareMode();
      });
    }

    if (btnCompareClose) {
      btnCompareClose.addEventListener("click", function () {
        exitCompareMode();
      });
    }

    if (compareLeftSelect) {
      compareLeftSelect.addEventListener("change", function () {
        loadComparePane("left", compareLeftSelect.value);
      });
    }

    if (compareRightSelect) {
      compareRightSelect.addEventListener("change", function () {
        loadComparePane("right", compareRightSelect.value);
      });
    }

    if (compareDiffClose) compareDiffClose.addEventListener("click", closeNodeDiff);
    if (compareDiffTabs) {
      compareDiffTabs.querySelectorAll("button").forEach(function (button) {
        button.addEventListener("click", function () {
          activateCompareTab(button.getAttribute("data-compare-tab"));
        });
      });
    }
    if (compareDiffModal) {
      var compareBackdrop = compareDiffModal.querySelector(".iso-modal-backdrop");
      if (compareBackdrop) compareBackdrop.addEventListener("click", closeNodeDiff);
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && compareDiffModal && !compareDiffModal.classList.contains("hidden")) {
        closeNodeDiff();
      }
    });

    global.addEventListener("resize", function () {
      [cyLeft, cyRight].forEach(function (cy) {
        if (!cy) return;
        cy.resize();
        cy.fit(undefined, 40);
      });
    });

    return {
      openInCompare: openInCompare,
      openNodeDiff: openNodeDiff,
      closeNodeDiff: closeNodeDiff
    };
  };
})(window);
