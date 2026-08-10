import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const staticDir = path.join(repoRoot, "sciona", "static");
const files = [
  "app.js",
  "browser_panel.js",
  "compare_mode.js",
  "dashboard.js",
  "detail_panel.js",
  "graph_core.js",
  "graph_state.js",
  "graph_styles.js",
  "isomorphism_panel.js",
  "runner_panel.js",
  "evolution_workspace.js",
  "evolution_dag.js",
  "guided_tour.js",
];

files.forEach((file) => {
  execFileSync(process.execPath, ["--check", path.join(staticDir, file)], {
    cwd: repoRoot,
    stdio: "pipe",
  });
});

console.log(`Checked ${files.length} static visualizer scripts with node --check.`);
