(function (global) {
  "use strict";

  global.createVisualizerGraphStyles = function createVisualizerGraphStyles(options) {
    function getCytoscapeStyle() {
      return [
        {
          selector: "node",
          style: {
            "label": "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": "data(labelWidth)",
            "font-size": "10px",
            "color": "data(textColor)",
            "background-color": "data(bgColor)",
            "border-color": "data(borderColor)",
            "border-width": 2,
            "shape": "data(shape)",
            "width": "data(size)",
            "height": "data(size)",
            "transition-property": "opacity, border-width",
            "transition-duration": "0.15s"
          }
        },
        {
          selector: "node:selected",
          style: {
            "border-width": 4,
            "overlay-opacity": 0.15,
            "overlay-color": "#42a5f5"
          }
        },
        {
          selector: ".dimmed",
          style: {
            "opacity": 0.15
          }
        },
        {
          selector: "edge.upstream-highlight",
          style: {
            "line-color": "#1976d2",
            "target-arrow-color": "#1976d2",
            "width": 3,
            "opacity": 1,
            "z-index": 10
          }
        },
        {
          selector: "node.upstream-highlight",
          style: {
            "border-width": 3,
            "opacity": 1,
            "z-index": 10
          }
        },
        {
          selector: "edge.downstream-highlight",
          style: {
            "line-color": "#e65100",
            "target-arrow-color": "#e65100",
            "width": 3,
            "opacity": 1,
            "z-index": 10
          }
        },
        {
          selector: "node.downstream-highlight",
          style: {
            "border-width": 3,
            "opacity": 1,
            "z-index": 10
          }
        },
        {
          selector: ".hover-focus",
          style: {
            "border-width": 4,
            "opacity": 1,
            "z-index": 20
          }
        },
        {
          selector: "edge.edge-tooltip",
          style: {
            "width": 3,
            "z-index": 15
          }
        },
        {
          selector: ".search-dimmed",
          style: {
            "opacity": 0.12
          }
        },
        {
          selector: ".search-match",
          style: {
            "border-width": 4,
            "border-color": "#ff6f00",
            "opacity": 1,
            "z-index": 10
          }
        },
        {
          selector: ".collapsed-hidden",
          style: {
            "display": "none"
          }
        },
        {
          selector: "edge[edgeType='hierarchy']",
          style: {
            "width": 1.5,
            "line-style": "dotted",
            "line-color": "#b0bec5",
            "target-arrow-color": "#b0bec5",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "arrow-scale": 0.8
          }
        },
        {
          selector: "edge[edgeType='dataflow']",
          style: {
            "width": 2,
            "line-style": "dashed",
            "line-dash-pattern": [6, 3],
            "line-dash-offset": 0,
            "line-color": "#546e7a",
            "target-arrow-color": "#546e7a",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "label": "data(label)",
            "font-size": "9px",
            "text-rotation": "autorotate",
            "color": "#37474f",
            "text-background-color": "#fff",
            "text-background-opacity": 0.8,
            "text-background-padding": "2px",
            "arrow-scale": 1
          }
        },
        {
          selector: ".glue-edge",
          style: {
            "line-style": "dashed",
            "line-color": "#ffa726",
            "target-arrow-color": "#ffa726"
          }
        },
        {
          selector: "edge.mismatch-edge",
          style: {
            "line-color": "#c62828",
            "target-arrow-color": "#c62828",
            "width": 4,
            "line-style": "solid",
            "color": "#c62828",
            "font-weight": "bold"
          }
        },
        {
          selector: "node.exec-success",
          style: {
            "border-color": "#2e7d32",
            "border-width": 4
          }
        },
        {
          selector: "node.exec-cached",
          style: {
            "border-color": "#4caf50",
            "border-style": "double",
            "border-width": 6
          }
        },
        {
          selector: "node.exec-failed",
          style: {
            "border-color": "#c62828",
            "border-width": 4
          }
        },
        {
          selector: "node.has-outputs",
          style: {
            "border-color": "#1565c0",
            "border-width": 4
          }
        }
      ];
    }

    function getLayoutConfig(name) {
      if (name === "dagre") {
        return {
          name: "dagre",
          rankDir: "TB",
          nodeSep: 50,
          rankSep: 80,
          animate: true,
          animationDuration: 300
        };
      }
      if (name === "cose") {
        return {
          name: "cose",
          nodeRepulsion: function () { return 8000; },
          animate: true,
          animationDuration: 500
        };
      }
      if (name === "breadthfirst") {
        return {
          name: "breadthfirst",
          directed: true,
          spacingFactor: 1.5,
          animate: true,
          animationDuration: 300
        };
      }
      return { name: "dagre", rankDir: "TB", nodeSep: 50, rankSep: 80 };
    }

    function buildLegend() {
      var container = document.getElementById("legend-content");
      if (!container) return;
      container.innerHTML = "";

      var colorTitle = document.createElement("div");
      colorTitle.className = "legend-group-title";
      colorTitle.textContent = "Color = Concept Type Family";
      container.appendChild(colorTitle);

      Object.keys(options.familyColors).forEach(function (key) {
        var row = document.createElement("div");
        row.className = "legend-row";
        var swatch = document.createElement("span");
        swatch.className = "legend-swatch";
        swatch.style.background = options.familyColors[key].bg;
        swatch.style.borderColor = options.familyColors[key].border;
        var label = document.createElement("span");
        label.className = "legend-label";
        label.textContent = options.familyLabels[key] || key;
        row.appendChild(swatch);
        row.appendChild(label);
        container.appendChild(row);
      });

      var shapeTitle = document.createElement("div");
      shapeTitle.className = "legend-group-title";
      shapeTitle.style.marginTop = "12px";
      shapeTitle.textContent = "Shape = Status";
      container.appendChild(shapeTitle);

      {
        var shapeLabels = {
          atomic: "Atomic (circle)",
          decomposed: "Decomposed (rounded rect)",
          external: "External (diamond)"
        };
        Object.keys(shapeLabels).forEach(function (key) {
          var row = document.createElement("div");
          row.className = "legend-row";
          var icon = document.createElement("span");
          icon.className = "legend-shape legend-shape-" + key;
          var label = document.createElement("span");
          label.className = "legend-label";
          label.textContent = shapeLabels[key];
          row.appendChild(icon);
          row.appendChild(label);
          container.appendChild(row);
        });
      }
    }

    return {
      getCytoscapeStyle: getCytoscapeStyle,
      getLayoutConfig: getLayoutConfig,
      buildLegend: buildLegend
    };
  };
})(window);
