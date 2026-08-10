(function (global) {
  "use strict";

  global.initVisualizerDetailPanel = function initVisualizerDetailPanel(options) {
    var detailPanel = document.getElementById("detail-panel");
    var detailTabs = document.querySelectorAll(".detail-tab");
    var tabContents = document.querySelectorAll(".tab-content");
    var selectedNodeId = null;
    var executionRequestSerial = 0;
    var btnRunNode = document.getElementById("btn-run-node");

    function activateTab(target) {
      detailTabs.forEach(function (t) {
        t.classList.toggle("active", t.getAttribute("data-tab") === target);
      });
      tabContents.forEach(function (c) {
        c.classList.toggle("active", c.id === "tab-" + target);
      });
    }

    function populateIOTable(tableId, specs) {
      var table = document.getElementById(tableId);
      if (!table) return;
      var tbody = table.querySelector("tbody");
      tbody.innerHTML = "";
      if (specs.length === 0) {
        var row = document.createElement("tr");
        var td = document.createElement("td");
        td.setAttribute("colspan", "3");
        td.textContent = "(none)";
        td.style.color = "#9e9e9e";
        row.appendChild(td);
        tbody.appendChild(row);
        return;
      }
      specs.forEach(function (spec) {
        var row = document.createElement("tr");
        var tdName = document.createElement("td");
        var tdType = document.createElement("td");
        var tdConstraints = document.createElement("td");
        tdName.textContent = spec.name || "";
        tdType.textContent = spec.type_desc || "";
        tdConstraints.textContent = spec.constraints || "";
        row.appendChild(tdName);
        row.appendChild(tdType);
        row.appendChild(tdConstraints);
        tbody.appendChild(row);
      });
    }

    function populateDetailPanel(node) {
      document.getElementById("detail-name").textContent = node.name || "(unnamed)";

      var statusEl = document.getElementById("detail-status");
      var status = node.status || "pending";
      statusEl.textContent = status;
      statusEl.className = "status-badge status-" + status;

      var ct = node.concept_type || "custom";
      var ctEl = document.getElementById("detail-concept-type");
      var family = options.conceptFamily[ct] || "other";
      var colors = options.familyColors[family];
      ctEl.textContent = ct.replace(/_/g, " ");
      ctEl.style.background = colors.bg;
      ctEl.style.color = colors.text;
      ctEl.style.borderColor = colors.border;

      document.getElementById("detail-description").textContent = node.description || "(none)";
      document.getElementById("detail-type-sig").textContent = node.type_signature || "(none)";
      document.getElementById("detail-primitive").textContent = node.matched_primitive || "(none)";

      populateIOTable("detail-inputs", node.inputs || []);
      populateIOTable("detail-outputs", node.outputs || []);

      document.getElementById("detail-depth").textContent = node.depth != null ? String(node.depth) : "—";
      document.getElementById("detail-children").textContent = node.children && node.children.length > 0 ? node.children.join(", ") : "(leaf)";
      document.getElementById("detail-parent").textContent = node.parent_id || "(root)";
      document.getElementById("detail-rationale").textContent = node.decomposition_rationale || "(none)";
      document.getElementById("detail-critic").value = node.critic_notes || "(none)";

      var mismatchSec = document.getElementById("mismatch-diagnostic-section");
      var mismatchErr = document.getElementById("detail-mismatch-error");
      if (mismatchSec && mismatchErr) {
        if (node.mismatchDetail) {
          mismatchSec.style.display = "block";
          mismatchErr.textContent = node.mismatchDetail;
        } else {
          mismatchSec.style.display = "none";
        }
      }

      // Control "Run Node" button visibility
      if (btnRunNode) {
        if (node.status === "atomic") {
          btnRunNode.classList.remove("hidden");
        } else {
          btnRunNode.classList.add("hidden");
        }
      }
    }

    function fetchQuickFixes(nodeId) {
      var currentData = options.getCurrentData ? options.getCurrentData() : null;
      if (!currentData) return;

      var qfList = document.getElementById("quick-fixes-list");
      if (!qfList) return;

      qfList.innerHTML = '<span class="lineage-hint">Loading recommendations...</span>';

      fetch("/api/delta_planner/recommendations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cdg: currentData,
          selected_node_id: nodeId
        })
      })
      .then(function (res) {
        if (!res.ok) throw new Error("Failed to load quick-fixes");
        return res.json();
      })
      .then(function (data) {
        qfList.innerHTML = "";
        if (!data.candidates || data.candidates.length === 0) {
          qfList.innerHTML = '<span class="lineage-hint">No quick-fixes available</span>';
          return;
        }

        data.candidates.forEach(function (cand) {
          if (cand.adaptation_kind === "direct_use") return;

          var card = document.createElement("div");
          card.style.background = "#e3f2fd";
          card.style.border = "1px solid #90caf9";
          card.style.borderRadius = "4px";
          card.style.padding = "8px";
          card.style.marginBottom = "8px";
          card.style.fontSize = "12px";

          var title = document.createElement("div");
          title.style.fontWeight = "bold";
          title.style.marginBottom = "4px";
          title.textContent = cand.adaptation_kind.toUpperCase() + ": " + (cand.operation_rule_names.join(", ") || cand.rationale);
          card.appendChild(title);

          var rat = document.createElement("div");
          rat.style.color = "#546e7a";
          rat.style.marginBottom = "6px";
          rat.textContent = cand.rationale;
          card.appendChild(rat);

          if (cand.operation_rule_names && cand.operation_rule_names.length > 0) {
            cand.operation_rule_names.forEach(function (ruleName) {
              var btn = document.createElement("button");
              btn.textContent = "Apply " + ruleName.replace(/_/g, " ");
              btn.style.background = "#1976d2";
              btn.style.color = "#fff";
              btn.style.border = "none";
              btn.style.borderRadius = "3px";
              btn.style.padding = "4px 8px";
              btn.style.cursor = "pointer";
              btn.style.marginRight = "5px";
              btn.style.marginTop = "4px";

              btn.addEventListener("click", function () {
                applyQuickFix(ruleName);
              });
              card.appendChild(btn);
            });
          }

          qfList.appendChild(card);
        });

        if (qfList.children.length === 0) {
          qfList.innerHTML = '<span class="lineage-hint">No quick-fixes available</span>';
        }
      })
      .catch(function (err) {
        qfList.innerHTML = '<span class="lineage-hint" style="color: #c62828;">Error loading quick-fixes</span>';
      });
    }

    function applyQuickFix(ruleName) {
      var currentData = options.getCurrentData ? options.getCurrentData() : null;
      if (!currentData) return;

      var qfList = document.getElementById("quick-fixes-list");
      if (qfList) {
        qfList.innerHTML = '<span class="lineage-hint">Applying fix and compiling...</span>';
      }

      fetch("/api/delta_planner/apply_fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cdg: currentData,
          rule_name: ruleName
        })
      })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (data) {
            throw new Error(data.detail || "Failed to apply fix");
          });
        }
        return res.json();
      })
      .then(function (data) {
        if (options.onGraphRewritten) {
          options.onGraphRewritten(data.updated_cdg, {
            operation: ruleName,
            label: ruleName.replace(/_/g, " "),
            rules_applied: [ruleName]
          });
        } else if (options.validateAndLoad) {
          options.validateAndLoad(data.updated_cdg);
        }

        var logContent = document.getElementById("repair-log-content");
        var diffContent = document.getElementById("repair-diff-content");
        if (logContent) logContent.textContent = data.logs || "No logs available.";
        if (diffContent) diffContent.textContent = data.diff || "No diffs available.";

        activateTab("repair");
        fetchQuickFixes(null);
      })
      .catch(function (err) {
        if (qfList) {
          qfList.innerHTML = '<span class="lineage-hint" style="color: #c62828;">Error: ' + err.message + '</span>';
        }
      });
    }

    function renderVariableVisual(container, runId, nodeId, valName, isInput, meta) {
      container.innerHTML = "";
      
      var valKey = (isInput ? "in_" : "out_") + valName;
      
      var item = document.createElement("div");
      item.className = "exec-value-item";
      
      var header = document.createElement("div");
      header.className = "exec-value-header";
      
      var nameEl = document.createElement("span");
      nameEl.className = "exec-value-name";
      nameEl.textContent = valName;
      
      var typeDesc = meta.dtype ? meta.dtype + " " + JSON.stringify(meta.shape) : meta.type || "Any";
      var metaEl = document.createElement("span");
      metaEl.className = "exec-value-meta";
      metaEl.textContent = typeDesc;
      
      header.appendChild(nameEl);
      header.appendChild(metaEl);
      item.appendChild(header);

      // Slicing control container (only for arrays with ndim > 1)
      var sliceInput = null;
      var visualWrapper = document.createElement("div");

      function loadSlice(sliceStr) {
        visualWrapper.innerHTML = '<div class="lineage-hint">Loading slice...</div>';
        
        var url = "/api/cdg/runs/" + runId + "/nodes/" + nodeId + "/values/" + valKey + "/slice";
        if (sliceStr) {
          url += "?slice=" + encodeURIComponent(sliceStr);
        }
        
        fetch(url)
          .then(function (res) { return res.json(); })
          .then(function (res) {
            visualWrapper.innerHTML = "";
            
            if (res.type === "scalar") {
              var txt = document.createElement("pre");
              txt.style.margin = "0";
              txt.style.fontSize = "11px";
              txt.textContent = "Scalar Value: " + res.data;
              visualWrapper.appendChild(txt);
            } else if (res.type === "1d") {
              var container = document.createElement("div");
              container.className = "chart-container";
              
              var canvas = document.createElement("canvas");
              container.appendChild(canvas);
              visualWrapper.appendChild(container);
              
              var ctx = canvas.getContext("2d");
              new Chart(ctx, {
                type: "line",
                data: {
                  labels: res.data.map(function (_, i) { return i; }),
                  datasets: [{
                    label: valName + (sliceStr ? " (" + sliceStr + ")" : ""),
                    data: res.data,
                    borderColor: "#009688",
                    backgroundColor: "rgba(0, 150, 136, 0.05)",
                    borderWidth: 1.5,
                    pointRadius: res.data.length > 100 ? 0 : 2
                  }]
                },
                options: {
                  responsive: true,
                  maintainAspectRatio: false,
                  scales: {
                    y: { ticks: { font: { size: 8 } } },
                    x: { ticks: { font: { size: 8 }, maxTicksLimit: 8 } }
                  },
                  plugins: { legend: { display: false } }
                }
              });
            } else if (res.type === "2d") {
              // Provide Grid / Canvas Heatmap previews
              var bar = document.createElement("div");
              bar.style.display = "flex";
              bar.style.gap = "8px";
              bar.style.marginBottom = "5px";
              
              var btnTable = document.createElement("button");
              btnTable.className = "exec-slice-btn";
              btnTable.textContent = "Table";
              
              var btnImg = document.createElement("button");
              btnImg.className = "exec-slice-btn";
              btnImg.textContent = "Heatmap";
              
              bar.appendChild(btnTable);
              bar.appendChild(btnImg);
              visualWrapper.appendChild(bar);
              
              var pane = document.createElement("div");
              visualWrapper.appendChild(pane);

              function renderTable() {
                pane.innerHTML = "";
                var wrapper = document.createElement("div");
                wrapper.className = "matrix-table-wrapper";
                var table = document.createElement("table");
                table.className = "matrix-table";
                
                var grid = res.data;
                var rows = Math.min(grid.length, 50);
                var cols = rows > 0 ? Math.min(grid[0].length, 25) : 0;
                
                var thead = document.createElement("thead");
                var headerRow = document.createElement("tr");
                headerRow.appendChild(document.createElement("th")); // corner
                for (var c = 0; c < cols; c++) {
                  var th = document.createElement("th");
                  th.textContent = c;
                  headerRow.appendChild(th);
                }
                thead.appendChild(headerRow);
                table.appendChild(thead);
                
                var tbody = document.createElement("tbody");
                for (var r = 0; r < rows; r++) {
                  var row = document.createElement("tr");
                  var rth = document.createElement("th");
                  rth.textContent = r;
                  row.appendChild(rth);
                  for (var c = 0; c < cols; c++) {
                    var td = document.createElement("td");
                    var num = grid[r][c];
                    td.textContent = typeof num === "number" ? num.toFixed(4) : String(num);
                    row.appendChild(td);
                  }
                  tbody.appendChild(row);
                }
                table.appendChild(tbody);
                wrapper.appendChild(table);
                pane.appendChild(wrapper);
                
                if (grid.length > 50 || (grid[0] && grid[0].length > 25)) {
                  var cap = document.createElement("div");
                  cap.className = "exec-value-meta";
                  cap.textContent = "* Showing truncated 50x25 preview of " + grid.length + "x" + (grid[0] ? grid[0].length : 0) + " matrix.";
                  pane.appendChild(cap);
                }
              }

              function renderHeatmap() {
                pane.innerHTML = "";
                var grid = res.data;
                var rows = grid.length;
                var cols = rows > 0 ? grid[0].length : 0;
                if (rows === 0 || cols === 0) return;
                
                var wrapper = document.createElement("div");
                wrapper.className = "image-preview-container";
                var canvas = document.createElement("canvas");
                canvas.className = "image-preview-canvas";
                
                // Fine-tune canvas display sizes
                canvas.style.width = "100%";
                canvas.style.height = "auto";
                
                wrapper.appendChild(canvas);
                pane.appendChild(wrapper);
                
                var min = Infinity;
                var max = -Infinity;
                for (var r = 0; r < rows; r++) {
                  for (var c = 0; c < cols; c++) {
                    var v = grid[r][c];
                    if (v < min) min = v;
                    if (v > max) max = v;
                  }
                }
                var range = max - min || 1;
                
                canvas.width = cols;
                canvas.height = rows;
                var ctx = canvas.getContext("2d");
                var imgData = ctx.createImageData(cols, rows);
                
                for (var r = 0; r < rows; r++) {
                  for (var c = 0; c < cols; c++) {
                    var val = grid[r][c];
                    var norm = (val - min) / range;
                    var idx = (r * cols + c) * 4;
                    // Draw a teal-spectrum heat scale
                    imgData.data[idx] = Math.round(norm * 0 + (1 - norm) * 240);
                    imgData.data[idx + 1] = Math.round(norm * 150 + (1 - norm) * 240);
                    imgData.data[idx + 2] = Math.round(norm * 136 + (1 - norm) * 240);
                    imgData.data[idx + 3] = 255;
                  }
                }
                ctx.putImageData(imgData, 0, 0);
              }

              btnTable.addEventListener("click", renderTable);
              btnImg.addEventListener("click", renderHeatmap);
              
              // Default to table/grid view
              renderTable();
            } else if (res.type === "nd") {
              var txt = document.createElement("div");
              txt.className = "lineage-hint";
              txt.textContent = res.message || "Tensors of rank 3+ are unsupported. Add a slice query to select a plane.";
              visualWrapper.appendChild(txt);
            } else if (res.type === "json") {
              var pre = document.createElement("pre");
              pre.style.margin = "0";
              pre.style.fontSize = "11px";
              pre.style.maxHeight = "120px";
              pre.style.overflow = "auto";
              pre.textContent = JSON.stringify(res.data, null, 2);
              visualWrapper.appendChild(pre);
            }
          })
          .catch(function (err) {
            visualWrapper.innerHTML = '<div class="lineage-hint" style="color: #ff5252;">Error loading slice: ' + err.message + '</div>';
          });
      }

      if (meta.shape && meta.shape.length > 1) {
        var sliceBar = document.createElement("div");
        sliceBar.className = "exec-slice-container";
        
        sliceInput = document.createElement("input");
        sliceInput.type = "text";
        sliceInput.className = "exec-slice-input";
        sliceInput.placeholder = "Slice E.g. [0:10, 0] or [:]";
        
        var sliceBtn = document.createElement("button");
        sliceBtn.className = "exec-slice-btn";
        sliceBtn.textContent = "Apply";
        sliceBtn.addEventListener("click", function () {
          loadSlice(sliceInput.value);
        });

        sliceBar.appendChild(sliceInput);
        sliceBar.appendChild(sliceBtn);
        item.appendChild(sliceBar);
      }

      item.appendChild(visualWrapper);
      container.appendChild(item);
      
      // Load initial un-sliced value
      loadSlice("");
    }

    function populateExecutionTab(nodeId) {
      executionRequestSerial += 1;
      var requestSerial = executionRequestSerial;
      var execEmpty = document.getElementById("execution-empty");
      var execContent = document.getElementById("execution-content");
      var inputsList = document.getElementById("exec-inputs-list");
      var outputsList = document.getElementById("exec-outputs-list");

      if (!inputsList || !outputsList) return;
      inputsList.innerHTML = "";
      outputsList.innerHTML = "";

      var runId = options.getRunId ? options.getRunId() : null;
      if (!runId || !options.isApiAvailable || !options.isApiAvailable()) {
        if (execEmpty) execEmpty.style.display = "block";
        if (execContent) execContent.classList.add("hidden");
        return;
      }

      fetch("/api/cdg/runs/" + runId + "/nodes/" + nodeId + "/values")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          if (requestSerial !== executionRequestSerial) return;
          var hasInputs = data.inputs && Object.keys(data.inputs).length > 0;
          var hasOutputs = data.outputs && Object.keys(data.outputs).length > 0;

          if (!hasInputs && !hasOutputs) {
            if (execEmpty) execEmpty.style.display = "block";
            if (execContent) execContent.classList.add("hidden");
            return;
          }

          if (execEmpty) execEmpty.style.display = "none";
          if (execContent) execContent.classList.remove("hidden");

          if (hasInputs) {
            Object.keys(data.inputs).forEach(function (name) {
              var row = document.createElement("div");
              inputsList.appendChild(row);
              renderVariableVisual(row, runId, nodeId, name, true, data.inputs[name]);
            });
          } else {
            inputsList.innerHTML = '<div class="lineage-hint">(none)</div>';
          }

          if (hasOutputs) {
            Object.keys(data.outputs).forEach(function (name) {
              var row = document.createElement("div");
              outputsList.appendChild(row);
              renderVariableVisual(row, runId, nodeId, name, false, data.outputs[name]);
            });
          } else {
            outputsList.innerHTML = '<div class="lineage-hint">(none)</div>';
          }
        })
        .catch(function (err) {
          if (requestSerial !== executionRequestSerial) return;
          console.error("Failed to query node execution values:", err);
          if (execEmpty) execEmpty.style.display = "block";
          if (execContent) execContent.classList.add("hidden");
        });
    }

    function buildLineageTree(startId, direction, container, maxDepth) {
      var cy = options.getCy();
      var visited = {};
      visited[startId] = true;

      function getNeighbors(nid) {
        var cyNode = cy.getElementById(nid);
        if (!cyNode || cyNode.length === 0) return [];
        var edges = direction === "upstream"
          ? cyNode.incomers("edge[edgeType='dataflow']")
          : cyNode.outgoers("edge[edgeType='dataflow']");
        var results = [];
        edges.forEach(function (e) {
          results.push({
            id: direction === "upstream" ? e.data("source") : e.data("target"),
            edgeLabel: e.data("outputName") + " \u2192 " + e.data("inputName")
          });
        });
        return results;
      }

      function renderLevel(nodeIds, depth) {
        if (depth > maxDepth) return;
        var nextLevel = [];
        nodeIds.forEach(function (entry) {
          if (visited[entry.id]) return;
          visited[entry.id] = true;

          var nd = options.getNodeById(entry.id);
          var colors = options.getNodeColors(nd ? (nd.concept_type || "custom") : "custom");
          var item = document.createElement("div");
          item.className = "lineage-item" + (depth > 1 ? " lineage-depth-" + depth : "");
          item.addEventListener("click", function () {
            options.focusNode(entry.id);
          });

          var swatch = document.createElement("span");
          swatch.className = "lineage-item-swatch";
          swatch.style.background = colors.bg;
          swatch.style.borderColor = colors.border;

          var name = document.createElement("span");
          name.className = "lineage-item-name";
          name.textContent = nd ? nd.name : entry.id;

          var edgeInfo = document.createElement("span");
          edgeInfo.className = "lineage-item-edge";
          edgeInfo.textContent = entry.edgeLabel;

          item.appendChild(swatch);
          item.appendChild(name);
          item.appendChild(edgeInfo);
          container.appendChild(item);

          getNeighbors(entry.id).forEach(function (n) {
            if (!visited[n.id]) nextLevel.push(n);
          });
        });

        if (nextLevel.length > 0) renderLevel(nextLevel, depth + 1);
      }

      var initial = getNeighbors(startId);
      if (initial.length === 0) {
        var empty = document.createElement("div");
        empty.className = "lineage-hint";
        empty.textContent = "(none)";
        container.appendChild(empty);
        return;
      }
      renderLevel(initial, 1);
    }

    function populateLineage(nodeId) {
      var upList = document.getElementById("lineage-upstream-list");
      var downList = document.getElementById("lineage-downstream-list");
      var hint = document.querySelector(".lineage-hint");
      if (!upList || !downList) return;
      upList.innerHTML = "";
      downList.innerHTML = "";
      if (hint) hint.style.display = "none";
      if (!options.getCy()) return;
      buildLineageTree(nodeId, "upstream", upList, 3);
      buildLineageTree(nodeId, "downstream", downList, 3);
    }

    function handleNodeSelected(node) {
      if (!node) return;
      selectedNodeId = node.node_id;
      populateDetailPanel(node);
      populateLineage(node.node_id);
      populateExecutionTab(node.node_id);
      fetchQuickFixes(node.node_id);
      detailPanel.classList.add("visible");

      var cy = options.getCy ? options.getCy() : null;
      if (cy) {
        setTimeout(function () {
          cy.resize();
        }, 50);
      }
    }

    function hide() {
      executionRequestSerial += 1;
      if (detailPanel) {
        detailPanel.classList.remove("visible");
        var cy = options.getCy ? options.getCy() : null;
        if (cy) {
          setTimeout(function () {
            cy.resize();
          }, 50);
        }
      }
    }

    detailTabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        activateTab(tab.getAttribute("data-tab"));
      });
    });

    return {
      activateTab: activateTab,
      handleNodeSelected: handleNodeSelected,
      getSelectedNodeId: function () { return selectedNodeId; },
      fetchQuickFixes: fetchQuickFixes,
      hide: hide,
      getPanel: function () { return detailPanel; },
      refreshExecutionTab: function () {
        if (selectedNodeId) populateExecutionTab(selectedNodeId);
      }
    };
  };
})(window);
