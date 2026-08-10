(function (global) {
  "use strict";

  global.createVisualizerGraphState = function createVisualizerGraphState(options) {
    var currentData = null;
    var expandedNodes = {};
    var nodeById = {};
    var childrenOf = {};
    var parentOf = {};
    var breadcrumbPath = [];
    var searchMatches = [];
    var searchIndex = -1;

    function setCurrentData(data) {
      currentData = data;
      expandedNodes = {};
      breadcrumbPath = [];
      searchMatches = [];
      searchIndex = -1;
      buildIndexes(data);
    }

    function buildIndexes(data) {
      nodeById = {};
      childrenOf = {};
      parentOf = {};
      data.nodes.forEach(function (node) {
        nodeById[node.node_id] = node;
        if (node.children && node.children.length > 0) {
          childrenOf[node.node_id] = node.children.slice();
        }
        if (node.parent_id) {
          parentOf[node.node_id] = node.parent_id;
        }
      });
    }

    function isNodeVisible(nodeId) {
      var pid = parentOf[nodeId];
      if (!pid) return true;
      return !!expandedNodes[pid];
    }

    function isDecomposed(nodeId) {
      return childrenOf[nodeId] && childrenOf[nodeId].length > 0;
    }

    function isExpanded(nodeId) {
      return !!expandedNodes[nodeId];
    }

    function getDescendants(nodeId) {
      var result = [];
      var stack = childrenOf[nodeId] ? childrenOf[nodeId].slice() : [];
      while (stack.length > 0) {
        var id = stack.pop();
        result.push(id);
        if (childrenOf[id]) {
          stack = stack.concat(childrenOf[id]);
        }
      }
      return result;
    }

    function renderBreadcrumb(collapseAll, collapseTo) {
      if (!options.breadcrumbBar || !options.breadcrumbContent) return;
      if (breadcrumbPath.length === 0) {
        options.breadcrumbBar.classList.add("hidden");
        return;
      }
      options.breadcrumbBar.classList.remove("hidden");
      options.breadcrumbContent.innerHTML = "";

      var rootLink = document.createElement("span");
      rootLink.className = "breadcrumb-link";
      rootLink.textContent = "Overview";
      rootLink.addEventListener("click", function () {
        collapseAll();
      });
      options.breadcrumbContent.appendChild(rootLink);

      breadcrumbPath.forEach(function (nodeId, idx) {
        var sep = document.createElement("span");
        sep.className = "breadcrumb-sep";
        sep.textContent = ">";
        options.breadcrumbContent.appendChild(sep);

        var node = nodeById[nodeId];
        var name = node ? node.name : nodeId;

        if (idx < breadcrumbPath.length - 1) {
          var link = document.createElement("span");
          link.className = "breadcrumb-link";
          link.textContent = name;
          link.addEventListener("click", (function (nid, depth) {
            return function () {
              collapseTo(nid, depth);
            };
          })(nodeId, idx));
          options.breadcrumbContent.appendChild(link);
        } else {
          var current = document.createElement("span");
          current.className = "breadcrumb-current";
          current.textContent = name;
          options.breadcrumbContent.appendChild(current);
        }
      });
    }

    function collapseAll(rebuild) {
      expandedNodes = {};
      breadcrumbPath = [];
      rebuild();
    }

    function collapseTo(nodeId, depth, rebuild) {
      breadcrumbPath = breadcrumbPath.slice(0, depth + 1);
      var keep = {};
      breadcrumbPath.forEach(function (id) { keep[id] = true; });
      Object.keys(expandedNodes).forEach(function (id) {
        if (!keep[id]) delete expandedNodes[id];
      });
      rebuild();
    }

    function toggleExpand(nodeId, rebuild) {
      if (expandedNodes[nodeId]) {
        delete expandedNodes[nodeId];
        getDescendants(nodeId).forEach(function (id) { delete expandedNodes[id]; });
        var idx = breadcrumbPath.indexOf(nodeId);
        if (idx >= 0) breadcrumbPath = breadcrumbPath.slice(0, idx);
      } else {
        expandedNodes[nodeId] = true;
        breadcrumbPath = [];
        var cur = nodeId;
        while (cur) {
          if (expandedNodes[cur]) breadcrumbPath.unshift(cur);
          cur = parentOf[cur];
        }
      }
      rebuild();
    }

    function computeVisibleNodeIds() {
      var visibleNodeIds = {};
      if (!currentData) return visibleNodeIds;
      currentData.nodes.forEach(function (node) {
        if (isNodeVisible(node.node_id)) {
          visibleNodeIds[node.node_id] = true;
        }
      });
      return visibleNodeIds;
    }

    function parseSearchQuery(raw) {
      var structured = {};
      var freeText = [];
      raw.trim().split(/\s+/).forEach(function (tok) {
        var m = tok.match(/^(type|status|depth):(.+)$/i);
        if (m) {
          structured[m[1].toLowerCase()] = m[2];
        } else {
          freeText.push(tok.toLowerCase());
        }
      });
      return { structured: structured, freeText: freeText.join(" ") };
    }

    function nodeMatchesQuery(nodeData, query) {
      if (query.structured.type) {
        var ct = (nodeData.concept_type || "").toLowerCase();
        if (ct.indexOf(query.structured.type.toLowerCase()) === -1) return false;
      }
      if (query.structured.status) {
        var st = (nodeData.status || "").toLowerCase();
        if (st !== query.structured.status.toLowerCase()) return false;
      }
      if (query.structured.depth) {
        var depthStr = query.structured.depth;
        var nodeDepth = nodeData.depth != null ? nodeData.depth : -1;
        if (depthStr.charAt(0) === ">" && nodeDepth <= parseInt(depthStr.substring(1), 10)) return false;
        if (depthStr.charAt(0) === "<" && nodeDepth >= parseInt(depthStr.substring(1), 10)) return false;
        if (depthStr.charAt(0) !== ">" && depthStr.charAt(0) !== "<" && nodeDepth !== parseInt(depthStr, 10)) return false;
      }
      if (query.freeText) {
        var haystack = ((nodeData.name || "") + " " + (nodeData.description || "")).toLowerCase();
        if (haystack.indexOf(query.freeText) === -1) return false;
      }
      return true;
    }

    function resetSearch() {
      searchMatches = [];
      searchIndex = -1;
    }

    function updateSearchMatches(matches) {
      searchMatches = matches.slice();
      searchIndex = matches.length ? 0 : -1;
    }

    function hasSearchMatches() {
      return searchMatches.length > 0;
    }

    function advanceSearchMatch() {
      if (searchMatches.length <= 1) return null;
      searchIndex = (searchIndex + 1) % searchMatches.length;
      return {
        nodeId: searchMatches[searchIndex],
        index: searchIndex,
        total: searchMatches.length
      };
    }

    function getActiveSearchMatch() {
      if (searchIndex < 0 || searchIndex >= searchMatches.length) return "";
      return searchMatches[searchIndex];
    }

    function buildElements(config) {
      var elements = [];
      var dataFlowPairs = {};

      currentData.edges.forEach(function (edge) {
        dataFlowPairs[edge.source_id + "->" + edge.target_id] = true;
      });

      currentData.nodes.forEach(function (node) {
        var status = node.status || "pending";
        var conceptType = node.concept_type || "custom";
        var colors = config.getNodeColors(conceptType);
        var shape = config.statusShapes[status] || "ellipse";
        var childCount = (node.children && node.children.length) || 0;
        var visible = isNodeVisible(node.node_id);
        var label = isDecomposed(node.node_id)
          ? node.name + " [" + childrenOf[node.node_id].length + "]"
          : node.name;
        var size = Math.min(
          120,
          Math.max(64, 52 + Math.ceil(String(label || "").length / 10) * 10 + childCount * 4)
        );

        elements.push({
          group: "nodes",
          data: {
            id: node.node_id,
            label: label,
            _nodeData: node,
            bgColor: colors.bg,
            borderColor: colors.border,
            textColor: colors.text,
            shape: shape,
            size: size,
            labelWidth: Math.max(52, size - 14) + "px"
          },
          classes: visible ? "" : "collapsed-hidden"
        });

        if (node.parent_id) {
          var pairKey = node.parent_id + "->" + node.node_id;
          var reversePairKey = node.node_id + "->" + node.parent_id;
          if (!dataFlowPairs[pairKey] && !dataFlowPairs[reversePairKey]) {
            elements.push({
              group: "edges",
              data: {
                id: "hier_" + node.parent_id + "_" + node.node_id,
                source: node.parent_id,
                target: node.node_id,
                edgeType: "hierarchy"
              },
              classes: visible && isNodeVisible(node.parent_id) ? "" : "collapsed-hidden"
            });
          }
        }
      });

      currentData.edges.forEach(function (edge, i) {
        var classes = edge.requires_glue ? "glue-edge" : "";
        if (!isNodeVisible(edge.source_id) || !isNodeVisible(edge.target_id)) {
          classes = classes ? classes + " collapsed-hidden" : "collapsed-hidden";
        }
        elements.push({
          group: "edges",
          data: {
            id: "df_" + i + "_" + edge.source_id + "_" + edge.target_id,
            source: edge.source_id,
            target: edge.target_id,
            label: edge.output_name + " \u2192 " + edge.input_name,
            edgeType: "dataflow",
            requiresGlue: !!edge.requires_glue,
            sourceType: edge.source_type || "",
            targetType: edge.target_type || "",
            outputName: edge.output_name || "",
            inputName: edge.input_name || ""
          },
          classes: classes
        });
      });

      return elements;
    }

    return {
      setCurrentData: setCurrentData,
      getCurrentData: function () { return currentData; },
      getNodeById: function (nodeId) { return nodeById[nodeId]; },
      isDecomposed: isDecomposed,
      isExpanded: isExpanded,
      computeVisibleNodeIds: computeVisibleNodeIds,
      renderBreadcrumb: renderBreadcrumb,
      collapseAll: collapseAll,
      collapseTo: collapseTo,
      toggleExpand: toggleExpand,
      parseSearchQuery: parseSearchQuery,
      nodeMatchesQuery: nodeMatchesQuery,
      resetSearch: resetSearch,
      updateSearchMatches: updateSearchMatches,
      hasSearchMatches: hasSearchMatches,
      advanceSearchMatch: advanceSearchMatch,
      getActiveSearchMatch: getActiveSearchMatch,
      buildElements: buildElements
    };
  };
})(window);
