(function (global) {
  "use strict";

  global.initVisualizerCompare = function initVisualizerCompare(options) {
    var btnCompare = document.getElementById("btn-compare");
    var compareBar = document.getElementById("compare-bar");
    var compareContainer = document.getElementById("compare-container");
    var compareLeftSelect = document.getElementById("compare-left-select");
    var compareRightSelect = document.getElementById("compare-right-select");
    var compareScore = document.getElementById("compare-score");
    var btnCompareClose = document.getElementById("btn-compare-close");
    var cyLeft = null;
    var cyRight = null;

    function enterCompareMode() {
      if (!compareBar || !compareContainer) return;
      compareBar.classList.remove("hidden");
      compareContainer.classList.remove("hidden");
      options.cyContainer.style.display = "none";
      options.detailPanel.classList.remove("visible");

      fetch("/api/cdgs")
        .then(function (res) { return res.json(); })
        .then(function (cdgs) {
          populateCompareSelects(cdgs);
        })
        .catch(function () {
          if (compareScore) compareScore.textContent = "API not available";
        });
    }

    function exitCompareMode() {
      if (!compareBar || !compareContainer) return;
      compareBar.classList.add("hidden");
      compareContainer.classList.add("hidden");
      options.cyContainer.style.display = "";
      if (cyLeft) { cyLeft.destroy(); cyLeft = null; }
      if (cyRight) { cyRight.destroy(); cyRight = null; }
      if (compareScore) compareScore.textContent = "";
    }

    function populateCompareSelects(cdgs) {
      [compareLeftSelect, compareRightSelect].forEach(function (sel) {
        if (!sel) return;
        sel.innerHTML = '<option value="">Select CDG...</option>';
        cdgs.forEach(function (cdg) {
          var opt = document.createElement("option");
          opt.value = cdg.repo;
          opt.textContent = cdg.repo + " (" + cdg.node_count + " nodes)";
          sel.appendChild(opt);
        });
      });
    }

    function loadComparePane(side, repo) {
      if (!repo) return;
      var container = document.getElementById("compare-" + side);
      fetch("/api/cdg?repo=" + encodeURIComponent(repo))
        .then(function (res) { return res.json(); })
        .then(function (data) {
          var cyInstance = buildCompareGraph(container, data);
          if (side === "left") {
            if (cyLeft) cyLeft.destroy();
            cyLeft = cyInstance;
          } else {
            if (cyRight) cyRight.destroy();
            cyRight = cyInstance;
          }
          updateJaccardScore();
        });
    }

    function buildCompareGraph(container, data) {
      var elements = [];
      data.nodes.forEach(function (node) {
        var conceptType = node.concept_type || "custom";
        var colors = options.getNodeColors(conceptType);
        var status = node.status || "pending";
        var shape = options.statusShapes[status] || "ellipse";
        elements.push({
          group: "nodes",
          data: {
            id: node.node_id,
            label: node.name,
            bgColor: colors.bg,
            borderColor: colors.border,
            textColor: colors.text,
            shape: shape,
            size: 40,
            labelWidth: "70px",
            conceptType: conceptType
          }
        });
      });
      data.edges.forEach(function (edge, i) {
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

      return cytoscape({
        container: container,
        elements: elements,
        style: options.getCytoscapeStyle(),
        layout: { name: "dagre", rankDir: "TB", nodeSep: 30, rankSep: 50 },
        wheelSensitivity: 0.3
      });
    }

    function updateJaccardScore() {
      if (!compareScore) return;
      if (!cyLeft || !cyRight) {
        compareScore.textContent = "";
        return;
      }

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
      compareScore.textContent = "Jaccard similarity: " + jaccard.toFixed(3);
    }

    function openInCompare(currentRepo, matchRepo) {
      if (!currentRepo || !matchRepo) return;

      enterCompareMode();

      var waitForSelects = setInterval(function () {
        if (compareLeftSelect && compareLeftSelect.options.length > 1) {
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

    return {
      openInCompare: openInCompare
    };
  };
})(window);
