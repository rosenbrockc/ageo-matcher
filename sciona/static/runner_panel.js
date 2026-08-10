(function (global) {
  "use strict";

  global.initVisualizerRunner = function initVisualizerRunner(options) {
    var activeRunId = null;
    var currentRepo = null;
    var userInputs = {}; // Stores key-value mappings of root inputs
    var hasConfiguredInputs = false;

    var btnRunCdg = document.getElementById("btn-run-cdg");
    var btnNewInputs = document.getElementById("btn-new-inputs");
    var btnHistory = document.getElementById("btn-history");
    var btnHistoryClose = document.getElementById("btn-history-close");
    var runHistoryBrowser = document.getElementById("run-history-browser");
    var historyList = document.getElementById("history-list");

    var runModal = document.getElementById("run-modal");
    var runModalInputs = document.getElementById("run-modal-inputs");
    var runModalCancel = document.getElementById("run-modal-cancel");
    var runModalExecute = document.getElementById("run-modal-execute");
    var runModalError = document.getElementById("run-modal-error");
    var activeRunSpan = document.getElementById("active-run-id");

    var btnRunNode = document.getElementById("btn-run-node");

    // Initialize UUID
    function generateUUID() {
      if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
      }
      return "run_" + Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
    }

    function getQueryParam(name) {
      var search = window.location ? window.location.search : "";
      var match = RegExp('[?&]' + name + '=([^&]*)').exec(search);
      return match && decodeURIComponent(match[1].replace(/\+/g, ' '));
    }

    function syncUrl() {
      if (!currentRepo || !activeRunId) return;
      if (!window.location || !window.history) return;
      var params = new URLSearchParams(window.location.search);
      params.set("repo", currentRepo);
      params.set("run_id", activeRunId);
      var newUrl = window.location.pathname + "?" + params.toString();
      window.history.replaceState({ path: newUrl }, "", newUrl);
    }

    function initSession() {
      var qId = getQueryParam("run_id");
      if (qId) {
        activeRunId = qId;
        hasConfiguredInputs = true;
        if (btnNewInputs) btnNewInputs.classList.remove("hidden");
      } else {
        activeRunId = generateUUID();
        hasConfiguredInputs = false;
        if (btnNewInputs) btnNewInputs.classList.add("hidden");
      }
      if (activeRunSpan) activeRunSpan.textContent = activeRunId;
      syncUrl();
    }

    // Graph node state decoration
    function decorateNodeStatuses(trace) {
      var cy = options.getCy();
      if (!cy) return;

      trace.forEach(function (step) {
        var el = cy.getElementById(step.node_id);
        if (el && el.length > 0) {
          el.removeClass("exec-success exec-failed exec-cached");
          if (step.cached) {
            el.addClass("exec-cached");
          } else {
            el.addClass("exec-success");
          }
        }
      });
    }

    function markErrorNode(nodeId) {
      var cy = options.getCy();
      if (!cy || !nodeId) return;
      var el = cy.getElementById(nodeId);
      if (el && el.length > 0) {
        el.removeClass("exec-success exec-failed exec-cached");
        el.addClass("exec-failed");
      }
    }

    // Existing completed nodes query
    function fetchExistingRunNodes() {
      if (!activeRunId) return;
      fetch("/api/cdg/runs/" + activeRunId + "/existing")
        .then(function (res) { return res.json(); })
        .then(function (data) {
          var cy = options.getCy();
          if (!cy) return;
          
          // Clear previous output states
          cy.nodes().removeClass("has-outputs");
          
          if (data && data.nodes) {
            data.nodes.forEach(function (nodeId) {
              var el = cy.getElementById(nodeId);
              if (el && el.length > 0) {
                el.addClass("has-outputs");
              }
            });
            // Update the execution tab if a node is currently selected
            var activeTab = document.querySelector(".detail-tab.active");
            if (activeTab && activeTab.getAttribute("data-tab") === "execution") {
              options.detailControls.refreshExecutionTab();
            }
          }
        })
        .catch(function (err) {
          console.error("Failed to query existing run nodes:", err);
        });
    }

    // Input Port Finder: identifies root input parameters of CDG
    function findRootInputs() {
      var data = options.getCurrentData();
      if (!data) return [];

      var nodes = data.nodes || [];
      var edges = data.edges || [];

      // Only evaluate leaf nodes for parameters
      var leafNodes = nodes.filter(function (n) { return n.status === "atomic"; });
      var rootInputs = [];
      var rootInputsByName = {};

      leafNodes.forEach(function (node) {
        var inputs = node.inputs || [];
        inputs.forEach(function (inp) {
          // Check if any incoming data flow edge feeds this port
          var edgeFound = edges.some(function (edge) {
            return edge.target_id === node.node_id && edge.input_name === inp.name;
          });

          if (!edgeFound) {
            if (rootInputsByName[inp.name]) {
              rootInputsByName[inp.name].requiredBy.push(node.name);
              rootInputsByName[inp.name].nodeName = rootInputsByName[inp.name].requiredBy.join(", ");
              return;
            }
            var rootInput = {
              nodeId: node.node_id,
              nodeName: node.name,
              requiredBy: [node.name],
              matched_primitive: node.matched_primitive || "",
              name: inp.name,
              type_desc: inp.type_desc,
              constraints: inp.constraints
            };
            rootInputsByName[inp.name] = rootInput;
            rootInputs.push(rootInput);
          }
        });
      });

      return rootInputs;
    }

    // Generate Config Modal Fields
    function buildInputForm() {
      var inputs = findRootInputs();
      runModalInputs.innerHTML = "";
      runModalError.style.display = "none";

      if (inputs.length === 0) {
        runModalInputs.innerHTML = '<div class="lineage-hint">This CDG has no root input parameters. Ready to execute!</div>';
        return;
      }

      function applyCatalogDefaults(dataset) {
        var schemaDefaults = dataset && dataset.schema_json && dataset.schema_json.input_defaults;
        var defaults = Object.assign({}, schemaDefaults || {}, (dataset && dataset.input_defaults) || {});
        var frequency = dataset && dataset.sampling_metadata && dataset.sampling_metadata.frequency_hz;
        if (frequency != null) {
          ["sampling_rate", "sample_rate", "frequency_hz", "fs"].forEach(function (name) {
            if (defaults[name] == null) defaults[name] = frequency;
          });
        }
        Object.keys(defaults).forEach(function (name) {
          var targetGroup = runModalInputs.querySelector('[data-input-name="' + name + '"]');
          if (!targetGroup) return;
          var targetSelect = targetGroup.querySelector(".run-input-select");
          var targetInput = targetGroup.querySelector("input.run-input-field");
          if (targetSelect && targetSelect.value === "constant" && targetInput && !targetInput.value) {
            targetInput.value = String(defaults[name]);
          }
        });
      }

      inputs.forEach(function (inp) {
        var group = document.createElement("div");
        group.className = "run-input-group";

        var label = document.createElement("label");
        label.innerHTML = (inp.name || "input") + ' <span class="type-annotation">(' + (inp.type_desc || "Any") + ')</span>';
        
        var sublabel = document.createElement("div");
        sublabel.className = "exec-value-meta";
        sublabel.textContent = "Required by: " + inp.nodeName;
        sublabel.style.marginBottom = "2px";

        // Dropdown type selector (Constant, JSON, File Path, File Upload, Curated Dataset)
        var select = document.createElement("select");
        select.className = "run-input-select";
        select.style.marginBottom = "5px";
        
        var isArrayType = inp.type_desc.indexOf("NDArray") !== -1 || inp.type_desc.indexOf("ndarray") !== -1 || inp.type_desc.indexOf("matrix") !== -1;
        
        select.innerHTML = 
          '<option value="constant">Constant (int/float/str/bool)</option>' +
          '<option value="json">JSON Structure (tuple/list/dict)</option>' +
          '<option value="path">' + (isArrayType ? "File Path (npy/parquet/csv)" : "File Path") + '</option>' +
          '<option value="upload">File Upload (npy/parquet/csv)</option>' +
          '<option value="curated">Curated S3 Dataset</option>';

        var fieldContainer = document.createElement("div");
        
        // Input fields for different types
        var txtInput = document.createElement("input");
        txtInput.type = "text";
        txtInput.className = "run-input-field";
        txtInput.style.width = "100%";
        txtInput.placeholder = isArrayType ? "E.g. /path/to/data.npy" : "E.g. 42 or standard_value";

        var textarea = document.createElement("textarea");
        textarea.className = "run-input-field";
        textarea.style.width = "100%";
        textarea.style.height = "60px";
        textarea.placeholder = "E.g. [1, 2, 3] or {\"option\": true}";
        textarea.style.display = "none";

        var uploadContainer = document.createElement("div");
        uploadContainer.className = "run-input-file-container";
        uploadContainer.style.display = "none";

        var fileLabel = document.createElement("span");
        fileLabel.className = "exec-value-meta";
        fileLabel.textContent = "No file selected";
        fileLabel.style.flex = "1";

        var fileInput = document.createElement("input");
        fileInput.type = "file";
        fileInput.accept = ".npy,.npz,.parquet,.csv,.json";
        fileInput.style.display = "none";

        var fileBtn = document.createElement("button");
        fileBtn.className = "run-input-file-btn";
        fileBtn.textContent = "Choose File";
        fileBtn.type = "button";
        fileBtn.addEventListener("click", function () { fileInput.click(); });

        fileInput.addEventListener("change", function () {
          if (fileInput.files.length > 0) {
            var file = fileInput.files[0];
            fileLabel.textContent = "Uploading: " + file.name;
            
            var formData = new FormData();
            formData.append("file", file);
            
            fetch("/api/cdg/upload?run_id=" + activeRunId, {
              method: "POST",
              body: formData
            })
            .then(function (res) {
              if (!res.ok) throw new Error("Upload failed with status " + res.status);
              return res.json();
            })
            .then(function (data) {
              fileLabel.textContent = "Uploaded: " + file.name;
              txtInput.value = data.filepath;
            })
            .catch(function (err) {
              fileLabel.textContent = "Upload failed! " + err.message;
              console.error("Upload error:", err);
            });
          }
        });

        uploadContainer.appendChild(fileBtn);
        uploadContainer.appendChild(fileLabel);
        uploadContainer.appendChild(fileInput);

        var curatedContainer = document.createElement("div");
        curatedContainer.className = "run-curated-container";
        curatedContainer.style.display = "none";
        curatedContainer.style.flexDirection = "column";
        curatedContainer.style.gap = "5px";

        var curatedSelect = document.createElement("select");
        curatedSelect.className = "run-curated-select run-input-field";
        curatedSelect.style.width = "100%";
        curatedSelect.innerHTML = '<option value="">Loading curated inputs...</option>';

        var curatedPreview = document.createElement("div");
        curatedPreview.className = "run-curated-preview";
        curatedPreview.style.marginTop = "4px";

        curatedContainer.appendChild(curatedSelect);
        curatedContainer.appendChild(curatedPreview);

        fieldContainer.appendChild(txtInput);
        fieldContainer.appendChild(textarea);
        fieldContainer.appendChild(uploadContainer);
        fieldContainer.appendChild(curatedContainer);

        var datasetsCache = [];

        function updateCuratedPreview() {
          var fqn = curatedSelect.value;
          var found = datasetsCache.find(function (d) { return d.fqn === fqn; });
          if (found) {
            curatedPreview.innerHTML = 
              '<div style="margin-top: 8px; padding: 10px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; font-size: 11px;">' +
                '<div><strong>Dataset:</strong> ' + found.name + '</div>' +
                '<div><strong>FQN:</strong> <code>' + found.fqn + '</code></div>' +
                '<div><strong>Shape:</strong> ' + JSON.stringify(found.shape) + ' (' + (found.dtype || "unknown") + ')</div>' +
                '<div><strong>Description:</strong> ' + found.description + '</div>' +
                (found.attribution && found.attribution.source ? '<div><strong>Source:</strong> <a href="' + (found.attribution.url || "#") + '" target="_blank" style="color: #2563eb; text-decoration: none;">' + found.attribution.source + '</a></div>' : '') +
              '</div>';
            applyCatalogDefaults(found);
          } else {
            curatedPreview.innerHTML = "";
          }
        }

        curatedSelect.addEventListener("change", updateCuratedPreview);

        var isCuratedLoaded = false;

        select.addEventListener("change", function () {
          var type = select.value;
          if (type === "constant") {
            txtInput.style.display = "block";
            textarea.style.display = "none";
            uploadContainer.style.display = "none";
            curatedContainer.style.display = "none";
            txtInput.placeholder = "E.g. 42 or standard_value";
          } else if (type === "json") {
            txtInput.style.display = "none";
            textarea.style.display = "block";
            uploadContainer.style.display = "none";
            curatedContainer.style.display = "none";
          } else if (type === "path") {
            txtInput.style.display = "block";
            textarea.style.display = "none";
            uploadContainer.style.display = "none";
            curatedContainer.style.display = "none";
            txtInput.placeholder = isArrayType ? "E.g. /path/to/data.npy" : "E.g. 42 or standard_value";
          } else if (type === "upload") {
            txtInput.style.display = "none";
            textarea.style.display = "none";
            uploadContainer.style.display = "flex";
            curatedContainer.style.display = "none";
          } else if (type === "curated") {
            txtInput.style.display = "none";
            textarea.style.display = "none";
            uploadContainer.style.display = "none";
            curatedContainer.style.display = "flex";

            if (!isCuratedLoaded) {
              var primitiveName = inp.matched_primitive || inp.nodeName;
              curatedSelect.innerHTML = '<option value="">Loading curated inputs...</option>';
              fetch("/api/cdg/primitive/" + encodeURIComponent(primitiveName) + "/curated_inputs?input_port=" + encodeURIComponent(inp.name || ""))
                .then(function (res) { return res.json(); })
                .then(function (data) {
                  isCuratedLoaded = true;
                  datasetsCache = data;
                  curatedSelect.innerHTML = "";

                  if (!data || data.length === 0) {
                    fetch("/api/datasets")
                      .then(function (res2) { return res2.json(); })
                      .then(function (allData) {
                        datasetsCache = allData;
                        curatedSelect.innerHTML = "";
                        if (!allData || allData.length === 0) {
                          curatedSelect.innerHTML = '<option value="">(No datasets available)</option>';
                          curatedPreview.innerHTML = '<span class="lineage-hint">No datasets cataloged.</span>';
                          return;
                        }
                        allData.forEach(function (ds) {
                          var opt = document.createElement("option");
                          opt.value = ds.fqn;
                          opt.textContent = ds.name;
                          curatedSelect.appendChild(opt);
                        });
                        updateCuratedPreview();
                      });
                    return;
                  }

                  data.forEach(function (ds) {
                    var opt = document.createElement("option");
                    opt.value = ds.fqn;
                    opt.textContent = ds.name;
                    curatedSelect.appendChild(opt);
                  });

                  updateCuratedPreview();
                })
                .catch(function (err) {
                  curatedSelect.innerHTML = '<option value="">(Error loading datasets)</option>';
                  curatedPreview.innerHTML = '<span class="lineage-hint" style="color: #ef4444;">Failed to load dataset suggestion mapping.</span>';
                  console.error("Failed to load curated inputs:", err);
                });
            }
          }
        });

        if (isArrayType) {
          select.value = "curated";
          setTimeout(function () {
            select.dispatchEvent(new Event("change"));
          }, 0);
        }

        // Pre-populate if we have stored values
        var cachedVal = userInputs[inp.name];
        if (cachedVal !== undefined) {
          if (typeof cachedVal === "object") {
            select.value = "json";
            textarea.value = JSON.stringify(cachedVal);
            txtInput.style.display = "none";
            textarea.style.display = "block";
          } else {
            txtInput.value = String(cachedVal);
          }
        }

        group.appendChild(label);
        group.appendChild(sublabel);
        group.appendChild(select);
        group.appendChild(fieldContainer);

        // Tag inputs so we can query them on execution
        group.setAttribute("data-input-name", inp.name);
        group.setAttribute("data-type-desc", inp.type_desc);

        runModalInputs.appendChild(group);
      });
    }

    // Modal Form Extraction
    function getFormValues() {
      var values = {};
      var groups = runModalInputs.querySelectorAll(".run-input-group");
      var errorFound = false;

      groups.forEach(function (group) {
        var name = group.getAttribute("data-input-name");
        var typeDesc = group.getAttribute("data-type-desc");
        var select = group.querySelector(".run-input-select");
        var type = select ? select.value : "constant";

        var val = "";
        if (type === "constant") {
          val = group.querySelector("input").value;
          if (!String(val).trim()) {
            runModalError.textContent = "A value is required for input '" + name + "'.";
            runModalError.style.display = "block";
            errorFound = true;
          }
        } else if (type === "json") {
          var rawJson = group.querySelector("textarea").value.trim();
          try {
            val = JSON.parse(rawJson);
          } catch (e) {
            runModalError.textContent = "Invalid JSON in input '" + name + "': " + e.message;
            runModalError.style.display = "block";
            errorFound = true;
          }
        } else if (type === "path") {
          val = group.querySelector("input").value;
          if (!String(val).trim()) {
            runModalError.textContent = "A path is required for input '" + name + "'.";
            runModalError.style.display = "block";
            errorFound = true;
          }
        } else if (type === "upload") {
          val = group.querySelector("input").value; // filled post-upload
          if (!val) {
            runModalError.textContent = "File upload required for input '" + name + "'.";
            runModalError.style.display = "block";
            errorFound = true;
          }
        } else if (type === "curated") {
          var datasetFqn = group.querySelector(".run-curated-select").value;
          if (!datasetFqn) {
            runModalError.textContent = "Curated dataset selection required for input '" + name + "'.";
            runModalError.style.display = "block";
            errorFound = true;
          } else {
            val = { "$dataset": datasetFqn };
          }
        }
        values[name] = val;
      });

      if (errorFound) runModalError.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return errorFound ? null : values;
    }

    function selectedDatasetFqn(inputs) {
      var names = Object.keys(inputs || {});
      for (var index = 0; index < names.length; index += 1) {
        var value = inputs[names[index]];
        if (value && typeof value === "object" && value.$dataset) return value.$dataset;
      }
      return "";
    }

    function executableVersions() {
      var trace = options.getEvolutionTrace ? options.getEvolutionTrace() : null;
      if (!trace || !Array.isArray(trace.versions) || trace.versions.length < 2) return [];
      return trace.versions.filter(function (version) {
        var nodes = version.graph && version.graph.nodes;
        return Array.isArray(nodes) && nodes.length > 0 && nodes.every(function (node) {
          return node.status === "atomic" && Boolean(node.matched_primitive);
        });
      });
    }

    function versionRunId(baseRunId, versionId) {
      return baseRunId + "--" + String(versionId).replace(/[^a-zA-Z0-9_-]/g, "-");
    }

    function fetchJson(url, body) {
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (data) {
            throw new Error(data.detail || "Server error " + res.status);
          });
        }
        return res.json();
      });
    }

    function finishExecution(data) {
      runModalExecute.disabled = false;
      runModalExecute.textContent = "Execute";
      runModal.classList.add("hidden");
      hasConfiguredInputs = true;
      if (btnNewInputs) btnNewInputs.classList.remove("hidden");
      if (data && data.trace) decorateNodeStatuses(data.trace);
      fetchExistingRunNodes();
      if (options.onExecutionComplete) options.onExecutionComplete(data || {});
    }

    function showExecutionError(err) {
      runModalExecute.disabled = false;
      runModalExecute.textContent = "Execute";
      runModalError.textContent = err.message;
      runModalError.style.display = "block";
      var nodeMatch = /at node '([^']+)'/.exec(err.message);
      if (nodeMatch && nodeMatch[1]) markErrorNode(nodeMatch[1]);
    }

    function executeEvolution(inputs, versions, datasetFqn) {
      var baseRunId = activeRunId.split("--")[0];
      var lastResult = null;
      var chain = Promise.resolve();
      versions.forEach(function (version, index) {
        chain = chain.then(function () {
          var runId = versionRunId(baseRunId, version.version_id);
          runModalExecute.textContent = "Evaluating " + (index + 1) + " of " + versions.length + "...";
          return fetchJson(
            "/api/cdg/run?repo=" + encodeURIComponent(currentRepo) + "&run_id=" + encodeURIComponent(runId),
            { inputs: inputs, cdg: version.graph }
          ).then(function (runResult) {
            lastResult = runResult;
            return fetchJson(
              "/api/cdg/runs/" + encodeURIComponent(runId) + "/evaluate",
              { dataset_fqn: datasetFqn, version_id: version.version_id }
            );
          }).then(function (evaluation) {
            if (options.onVersionEvaluated) {
              options.onVersionEvaluated(version.version_id, evaluation, runId);
            }
          });
        });
      });
      return chain.then(function () {
        var activeVersion = options.getActiveEvolutionVersion
          ? options.getActiveEvolutionVersion()
          : versions[versions.length - 1];
        if (activeVersion && activeVersion.run_id) {
          activeRunId = activeVersion.run_id;
        } else {
          activeRunId = versionRunId(baseRunId, versions[versions.length - 1].version_id);
        }
        if (activeRunSpan) activeRunSpan.textContent = activeRunId;
        syncUrl();
        finishExecution(lastResult);
        return lastResult;
      }).catch(function (error) {
        showExecutionError(error);
        throw error;
      });
    }

    // CDG Runner Endpoint Caller
    function triggerExecution(inputs, targetNodeId) {
      if (!currentRepo || !activeRunId) {
        runModalError.textContent = "This graph does not have an executable repository or run session.";
        runModalError.style.display = "block";
        return;
      }

      runModalError.style.display = "none";
      runModalExecute.disabled = true;
      runModalExecute.textContent = "Executing...";

      var versions = targetNodeId ? [] : executableVersions();
      var datasetFqn = selectedDatasetFqn(inputs);
      if (versions.length && datasetFqn) {
        executeEvolution(inputs, versions, datasetFqn).catch(function () {});
        return;
      }

      fetchJson(
        "/api/cdg/run?repo=" + encodeURIComponent(currentRepo) + "&run_id=" + encodeURIComponent(activeRunId) +
          (targetNodeId ? "&target_node_id=" + encodeURIComponent(targetNodeId) : ""),
        { inputs: inputs, cdg: options.getCurrentData() }
      ).then(finishExecution).catch(showExecutionError);
    }

    // History Panel List Fetcher
    function fetchRunHistory() {
      if (!currentRepo) return;
      
      historyList.innerHTML = '<div class="lineage-hint">Loading history...</div>';
      
      fetch("/api/cdg/runs?repo=" + encodeURIComponent(currentRepo))
        .then(function (res) { return res.json(); })
        .then(function (data) {
          historyList.innerHTML = "";
          if (!data || !data.runs || data.runs.length === 0) {
            historyList.innerHTML = '<div class="lineage-hint">No history found for this CDG.</div>';
            return;
          }

          data.runs.forEach(function (run) {
            var item = document.createElement("div");
            item.className = "history-item";
            
            var date = new Date(run.timestamp * 1000).toLocaleString();
            var shortId = run.run_id.substring(0, 8) + "...";
            var statusClass = "history-status-" + (run.status || "running");

            item.innerHTML = 
              '<div class="history-item-header">' +
                '<span class="history-item-id">Run ID: ' + shortId + '</span>' +
                '<span class="history-item-time">' + date + '</span>' +
              '</div>' +
              '<div class="history-item-footer">' +
                '<span class="history-item-status ' + statusClass + '">' + (run.status || "running") + '</span>' +
                (run.target_node_id ? '<span class="history-item-target">Target: ' + run.target_node_id + '</span>' : '<span class="history-item-target">Full Run</span>') +
              '</div>';

            item.addEventListener("click", function () {
              // Load historical run ID
              activeRunId = run.run_id;
              if (activeRunSpan) activeRunSpan.textContent = activeRunId;
              syncUrl();
              runHistoryBrowser.classList.remove("visible");

              hasConfiguredInputs = true;
              if (btnNewInputs) btnNewInputs.classList.remove("hidden");

              // Fetch which nodes have outputs and decorate
              fetchExistingRunNodes();
            });

            historyList.appendChild(item);
          });
        })
        .catch(function (err) {
          historyList.innerHTML = '<div class="lineage-hint" style="color: #ff5252;">Failed to load history: ' + err.message + '</div>';
        });
    }

    // Modal Actions
    if (btnRunCdg) {
      btnRunCdg.addEventListener("click", function () {
        buildInputForm();
        runModal.classList.remove("hidden");
      });
    }

    if (runModalCancel) {
      runModalCancel.addEventListener("click", function () {
        runModal.classList.add("hidden");
      });
    }

    if (runModalExecute) {
      runModalExecute.addEventListener("click", function () {
        var vals = getFormValues();
        if (vals !== null) {
          userInputs = vals;
          triggerExecution(vals);
        }
      });
    }

    // Reset Session (New Inputs)
    if (btnNewInputs) {
      btnNewInputs.addEventListener("click", function () {
        activeRunId = generateUUID();
        userInputs = {};
        hasConfiguredInputs = false;
        btnNewInputs.classList.add("hidden");
        if (activeRunSpan) activeRunSpan.textContent = activeRunId;
        syncUrl();

        // Clear cytoscape nodes execution styles
        var cy = options.getCy();
        if (cy) {
          cy.nodes().removeClass("exec-success exec-failed exec-cached has-outputs");
        }

        // Hide Execution panel variables
        options.detailControls.refreshExecutionTab();

        // Immediately open input modal
        buildInputForm();
        runModal.classList.remove("hidden");
      });
    }

    // History Toggle Actions
    if (btnHistory) {
      btnHistory.addEventListener("click", function () {
        fetchRunHistory();
        runHistoryBrowser.classList.add("visible");
      });
    }

    if (btnHistoryClose) {
      btnHistoryClose.addEventListener("click", function () {
        runHistoryBrowser.classList.remove("visible");
      });
    }

    // Node-Level Run Trigger in sidebar
    if (btnRunNode) {
      btnRunNode.addEventListener("click", function () {
        var nid = options.detailControls.getSelectedNodeId();
        if (!nid) return;

        // If inputs are not yet configured, make user configure them first
        if (!hasConfiguredInputs) {
          buildInputForm();
          runModal.classList.remove("hidden");
          
          // Flash modal error box
          runModalError.textContent = "Inputs must be configured before running node '" + nid + "'. Fill input parameters and click Execute to start.";
          runModalError.style.display = "block";
          return;
        }

        // Direct execution
        triggerExecution(userInputs, nid);
      });
    }

    return {
      openInputDialog: function () {
        buildInputForm();
        runModal.classList.remove("hidden");
      },
      closeInputDialog: function () {
        runModal.classList.add("hidden");
      },
      setRepo: function (repo) {
        var repoChanged = currentRepo !== repo;
        currentRepo = repo;
        if (btnRunCdg) btnRunCdg.classList.remove("hidden");
        if (btnHistory) btnHistory.classList.remove("hidden");
        if (repoChanged || !activeRunId) {
          initSession();
          fetchExistingRunNodes();
        }
      },
      getActiveRunId: function () { return activeRunId; },
      setActiveRunId: function (runId) {
        if (!runId || activeRunId === runId) return;
        activeRunId = runId;
        if (activeRunSpan) activeRunSpan.textContent = activeRunId;
        syncUrl();
        fetchExistingRunNodes();
      },
      evaluateVersion: function (version) {
        var datasetFqn = selectedDatasetFqn(userInputs);
        if (!hasConfiguredInputs || !datasetFqn) {
          return Promise.reject(new Error(
            "Configure catalog evaluation inputs before creating a refinement branch."
          ));
        }
        return executeEvolution(userInputs, [version], datasetFqn);
      },
      hasOutputs: function () { return hasConfiguredInputs; },
      refreshOutputs: fetchExistingRunNodes
    };
  };
})(window);
