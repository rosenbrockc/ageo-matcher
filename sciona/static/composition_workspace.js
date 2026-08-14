(function (global) {
  "use strict";

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function slug(value) {
    return String(value || "component").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  function boundaryGraph(operation, node, inputName, workspaceId, label) {
    var inputs = operation === "replacement"
      ? clone(node.inputs || [])
      : clone(inputName
        ? (node.inputs || []).filter(function (spec) { return spec.name === inputName; })
        : (node.inputs || []));
    var outputs = operation === "replacement"
      ? clone(node.outputs || [])
      : clone(inputs);
    return {
      nodes: [{
        node_id: "component",
        name: label,
        description: "Develop this contract as an independently executable and reusable CDG.",
        concept_type: node.concept_type || "custom",
        status: "pending",
        inputs: inputs,
        outputs: outputs,
        depth: 0
      }],
      edges: [],
      metadata: {
        goal: label,
        paradigm: node.concept_type || "custom",
        repo: "workspace/" + workspaceId,
        composition_boundary: { operation: operation, input_name: inputName || "" }
      }
    };
  }

  global.initCompositionWorkspace = function initCompositionWorkspace(options) {
    var bar = document.getElementById("cdg-workspace-bar");
    var tabs = document.getElementById("cdg-workspace-tabs");
    var applyButton = document.getElementById("btn-apply-workspace");
    var modal = document.getElementById("composition-modal");
    var title = document.getElementById("composition-modal-title");
    var summary = document.getElementById("composition-modal-summary");
    var inputField = document.getElementById("composition-input-field");
    var inputSelect = document.getElementById("composition-input-select");
    var nameInput = document.getElementById("composition-workspace-name");
    var cancelButton = document.getElementById("composition-modal-cancel");
    var createButton = document.getElementById("composition-modal-create");
    var workspaces = [];
    var activeId = "";
    var pending = null;
    var sequence = 0;
    var familyId = "";

    function persist() {
      if (!options.saveFamily || !workspaces.length) return Promise.resolve();
      return Promise.resolve(options.saveFamily(familyId, {
        workspaces: clone(workspaces),
        active_workspace_id: activeId
      })).catch(function () {});
    }

    function activeWorkspace() {
      return workspaces.find(function (workspace) { return workspace.workspace_id === activeId; }) || null;
    }

    function saveActiveTrace() {
      var workspace = activeWorkspace();
      if (workspace) {
        workspace.trace = clone(options.getTrace());
        var version = options.getActiveVersion ? options.getActiveVersion() : null;
        if (version) workspace.active_version_id = version.version_id;
        if (options.getRunId) workspace.run_id = options.getRunId();
      }
    }

    function render(shouldPersist) {
      tabs.innerHTML = "";
      workspaces.forEach(function (workspace) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "cdg-workspace-tab" + (workspace.workspace_id === activeId ? " active" : "");
        button.setAttribute("data-workspace-id", workspace.workspace_id);
        var icon = document.createElement("i");
        icon.setAttribute("data-lucide", workspace.parent_id ? "blocks" : "workflow");
        var copy = document.createElement("strong");
        copy.textContent = workspace.label;
        var kind = document.createElement("small");
        kind.textContent = workspace.parent_id ? workspace.operation : "parent";
        button.appendChild(icon);
        button.appendChild(copy);
        button.appendChild(kind);
        button.addEventListener("click", function () { select(workspace.workspace_id); });
        tabs.appendChild(button);
      });
      bar.classList.toggle("hidden", workspaces.length < 2);
      var active = activeWorkspace();
      applyButton.classList.toggle("hidden", !active || !active.parent_id);
      var activeVersion = active && active.trace.versions.find(function (version) {
        return version.version_id === active.active_version_id;
      });
      if (!activeVersion && active) activeVersion = active.trace.versions[active.trace.versions.length - 1];
      var nodes = activeVersion && activeVersion.graph && activeVersion.graph.nodes;
      var ready = Array.isArray(nodes) && nodes.length > 0 && nodes.every(function (node) {
        return node.status === "atomic" && Boolean(node.matched_primitive);
      });
      applyButton.disabled = Boolean(active && active.parent_id && !ready);
      applyButton.title = ready
        ? "Insert this pinned CDG version into its parent"
        : "Ground and execute every child node before applying it to the parent";
      if (global.lucide) global.lucide.createIcons();
      if (shouldPersist !== false) persist();
    }

    function select(workspaceId) {
      if (workspaceId === activeId) return;
      saveActiveTrace();
      activeId = workspaceId;
      var workspace = activeWorkspace();
      if (workspace) {
        options.loadTrace(clone(workspace.trace));
        if (workspace.active_version_id && options.selectVersion) {
          options.selectVersion(workspace.active_version_id);
        }
        if (workspace.run_id && options.setRunId) options.setRunId(workspace.run_id);
      }
      render();
    }

    function registerParent(trace, label) {
      if (workspaces.length) return;
      var workspace = {
        workspace_id: "parent",
        label: label || trace.goal || "Primary CDG",
        trace: clone(trace),
        active_version_id: trace.versions[trace.versions.length - 1].version_id,
        run_id: options.getRunId ? options.getRunId() : "",
        parent_id: "",
        operation: ""
      };
      workspaces.push(workspace);
      activeId = workspace.workspace_id;
      familyId = options.getFamilyId ? options.getFamilyId() : "local";
      render(false);
      if (options.loadFamily) {
        Promise.resolve(options.loadFamily(familyId)).then(function (family) {
          if (!family || !Array.isArray(family.workspaces) || !family.workspaces.length) {
            persist();
            return;
          }
          workspaces = clone(family.workspaces);
          activeId = family.active_workspace_id || workspaces[0].workspace_id;
          var restored = activeWorkspace();
          if (restored) {
            options.loadTrace(clone(restored.trace));
            if (restored.active_version_id && options.selectVersion) {
              options.selectVersion(restored.active_version_id);
            }
            if (restored.run_id && options.setRunId) options.setRunId(restored.run_id);
          }
          render(false);
        }).catch(function () { persist(); });
      } else {
        persist();
      }
    }

    function resetParent(trace, label) {
      workspaces = [];
      activeId = "";
      sequence = 0;
      familyId = "";
      registerParent(trace, label);
    }

    function open(operation, node) {
      if (!node) return;
      pending = { operation: operation, node: clone(node) };
      inputSelect.innerHTML = "";
      if ((node.inputs || []).length > 1) {
        var allOption = document.createElement("option");
        allOption.value = "";
        allOption.textContent = "All inputs (recommended)";
        inputSelect.appendChild(allOption);
      }
      (node.inputs || []).forEach(function (spec) {
        var option = document.createElement("option");
        option.value = spec.name;
        option.textContent = spec.name + " (" + (spec.type_desc || "Any") + ")";
        inputSelect.appendChild(option);
      });
      inputField.classList.toggle("hidden", operation !== "predecessor");
      title.textContent = operation === "replacement" ? "Develop replacement CDG" : "Develop predecessor CDG";
      summary.textContent = operation === "replacement"
        ? "The child must expose the same external inputs and outputs as " + node.name + "."
        : "The child will transform one selected input before it reaches " + node.name + ".";
      nameInput.value = (operation === "replacement" ? "Replacement for " : "Features before ") + node.name;
      modal.classList.remove("hidden");
      nameInput.focus();
    }

    function create() {
      if (!pending) return;
      saveActiveTrace();
      var parent = activeWorkspace();
      var label = nameInput.value.trim() || "Component CDG";
      sequence += 1;
      var workspaceId = slug(label) + "-" + sequence;
      var inputName = pending.operation === "predecessor" ? inputSelect.value : "";
      var graph = boundaryGraph(pending.operation, pending.node, inputName, workspaceId, label);
      var trace = {
        schema_version: "1.0",
        goal: label,
        objective: parent.trace.objective || "",
        versions: [{
          version_id: "component-initial",
          label: "Boundary contract",
          phase: "initial_match",
          loss: null,
          graph: graph,
          status: "candidate"
        }],
        transitions: []
      };
      workspaces.push({
        workspace_id: workspaceId,
        label: label,
        trace: trace,
        active_version_id: "component-initial",
        parent_id: parent.workspace_id,
        operation: pending.operation,
        target_node_id: pending.node.node_id,
        target_input_name: inputName
      });
      modal.classList.add("hidden");
      pending = null;
      activeId = workspaceId;
      options.loadTrace(clone(trace));
      render();
    }

    function apply() {
      saveActiveTrace();
      var child = activeWorkspace();
      if (!child || !child.parent_id) return;
      var parent = workspaces.find(function (workspace) { return workspace.workspace_id === child.parent_id; });
      var childVersion = child.trace.versions.find(function (version) {
        return version.version_id === child.active_version_id;
      }) || child.trace.versions[child.trace.versions.length - 1];
      var childNodes = childVersion.graph && childVersion.graph.nodes;
      if (!Array.isArray(childNodes) || !childNodes.length || childNodes.some(function (node) {
        return node.status !== "atomic" || !node.matched_primitive;
      })) {
        options.setStatus("Ground and execute every child node before applying it to the parent.");
        return;
      }
      var parentVersion = parent.trace.versions.find(function (version) {
        return version.version_id === parent.active_version_id;
      }) || parent.trace.versions[parent.trace.versions.length - 1];
      applyButton.disabled = true;
      options.compose({
        parent_cdg: parentVersion.graph,
        child_cdg: childVersion.graph,
        operation: child.operation,
        target_node_id: child.target_node_id,
        target_input_name: child.target_input_name || "",
        workspace_id: child.workspace_id,
        version_id: childVersion.version_id
      }).then(function (payload) {
        activeId = parent.workspace_id;
        options.loadTrace(clone(parent.trace));
        if (parent.active_version_id && options.selectVersion) {
          options.selectVersion(parent.active_version_id);
        }
        options.recordParentTransition(payload.updated_cdg, {
          label: (child.operation === "replacement" ? "Replace " : "Insert before ") + child.label,
          operation: "compose_" + child.operation,
          rules_applied: ["typed_cdg_" + child.operation],
          selection_reason: "Pinned " + child.label + " at version " + childVersion.version_id,
          status: "candidate"
        });
        parent.trace = clone(options.getTrace());
        var activeParentVersion = options.getActiveVersion ? options.getActiveVersion() : null;
        if (activeParentVersion) parent.active_version_id = activeParentVersion.version_id;
        render();
      }).catch(function (error) {
        options.setStatus(error.message || "Composition failed.");
      }).finally(function () {
        applyButton.disabled = false;
      });
    }

    cancelButton.addEventListener("click", function () { modal.classList.add("hidden"); pending = null; });
    createButton.addEventListener("click", create);
    applyButton.addEventListener("click", apply);

    return {
      registerParent: registerParent,
      resetParent: resetParent,
      open: open,
      getWorkspaces: function () { saveActiveTrace(); return clone(workspaces); },
      getActiveWorkspace: activeWorkspace
    };
  };
})(window);
