import test from "node:test";
import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(process.cwd());
const read = (path) => readFile(resolve(root, path), "utf8");
const exists = async (path) => { try { await access(resolve(root, path)); return true; } catch { return false; } };

test("removed scanner routes are no longer present", async () => {
  for (const route of [
    "app/strategies/weekly-swing/page.tsx",
    "app/strategies/custom-strategy/page.tsx",
    "app/strategies/credit-butterfly-condor/weekly/page.tsx",
  ]) assert.equal(await exists(route), false, route + " should be removed");
});

test("removed scanner pages are absent from app navigation", async () => {
  const shell = await read("components/app-shell.tsx");
  for (const href of [
    "/strategies/weekly-swing",
    "/strategies/custom-strategy",
    "/strategies/credit-butterfly-condor/weekly",
  ]) assert.equal(shell.includes(href), false, href + " should not be navigable");
});

test("dashboard no longer links to Custom Strategy", async () => {
  const dashboard = await read("app/page.tsx");
  assert.equal(dashboard.includes('name: "Custom Strategy"'), false);
  assert.equal(dashboard.includes("/strategies/custom-strategy"), false);
});

test("Box Spread removed sections and mounts while scanner/history remain", async () => {
  const page = await read("app/strategies/box-spread/page.tsx");
  assert.equal(page.includes("BoxSpreadShadowPanel"), false);
  assert.equal(await exists("components/BoxSpreadShadowPanel.tsx"), false);
  assert.equal(page.includes("Shadow Monitor"), false);
  assert.equal(page.includes("System Health"), false);
  assert.equal(page.includes("box-system-health"), false);
  assert.equal(page.includes("StrategyWorkspace slug=\"box-spread\""), true);
  assert.equal(page.includes("BoxSpreadPostMarketHistory"), true);
});

test("Synthetic page no longer mounts trace panels but keeps scanner workspace", async () => {
  const page = await read("app/strategies/synthetic-arbitrage/page.tsx");
  assert.equal(page.includes("SyntheticTracePanel"), false);
  assert.equal(await exists("components/synthetic-trace-panel.tsx"), false);
  assert.equal(page.includes("StrategyWorkspace slug=\"synthetic-arbitrage\""), true);
});

test("shared pair monitor and alert/diagnostic sections are excluded only on requested scanner pages", async () => {
  const workspace = await read("components/strategy-workspace.tsx");
  assert.equal(workspace.includes('!["scanner", "calendar-spread", "synthetic-arbitrage"].includes(slug)'), true);
  assert.equal(workspace.includes('slug !== "box-spread" && ('), true);
  assert.equal(workspace.includes('!["scanner", "calendar-spread", "synthetic-arbitrage"].includes(slug)'), true);
  assert.equal(workspace.includes('!["calendar-spread", "synthetic-arbitrage"].includes(slug)'), true);
  // These components remain shared for other workspaces (e.g. IV Skew/Flip).
  assert.equal(workspace.includes("Strategy Alerts"), true);
  assert.equal(workspace.includes("Scanner Safety Boundary"), true);
  assert.equal(workspace.includes("Exchange Coverage Diagnostics"), false);
  assert.equal(workspace.includes("Session Diagnostics"), false);
});


test("PR #712 Cash Future page removals are actually absent from rendered UI", async () => {
  const page = await read("app/scanner/page.tsx");
  for (const section of [
    "Live Pair Monitor & Diagnostics",
    "Scanner safety boundary",
  ]) assert.equal(page.includes(section), false, section + " must not render on Cash Future scanner");
  assert.equal(page.includes("Post-Market Pair History"), true, "saved pair history must remain on Cash Future page");
  assert.equal(page.includes("filteredPairHistory.map"), true, "saved pair history rows must remain rendered");
  // Keep the main Cash Future opportunity scanner mounted.
  assert.equal(page.includes("filteredRows.map((row"), true);
});


test("Box Spread depth-shadow state updater remains explicitly typed", async () => {
  const workspace = await read("components/strategy-workspace.tsx");
  assert.match(workspace, /const \\[boxDepthShadow, setBoxDepthShadow\\] = useState<Record<string, unknown> \\| null>\\(null\\);/);
});
