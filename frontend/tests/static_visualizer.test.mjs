import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const staticDir = path.join(repoRoot, "sciona", "static");
const localVisualizerScripts = [
  "graph_styles.js",
  "graph_state.js",
  "graph_core.js",
  "detail_panel.js",
  "browser_panel.js",
  "compare_mode.js",
  "isomorphism_panel.js",
  "runner_panel.js",
  "evolution_workspace.js",
  "evolution_dag.js",
  "guided_tour.js",
  "app.js",
];

class FakeClassList {
  constructor(owner) {
    this.owner = owner;
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => {
      if (name) this.values.add(name);
    });
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  toggle(name, force) {
    if (force === true) {
      this.values.add(name);
      return true;
    }
    if (force === false) {
      this.values.delete(name);
      return false;
    }
    if (this.values.has(name)) {
      this.values.delete(name);
      return false;
    }
    this.values.add(name);
    return true;
  }

  contains(name) {
    return this.values.has(name);
  }

  setFromString(raw) {
    this.values = new Set(String(raw || "").split(/\s+/).filter(Boolean));
  }

  toString() {
    return Array.from(this.values).join(" ");
  }
}

function parseSelector(selector) {
  const parsed = {
    tag: null,
    id: null,
    classes: [],
    attrs: {},
    checked: false,
  };
  let rest = selector.trim();
  if (!rest) return parsed;

  if (rest.includes(":checked")) {
    parsed.checked = true;
    rest = rest.replace(":checked", "");
  }

  const attrPattern = /\[([^=\]]+)="([^"]*)"\]/g;
  rest = rest.replace(attrPattern, (_, key, value) => {
    parsed.attrs[key] = value;
    return "";
  });

  const idMatch = rest.match(/#([\w-]+)/);
  if (idMatch) {
    parsed.id = idMatch[1];
    rest = rest.replace(idMatch[0], "");
  }

  const classMatches = rest.match(/\.[\w-]+/g) || [];
  parsed.classes = classMatches.map((item) => item.slice(1));
  rest = rest.replace(/\.[\w-]+/g, "").trim();
  if (rest) parsed.tag = rest.toLowerCase();
  return parsed;
}

function matchesSelector(element, selector) {
  const parsed = parseSelector(selector);
  if (parsed.tag && element.tagName.toLowerCase() !== parsed.tag) return false;
  if (parsed.id && element.id !== parsed.id) return false;
  if (parsed.checked && !element.checked) return false;
  if (parsed.classes.some((name) => !element.classList.contains(name))) return false;
  return Object.entries(parsed.attrs).every(([key, value]) => {
    const attrValue = element.getAttribute(key);
    return attrValue === value;
  });
}

function querySelectorAllFrom(root, selector) {
  const matches = [];

  function visit(node) {
    if (matchesSelector(node, selector)) matches.push(node);
    node.children.forEach(visit);
  }

  root.children.forEach(visit);
  return matches;
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.id = "";
    this.name = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.textContent = "";
    this.children = [];
    this.parentNode = null;
    this.style = {};
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.focused = false;
    this._innerHTML = "";
    this.classList = new FakeClassList(this);
  }

  get className() {
    return this.classList.toString();
  }

  set className(value) {
    this.classList.setFromString(value);
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this._innerHTML = String(value);
    this.children = [];
  }

  get options() {
    return this.children;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  addEventListener(type, handler) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(handler);
  }

  dispatchEvent(type, event = {}) {
    (this.listeners[type] || []).forEach((handler) => handler(event));
  }

  click() {
    this.dispatchEvent("click", { target: this });
  }

  focus() {
    this.focused = true;
  }

  getContext() {
    return {};
  }

  setAttribute(name, value) {
    const normalized = String(value);
    this.attributes[name] = normalized;
    if (name === "id") this.id = normalized;
    if (name === "class") this.className = normalized;
    if (name === "name") this.name = normalized;
    if (name === "value") this.value = normalized;
    if (name.startsWith("data-")) this.dataset[name.slice(5)] = normalized;
  }

  getAttribute(name) {
    if (name === "id") return this.id || null;
    if (name === "class") return this.className || null;
    if (name === "name") return this.name || null;
    if (name === "value") return this.value || null;
    return this.attributes[name] || null;
  }

  querySelector(selector) {
    return querySelectorAllFrom(this, selector)[0] || null;
  }

  querySelectorAll(selector) {
    return querySelectorAllFrom(this, selector);
  }
}

class FakeDocument {
  constructor() {
    this.listeners = {};
    this.documentElement = new FakeElement("html");
    this.body = new FakeElement("body");
    this.documentElement.appendChild(this.body);
  }

  createElement(tagName) {
    return new FakeElement(tagName);
  }

  getElementById(id) {
    return this.querySelector(`#${id}`);
  }

  querySelector(selector) {
    return querySelectorAllFrom(this.documentElement, selector)[0] || null;
  }

  querySelectorAll(selector) {
    return querySelectorAllFrom(this.documentElement, selector);
  }

  addEventListener(type, handler) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(handler);
  }

  dispatchEvent(type, event = {}) {
    (this.listeners[type] || []).forEach((handler) => handler(event));
  }
}

function addElement(document, tagName, id, parent, options = {}) {
  const element = document.createElement(tagName);
  if (id) element.id = id;
  if (options.classes) options.classes.forEach((name) => element.classList.add(name));
  if (options.textContent) element.textContent = options.textContent;
  if (options.value != null) element.value = String(options.value);
  if (options.checked != null) element.checked = Boolean(options.checked);
  if (options.attributes) {
    Object.entries(options.attributes).forEach(([key, value]) => element.setAttribute(key, value));
  }
  parent.appendChild(element);
  return element;
}

function createVisualizerDocument() {
  const document = new FakeDocument();
  const body = document.body;

  [
    "meta-goal",
    "meta-paradigm",
    "meta-nodes",
    "meta-edges",
    "meta-thread",
    "status-text",
    "cy-container",
    "drop-zone",
    "graph-search",
    "legend-panel",
    "breadcrumb-bar",
    "breadcrumb-content",
    "btn-browse",
    "cdg-browser",
    "btn-browser-close",
    "browser-search",
    "browser-list",
    "btn-compare",
    "compare-bar",
    "compare-container",
    "compare-left",
    "compare-right",
    "compare-left-select",
    "compare-right-select",
    "compare-score",
    "compare-diff-summary",
    "compare-common-count",
    "compare-added-count",
    "compare-removed-count",
    "compare-changed-count",
    "compare-hover-row",
    "compare-node-name",
    "btn-compare-close",
    "compare-diff-modal",
    "compare-diff-state",
    "compare-diff-title",
    "compare-diff-subtitle",
    "compare-diff-fields",
    "compare-diff-close",
    "compare-diff-tabs",
    "compare-tab-details",
    "compare-tab-inputs",
    "compare-tab-outputs",
    "compare-input-visuals",
    "compare-output-visuals",
    "detail-panel",
    "detail-name",
    "btn-find-iso",
    "detail-status",
    "detail-concept-type",
    "detail-description",
    "detail-type-sig",
    "detail-primitive",
    "detail-depth",
    "detail-children",
    "detail-parent",
    "detail-rationale",
    "detail-critic",
    "lineage-upstream-list",
    "lineage-downstream-list",
    "iso-modal",
    "iso-min-sim",
    "iso-sim-value",
    "iso-max-results",
    "iso-cancel",
    "iso-search",
    "iso-loading",
    "iso-empty",
    "iso-results",
    "file-input",
    "btn-open",
    "btn-dashboard",
    "legend-content",
    "active-run-id",
    "btn-run-cdg",
    "btn-new-inputs",
    "btn-history",
    "btn-history-close",
    "run-history-browser",
    "history-list",
    "run-modal",
    "run-modal-inputs",
    "run-modal-error",
    "run-modal-cancel",
    "run-modal-execute",
    "btn-run-node",
    "evolution-workspace",
    "evolution-tabs",
    "evolution-operation",
    "evolution-loss",
    "evolution-delta",
    "evolution-diff",
    "evolution-guidance",
    "evolution-guidance-status",
    "btn-evolution-previous",
    "btn-evolution-next",
    "btn-evolution-refine",
    "btn-evolution-reject",
    "btn-evolution-dag",
    "evolution-dag-modal",
    "evolution-dag-canvas",
    "evolution-dag-selection",
    "evolution-dag-close",
    "evolution-dag-select",
    "execution-empty",
    "execution-content",
    "exec-inputs-list",
    "exec-outputs-list",
    "btn-tutorials",
    "btn-guided-tour",
    "tutorials-modal",
    "btn-tutorials-close",
    "quick-fixes-list",
    "repair-log-content",
    "repair-diff-content",
    "mismatch-diagnostic-section",
    "detail-mismatch-error",
    "quick-fixes-section",
    "btn-load-tutorial-a",
    "btn-load-tutorial-b",
    "btn-load-tutorial-c",
    "guided-tour",
    "guided-tour-spotlight",
    "guided-tour-dialog",
    "guided-tour-progress",
    "guided-tour-title",
    "guided-tour-body",
    "guided-tour-previous",
    "guided-tour-next",
    "guided-tour-close",
  ].forEach((id) => addElement(document, "div", id, body));

  const graphViewControls = addElement(document, "div", "graph-view-controls", body);
  const graphViewMenu = addElement(document, "div", "graph-view-menu", graphViewControls, {
    classes: ["hidden"],
  });
  ["dagre", "cose", "breadthfirst"].forEach((value, index) => {
    addElement(document, "input", "", graphViewMenu, {
      checked: index === 0,
      attributes: { name: "graph-layout", value },
    });
  });
  addElement(document, "button", "btn-fit", graphViewMenu);
  addElement(document, "button", "btn-reset", graphViewMenu);
  addElement(document, "button", "btn-legend", graphViewMenu, {
    attributes: { "aria-checked": "false" },
  });
  addElement(document, "button", "btn-graph-menu", graphViewControls, {
    attributes: { "aria-expanded": "false" },
  });

  const graphSearch = document.getElementById("graph-search");
  graphSearch.tagName = "INPUT";

  const browserSearch = document.getElementById("browser-search");
  browserSearch.tagName = "INPUT";

  const compareLeftSelect = document.getElementById("compare-left-select");
  compareLeftSelect.tagName = "SELECT";
  const compareRightSelect = document.getElementById("compare-right-select");
  compareRightSelect.tagName = "SELECT";
  document.getElementById("compare-diff-summary").classList.add("hidden");
  document.getElementById("compare-hover-row").classList.add("hidden");
  const compareDiffModal = document.getElementById("compare-diff-modal");
  compareDiffModal.classList.add("hidden");
  addElement(document, "div", "", compareDiffModal, { classes: ["iso-modal-backdrop"] });
  const compareDiffTabs = document.getElementById("compare-diff-tabs");
  ["details", "inputs", "outputs"].forEach((name, index) => {
    addElement(document, "button", "", compareDiffTabs, {
      classes: index === 0 ? ["active"] : [],
      attributes: { "data-compare-tab": name, "aria-selected": index === 0 ? "true" : "false" },
    });
    const panel = document.getElementById(`compare-tab-${name}`);
    if (index === 0) panel.classList.add("active");
  });

  const fileInput = document.getElementById("file-input");
  fileInput.tagName = "INPUT";
  fileInput.files = [];

  const isoMinSim = document.getElementById("iso-min-sim");
  isoMinSim.tagName = "INPUT";
  isoMinSim.value = "0.3";

  const isoMaxResults = document.getElementById("iso-max-results");
  isoMaxResults.tagName = "INPUT";
  isoMaxResults.value = "20";

  const detailTabs = addElement(document, "div", "detail-tabs", body);
  ["summary", "ports", "lineage", "isomorphisms", "execution", "repair"].forEach((tab, index) => {
    addElement(document, "button", "", detailTabs, {
      classes: index === 0 ? ["detail-tab", "active"] : ["detail-tab"],
      attributes: { "data-tab": tab },
    });
    addElement(document, "div", `tab-${tab}`, body, {
      classes: index === 0 ? ["tab-content", "active"] : ["tab-content"],
    });
  });

  addElement(document, "div", "", body, {
    classes: ["lineage-hint"],
    textContent: "Select a node to see its data-flow neighbors",
  });

  const detailInputs = addElement(document, "table", "detail-inputs", body);
  addElement(document, "tbody", "", detailInputs);
  const detailOutputs = addElement(document, "table", "detail-outputs", body);
  addElement(document, "tbody", "", detailOutputs);

  const isoModal = document.getElementById("iso-modal");
  addElement(document, "div", "", isoModal, { classes: ["iso-modal-backdrop"] });

  addElement(document, "input", "iso-layer-1", body, { checked: true });
  addElement(document, "input", "iso-layer-2", body, { checked: true });
  addElement(document, "input", "iso-layer-3", body, { checked: true });
  addElement(document, "input", "", body, {
    checked: true,
    attributes: { name: "iso-scope", value: "this" },
  });
  addElement(document, "input", "", body, {
    checked: false,
    attributes: { name: "iso-scope", value: "parent" },
  });

  return document;
}

function createCytoscapeStub() {
  const collection = {
    length: 0,
    forEach() {},
    addClass() { return collection; },
    removeClass() { return collection; },
    not() { return collection; },
    union() { return collection; },
    edges() { return collection; },
    nodes() { return collection; },
    edgesWith() { return collection; },
    layout() { return { run() {} }; },
  };

  return {
    destroy() {},
    nodes() { return collection; },
    edges() { return collection; },
    elements() { return collection; },
    on() {},
    getElementById() { return { length: 0 }; },
    animate() {},
    zoom() { return 1; },
    layout() { return { run() {} }; },
    fit() {},
    resize() {},
  };
}

function createBrowserContext(document, fetchImpl) {
  const fetchCalls = [];
  const animationFrames = [];
  const timers = new Map();
  let timerId = 0;

  function fetch(url, options) {
    fetchCalls.push({ url, options });
    return fetchImpl(url, options);
  }

  function schedule(callback) {
    timerId += 1;
    timers.set(timerId, callback);
    return timerId;
  }

  const context = {
    console,
    document,
    fetch,
    URLSearchParams,
    requestAnimationFrame(callback) {
      animationFrames.push(callback);
      return animationFrames.length;
    },
    cancelAnimationFrame() {},
    setTimeout(callback) {
      return schedule(callback);
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    setInterval(callback) {
      return schedule(callback);
    },
    clearInterval(id) {
      timers.delete(id);
    },
    FileReader: class FileReader {
      readAsText() {
        throw new Error("FileReader is not implemented in the test harness");
      }
    },
    cytoscape() {
      return createCytoscapeStub();
    },
  };

  context.window = {
    ...context,
    document,
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener() {},
    open() {},
  };
  context.window.window = context.window;
  context.self = context.window;
  context.globalThis = context;

  return { context, fetchCalls, animationFrames };
}

function loadScript(context, fileName) {
  const source = fs.readFileSync(path.join(staticDir, fileName), "utf8");
  vm.runInNewContext(source, context, { filename: fileName });
}

function loadScripts(context, files) {
  files.forEach((file) => loadScript(context, file));
}

function sampleGraphData() {
  return {
    nodes: [
      {
        node_id: "root",
        name: "Root Task",
        description: "Top level",
        concept_type: "divide_and_conquer",
        status: "decomposed",
        children: ["child"],
        depth: 0,
      },
      {
        node_id: "child",
        name: "Child Step",
        description: "Sort child data",
        concept_type: "sorting",
        status: "atomic",
        parent_id: "root",
        children: [],
        depth: 1,
      },
    ],
    edges: [
      {
        source_id: "root",
        target_id: "child",
        output_name: "out",
        input_name: "in",
        source_type: "list[int]",
        target_type: "list[int]",
        requires_glue: false,
      },
    ],
    metadata: {
      goal: "Sort values",
      paradigm: "divide_and_conquer",
      repo: "ageo/demo",
    },
  };
}

async function flushAsync() {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

test("index.html keeps the visualizer script order explicit", () => {
  const html = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
  const scriptSrcs = Array.from(html.matchAll(/<script src="([^"]+)"><\/script>/g)).map((match) => match[1]);
  const localScripts = scriptSrcs.filter((src) => !src.startsWith("https://"));
  assert.deepEqual(localScripts, localVisualizerScripts);
});

test("toolbar uses accessible icon commands and moves graph controls into a floating menu", () => {
  const html = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
  [
    ["btn-browse", "panel-left-open"],
    ["btn-tutorials", "book-open"],
    ["btn-guided-tour", "map"],
    ["btn-open", "folder-open"],
    ["btn-run-cdg", "play"],
    ["btn-new-inputs", "list-restart"],
    ["btn-history", "history"],
  ].forEach(([id, icon]) => {
    assert.match(html, new RegExp(`id="${id}"[^>]+aria-label="[^"]+"[^>]*>\\s*<i data-lucide="${icon}"`));
  });
  assert.match(html, /id="btn-graph-menu"[^>]+aria-haspopup="menu"/);
  assert.match(html, /name="graph-layout" value="dagre" checked/);
  assert.match(html, /id="graph-view-menu" class="hidden" role="menu"/);
  assert.doesNotMatch(html, /id="layout-select"/);
});

test("floating graph menu manages disabled, legend, and keyboard states", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: false, json: async () => ({}) }));
  loadScripts(context, ["graph_styles.js", "graph_state.js", "graph_core.js"]);

  const controls = context.window.initVisualizerGraph({
    familyColors: {},
    familyLabels: {},
    getNodeColors: () => ({ bg: "#fff", border: "#000", text: "#000" }),
    statusShapes: {},
    isApiAvailable: () => false,
  });
  const menu = document.getElementById("graph-view-menu");
  const menuButton = document.getElementById("btn-graph-menu");
  const fitButton = document.getElementById("btn-fit");
  const legendButton = document.getElementById("btn-legend");

  assert.equal(fitButton.disabled, true);
  assert.equal(document.querySelector('input[name="graph-layout"]').disabled, true);
  menuButton.click();
  assert.equal(menu.classList.contains("hidden"), false);
  assert.equal(menuButton.getAttribute("aria-expanded"), "true");

  controls.validateAndLoad(sampleGraphData());
  assert.equal(fitButton.disabled, false);
  legendButton.click();
  assert.equal(document.getElementById("legend-panel").classList.contains("visible"), true);
  assert.equal(legendButton.getAttribute("aria-checked"), "true");
  assert.equal(menu.classList.contains("hidden"), true);

  menuButton.click();
  document.dispatchEvent("keydown", { key: "Escape", preventDefault() {} });
  assert.equal(menu.classList.contains("hidden"), true);
  assert.equal(menuButton.getAttribute("aria-expanded"), "false");
  assert.equal(menuButton.focused, true);

  menuButton.click();
  document.dispatchEvent("click", { target: document.body });
  assert.equal(menu.classList.contains("hidden"), true);
  assert.equal(menuButton.getAttribute("aria-expanded"), "false");
});

test("graph_state supports structured search and element generation", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: false, json: async () => ({}) }));
  loadScript(context, "graph_state.js");

  const state = context.window.createVisualizerGraphState({
    breadcrumbBar: document.getElementById("breadcrumb-bar"),
    breadcrumbContent: document.getElementById("breadcrumb-content"),
  });
  state.setCurrentData(sampleGraphData());

  const query = state.parseSearchQuery("type:sorting status:atomic child");
  assert.equal(query.structured.type, "sorting");
  assert.equal(query.structured.status, "atomic");
  assert.equal(query.freeText, "child");
  assert.equal(state.nodeMatchesQuery(sampleGraphData().nodes[1], query), true);
  assert.equal(state.nodeMatchesQuery(sampleGraphData().nodes[0], query), false);

  let rebuilt = 0;
  state.toggleExpand("root", () => {
    rebuilt += 1;
  });
  assert.equal(rebuilt, 1);
  assert.equal(state.computeVisibleNodeIds().child, true);

  const elements = state.buildElements({
    getNodeColors() {
      return { bg: "#fff", border: "#000", text: "#111" };
    },
    statusShapes: {
      atomic: "ellipse",
      decomposed: "round-rectangle",
    },
  });

  const nodeElements = elements.filter((entry) => entry.group === "nodes");
  const edgeElements = elements.filter((entry) => entry.group === "edges");
  assert.equal(nodeElements.length, 2);
  assert.equal(edgeElements.length, 1);

  state.renderBreadcrumb(() => {}, () => {});
  const breadcrumb = document.getElementById("breadcrumb-content");
  assert.equal(breadcrumb.children.length > 0, true);
});

test("graph_styles returns layouts and renders a legend", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: false, json: async () => ({}) }));
  loadScript(context, "graph_styles.js");

  const styles = context.window.createVisualizerGraphStyles({
    familyColors: {
      math: { bg: "#bbdefb", border: "#1976d2", text: "#0d47a1" },
      other: { bg: "#e0e0e0", border: "#757575", text: "#424242" },
    },
    familyLabels: {
      math: "Math / Algo",
      other: "Other",
    },
  });

  const cytoscapeStyles = styles.getCytoscapeStyle();
  assert.equal(cytoscapeStyles.some((entry) => entry.selector === "node"), true);
  assert.equal(cytoscapeStyles.some((entry) => entry.selector === "edge[edgeType='dataflow']"), true);
  assert.equal(styles.getLayoutConfig("cose").name, "cose");

  styles.buildLegend();
  const legendContent = document.getElementById("legend-content");
  assert.equal(legendContent.children.length > 0, true);
  assert.equal(legendContent.children[0].textContent, "Color = Concept Type Family");
});

test("compare mode keeps open evolution versions when the catalog browser is unavailable", async () => {
  const document = createVisualizerDocument();
  const { context, fetchCalls } = createBrowserContext(document, () => Promise.resolve({
    ok: false,
    status: 503,
    json: async () => ({ detail: "Memgraph unavailable" }),
  }));
  loadScript(context, "compare_mode.js");
  const graph = sampleGraphData();
  const refinedGraph = JSON.parse(JSON.stringify(graph));
  refinedGraph.nodes[1].matched_primitive = "provider.refined";
  const compare = context.window.initVisualizerCompare({
    cyContainer: document.getElementById("cy-container"),
    detailPanel: document.getElementById("detail-panel"),
    getNodeColors() { return { bg: "#fff", border: "#777", text: "#111" }; },
    getCytoscapeStyle() { return []; },
    statusShapes: {},
    getLocalComparands() {
      return [
        { key: "version:initial", label: "Initial match (2 nodes)", graph },
        { key: "version:refined", label: "Refinement (2 nodes)", graph: refinedGraph },
      ];
    },
  });

  document.getElementById("btn-compare").click();
  await flushAsync();

  const left = document.getElementById("compare-left-select");
  const right = document.getElementById("compare-right-select");
  assert.equal(left.options.length, 3);
  assert.equal(right.options.length, 3);
  assert.equal(left.value, "version:initial");
  assert.equal(right.value, "version:refined");
  assert.equal(document.getElementById("compare-hover-row").classList.contains("hidden"), false);
  assert.equal(document.getElementById("compare-node-name").textContent,
    "Hover a node to see its comparison summary.");
  assert.equal(left.options[1].textContent, "Initial match (2 nodes)");
  assert.equal(document.getElementById("compare-score").textContent, "Type similarity 0.000");

  left.value = "version:initial";
  left.dispatchEvent("change");
  right.value = "version:refined";
  right.dispatchEvent("change");
  await flushAsync();

  assert.equal(document.getElementById("compare-score").textContent, "Type similarity 0.000");
  assert.equal(document.getElementById("compare-common-count").textContent, "1");
  assert.equal(document.getElementById("compare-changed-count").textContent, "1");
  compare.openNodeDiff("child");
  assert.equal(document.getElementById("compare-diff-modal").classList.contains("hidden"), false);
  assert.equal(document.getElementById("compare-diff-state").textContent, "Changed");
  assert.equal(document.getElementById("compare-diff-subtitle").textContent, "Changed fields: Matched Primitive");
  assert.equal(document.getElementById("compare-diff-fields").children.length, 10);
  document.getElementById("compare-diff-tabs").querySelector('[data-compare-tab="inputs"]').click();
  await flushAsync();
  assert.equal(document.getElementById("compare-tab-inputs").classList.contains("active"), true);
  assert.equal(document.getElementById("compare-input-visuals").children[0].textContent,
    "Execute both graph versions to compare persisted inputs.");
  document.getElementById("compare-diff-close").click();
  assert.equal(document.getElementById("compare-diff-modal").classList.contains("hidden"), true);
  assert.deepEqual(fetchCalls.map((call) => call.url), ["/api/cdgs"]);
});

test("compare mode classifies structural node changes and shortens only long labels", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: false }));
  loadScript(context, "compare_mode.js");
  const left = sampleGraphData();
  const right = JSON.parse(JSON.stringify(left));
  right.nodes[1].matched_primitive = "provider.changed";
  right.nodes.push({
    node_id: "added",
    name: "Condition the sampled input waveform before detection",
    concept_type: "filtering",
    status: "atomic",
  });
  left.nodes.push({ node_id: "removed", name: "Old stage", concept_type: "custom", status: "atomic" });

  const diff = context.window.classifyVisualizerGraphDiff(left, right);
  const childDetail = context.window.describeVisualizerNodeDiff(left, right, "child");
  assert.deepEqual(Array.from(diff.common), ["root"]);
  assert.deepEqual(Array.from(diff.changed), ["child"]);
  assert.deepEqual(Array.from(diff.added), ["added"]);
  assert.deepEqual(Array.from(diff.removed), ["removed"]);
  assert.deepEqual(Array.from(childDetail.changedFields), ["matched_primitive"]);
  assert.equal(context.window.conciseVisualizerNodeLabel("Detect Recurring Events"), "Detect Recurring Events");
  assert.equal(context.window.conciseVisualizerNodeLabel(right.nodes[2].name), "Condition the ... detection");
});

test("compare modal overlays persisted input series from both execution versions", async () => {
  const document = createVisualizerDocument();
  const chartConfigs = [];
  const { context, fetchCalls } = createBrowserContext(document, (url) => {
    if (url === "/api/cdgs") return Promise.resolve({ ok: false, status: 503, json: async () => ({}) });
    if (url.endsWith("/values")) {
      return Promise.resolve({
        ok: true,
        json: async () => ({ inputs: { signal: { dtype: "float64", shape: [3] } }, outputs: {} }),
      });
    }
    if (url.includes("/values/in_signal/slice")) {
      const data = url.includes("run-left") ? [1, 2, 3] : [1, 2.5, 4];
      return Promise.resolve({ ok: true, json: async () => ({ type: "1d", data, shape: [3], dtype: "float64" }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
  });
  context.window.Chart = function Chart(_context, config) {
    chartConfigs.push(config);
    this.destroy = function () {};
  };
  loadScript(context, "compare_mode.js");
  const graph = sampleGraphData();
  const compare = context.window.initVisualizerCompare({
    cyContainer: document.getElementById("cy-container"),
    detailPanel: document.getElementById("detail-panel"),
    getNodeColors() { return { bg: "#fff", border: "#777", text: "#111" }; },
    getCytoscapeStyle() { return []; },
    statusShapes: {},
    getLocalComparands() {
      return [
        { key: "version:left", label: "Before (2 nodes)", graph, runId: "run-left" },
        { key: "version:right", label: "After (2 nodes)", graph, runId: "run-right" },
      ];
    },
  });

  document.getElementById("btn-compare").click();
  await flushAsync();
  compare.openNodeDiff("child");
  document.getElementById("compare-diff-tabs").querySelector('[data-compare-tab="inputs"]').click();
  await flushAsync();
  await flushAsync();

  assert.equal(chartConfigs.length, 1);
  assert.deepEqual(Array.from(chartConfigs[0].data.datasets, (dataset) => dataset.label), ["Before", "After"]);
  assert.deepEqual(Array.from(chartConfigs[0].data.datasets[0].data), [1, 2, 3]);
  assert.deepEqual(Array.from(chartConfigs[0].data.datasets[1].data), [1, 2.5, 4]);
  assert.equal(fetchCalls.filter((call) => call.url.endsWith("/values")).length, 2);
  assert.equal(fetchCalls.filter((call) => call.url.includes("/values/in_signal/slice")).length, 2);
});

test("evolution workspace preserves versions and supports human rejection", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: true, json: async () => ({}) }));
  loadScript(context, "evolution_workspace.js");
  const loaded = [];
  const workspace = context.window.initEvolutionWorkspace({
    loadGraph(graph) { loaded.push(graph); },
  });
  const initial = sampleGraphData();
  workspace.start(initial, { label: "Initial match", loss: 4 });

  const refined = JSON.parse(JSON.stringify(initial));
  refined.nodes.push({
    node_id: "validate",
    name: "Validate",
    description: "",
    concept_type: "custom",
    status: "atomic",
    matched_primitive: "provider.validate",
    inputs: [],
    outputs: [],
    depth: 1,
  });
  workspace.recordTransition(refined, { operation: "insert_validation", label: "Refinement", loss: 2 });

  assert.equal(workspace.getTrace().versions.length, 2);
  assert.equal(workspace.getTrace().transitions[0].graph_diff.added_nodes[0].node_id, "validate");
  assert.equal(document.getElementById("evolution-delta").textContent, "Delta -2.000000");

  workspace.setVersionEvaluation("initial-match", { loss: 3.5, metrics: { mae: 3.5 } }, "run-initial");
  workspace.setVersionEvaluation("version-2", { loss: 1.25, metrics: { mae: 1.25 } }, "run-refined");
  assert.equal(workspace.getTrace().versions[1].run_id, "run-refined");
  assert.equal(workspace.getTrace().transitions[0].loss_delta, -2.25);
  assert.equal(document.getElementById("evolution-delta").textContent, "Delta -2.250000");

  document.getElementById("evolution-guidance").value = "This branch amplifies noise";
  document.getElementById("btn-evolution-reject").click();
  assert.equal(workspace.getTrace().versions.length, 2);
  assert.equal(workspace.getTrace().versions[1].status, "rejected");
  assert.equal(loaded.at(-1), initial);
});

test("guided refinement creates a child branch without deleting siblings", async () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: true, json: async () => ({}) }));
  loadScript(context, "evolution_workspace.js");
  const created = [];
  const graph = sampleGraphData();
  const workspace = context.window.initEvolutionWorkspace({
    loadGraph() {},
    onRefineRequest(version, note) {
      assert.equal(version.version_id, "initial-match");
      assert.equal(note, "Preserve ordering");
      return Promise.resolve({
        updated_cdg: JSON.parse(JSON.stringify(graph)),
        operation: "local_mutation",
        selection_reason: "deterministic candidate",
      });
    },
    onVersionCreated(version) { created.push(version); },
  });
  workspace.start(graph, { label: "Initial match", loss: 4 });
  document.getElementById("evolution-guidance").value = "Preserve ordering";
  document.getElementById("btn-evolution-refine").click();
  await flushAsync();

  assert.equal(workspace.getTrace().versions.length, 2);
  assert.equal(workspace.getTrace().versions[1].parent_version_id, "initial-match");
  assert.equal(workspace.getTrace().transitions[0].human_guidance, "Preserve ordering");
  assert.equal(created.length, 1);
});

test("evolution DAG elements preserve branch and rejection topology", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: true, json: async () => ({}) }));
  loadScript(context, "evolution_dag.js");
  const elements = context.window.createEvolutionDAGElements({
    versions: [
      { version_id: "root", label: "Root", loss: 2, status: "accepted" },
      { version_id: "left", label: "Left", loss: 1, status: "accepted" },
      { version_id: "right", label: "Right", loss: 3, status: "rejected" },
    ],
    transitions: [
      { transition_id: "root--left", source_version_id: "root", target_version_id: "left", operation: "expansion" },
      { transition_id: "root--right", source_version_id: "root", target_version_id: "right", operation: "mutation", status: "rejected" },
    ],
  });

  assert.equal(elements.filter((item) => !item.data.source).length, 3);
  assert.equal(elements.filter((item) => item.data.source).length, 2);
  assert.equal(elements.find((item) => item.data.id === "right").data.status, "rejected");
});

test("execution panel ignores stale overlapping refreshes", async () => {
  const document = createVisualizerDocument();
  const pendingValues = [];
  const { context } = createBrowserContext(document, (url) => {
    if (url.includes("/values/") && url.endsWith("/slice")) {
      return Promise.resolve({ ok: true, json: async () => ({ type: "scalar", data: 1 }) });
    }
    if (url.endsWith("/values")) {
      return new Promise((resolve) => pendingValues.push(resolve));
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
  loadScript(context, "detail_panel.js");
  const panel = context.window.initVisualizerDetailPanel({
    conceptFamily: { custom: "other" },
    familyColors: { other: { bg: "#fff", border: "#777", text: "#111" } },
    getCy() { return null; },
    getRunId() { return "run-refined"; },
    isApiAvailable() { return true; },
    getCurrentData() { return sampleGraphData(); },
    getNodeColors() { return { bg: "#fff", border: "#777", text: "#111" }; },
  });
  const node = sampleGraphData().nodes[0];

  panel.handleNodeSelected(node);
  panel.refreshExecutionTab();
  assert.equal(pendingValues.length, 2);

  const valuesResponse = {
    ok: true,
    json: async () => ({
      inputs: { signal: { type: "ndarray", shape: [4] } },
      outputs: { rate: { type: "ndarray", shape: [4] } },
    }),
  };
  pendingValues[1](valuesResponse);
  await flushAsync();
  pendingValues[0](valuesResponse);
  await flushAsync();

  assert.equal(document.getElementById("exec-inputs-list").children.length, 1);
  assert.equal(document.getElementById("exec-outputs-list").children.length, 1);
});

test("history selection restores the persisted run snapshot", async () => {
  const document = createVisualizerDocument();
  const graph = sampleGraphData();
  const snapshot = {
    run_id: "run-history-123",
    metadata: { status: "completed", version_id: "refined", loss: 0.125 },
    cdg: graph,
    trace: [{ node_id: "child", name: "Child Step", cached: false }],
    evaluation: { version_id: "refined", loss: 0.125, metrics: {} },
    replayable: true,
  };
  const { context, fetchCalls } = createBrowserContext(document, (url) => {
    if (url === "/api/cdg/runs?repo=ageo%2Fdemo") {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          runs: [{
            run_id: snapshot.run_id,
            repo: "ageo/demo",
            timestamp: 1000,
            status: "completed",
          }],
        }),
      });
    }
    if (url === `/api/cdg/runs/${snapshot.run_id}`) {
      return Promise.resolve({ ok: true, json: async () => snapshot });
    }
    if (url.endsWith("/existing")) {
      return Promise.resolve({ ok: true, json: async () => ({ nodes: ["child"] }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
  });
  loadScript(context, "runner_panel.js");
  let restored = null;
  const runner = context.window.initVisualizerRunner({
    getCy() { return createCytoscapeStub(); },
    getCurrentData() { return graph; },
    detailControls: {
      refreshExecutionTab() {},
      getSelectedNodeId() { return null; },
    },
    onHistoricalRunLoaded(value) { restored = value; },
  });

  runner.setRepo("ageo/demo");
  document.getElementById("btn-history").click();
  await flushAsync();
  assert.equal(document.getElementById("history-list").children.length, 1);

  document.getElementById("history-list").children[0].click();
  await flushAsync();

  assert.equal(restored, snapshot);
  assert.equal(runner.getActiveRunId(), snapshot.run_id);
  assert.equal(document.getElementById("active-run-id").textContent, snapshot.run_id);
  assert.equal(document.getElementById("run-history-browser").classList.contains("visible"), false);
  assert.equal(fetchCalls.some((call) => call.url === `/api/cdg/runs/${snapshot.run_id}`), true);
});

test("history groups version evaluations under one execution session", async () => {
  const document = createVisualizerDocument();
  const graph = sampleGraphData();
  const runs = [
    {
      run_id: "execution-123--refined",
      execution_id: "execution-123",
      version_id: "refined",
      repo: "ageo/demo",
      timestamp: 1001,
      status: "completed",
      loss: 0.1,
    },
    {
      run_id: "execution-123--expanded",
      execution_id: "execution-123",
      version_id: "expanded",
      repo: "ageo/demo",
      timestamp: 1000,
      status: "completed",
      loss: 0.2,
    },
  ];
  const { context } = createBrowserContext(document, (url) => {
    if (url === "/api/cdg/runs?repo=ageo%2Fdemo") {
      return Promise.resolve({ ok: true, json: async () => ({ runs }) });
    }
    const run = runs.find((candidate) => url === `/api/cdg/runs/${candidate.run_id}`);
    if (run) {
      return Promise.resolve({
        ok: true,
        json: async () => ({
          run_id: run.run_id,
          metadata: run,
          cdg: graph,
          trace: [],
          evaluation: { version_id: run.version_id, loss: run.loss },
          replayable: true,
        }),
      });
    }
    if (url.endsWith("/existing")) {
      return Promise.resolve({ ok: true, json: async () => ({ nodes: [] }) });
    }
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) });
  });
  loadScript(context, "runner_panel.js");
  let restored = null;
  const runner = context.window.initVisualizerRunner({
    getCy() { return createCytoscapeStub(); },
    getCurrentData() { return graph; },
    detailControls: {
      refreshExecutionTab() {},
      getSelectedNodeId() { return null; },
    },
    onHistoricalRunLoaded(value) { restored = value; },
  });

  runner.setRepo("ageo/demo");
  document.getElementById("btn-history").click();
  await flushAsync();

  const historyList = document.getElementById("history-list");
  assert.equal(historyList.children.length, 1);
  const session = historyList.children[0];
  const versionList = session.querySelector(".history-version-list");
  assert.equal(versionList.children.length, 2);
  assert.equal(versionList.classList.contains("hidden"), true);

  session.click();
  assert.equal(versionList.classList.contains("hidden"), false);
  versionList.children[0].click();
  await flushAsync();

  assert.equal(restored.run_id, "execution-123--refined");
  assert.equal(runner.getActiveRunId(), "execution-123--refined");
});

test("guided tour advances through workflow steps and finishes cleanly", () => {
  const document = createVisualizerDocument();
  const { context } = createBrowserContext(document, () => Promise.resolve({ ok: false }));
  loadScript(context, "guided_tour.js");

  const prepared = [];
  const tour = context.window.initGuidedTour({
    steps: [
      { id: "objective", target: "#metadata-bar", title: "Objective", description: "Read the goal." },
      { id: "graph", target: "#cy-container", title: "Graph", description: "Inspect the graph." },
    ],
    prepareStep(step) { prepared.push(step.id); },
  });

  tour.start();
  assert.equal(tour.getActiveIndex(), 0);
  assert.equal(document.getElementById("guided-tour-title").textContent, "Objective");
  assert.equal(document.getElementById("guided-tour").classList.contains("hidden"), false);

  document.getElementById("guided-tour-next").click();
  assert.equal(tour.getActiveIndex(), 1);
  assert.equal(document.getElementById("guided-tour-title").textContent, "Graph");
  assert.deepEqual(prepared, ["objective", "graph"]);

  document.getElementById("guided-tour-next").click();
  assert.equal(tour.getActiveIndex(), -1);
  assert.equal(document.getElementById("guided-tour").classList.contains("hidden"), true);
});

test("visualizer scripts bootstrap in a headless browser harness", async () => {
  const document = createVisualizerDocument();
  const { context, fetchCalls, animationFrames } = createBrowserContext(document, (url) => {
    if (url === "/api/cdgs") {
      return Promise.resolve({
        ok: true,
        json: async () => [],
      });
    }
    if (url === "default_cdg.json") {
      return Promise.resolve({
        ok: false,
        status: 404,
        json: async () => ({}),
      });
    }
    return Promise.resolve({
      ok: true,
      json: async () => ({}),
    });
  });

  loadScripts(context, localVisualizerScripts);
  await flushAsync();

  assert.equal(typeof context.window.initVisualizerGraph, "function");
  assert.equal(typeof context.window.initVisualizerBrowser, "function");
  assert.equal(typeof context.window.initVisualizerDetailPanel, "function");
  assert.equal(fetchCalls.some((call) => call.url === "/api/cdgs"), false);
  assert.equal(fetchCalls.some((call) => call.url === "default_cdg.json"), true);
  assert.equal(animationFrames.length > 0, true);
  assert.equal(document.getElementById("browser-list").innerHTML, "");
});
