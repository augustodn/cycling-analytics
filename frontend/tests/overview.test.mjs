// Run with: node --test tests/overview.test.mjs
import assert from "node:assert/strict";
import { once } from "node:events";
import { readFileSync } from "node:fs";
import { PassThrough } from "node:stream";
import test from "node:test";
import vm from "node:vm";
import React from "react";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToPipeableStream } from "react-dom/server";
import ts from "typescript";

function loadTsx(path, imports) {
  const exports = {};
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  vm.runInNewContext(outputText, {
    exports,
    require: (name) => {
      assert.ok(name in imports, `Unexpected import: ${name}`);
      return imports[name];
    },
  }, { filename: path });
  return exports;
}

test("Overview streams independent panels without duplicate requests", { timeout: 5000 }, async (t) => {
  const calls = [];
  const pending = new Map();
  const imports = {
    react: React,
    "react/jsx-runtime": jsxRuntime,
    "next/link": { __esModule: true, default: ({ children, ...props }) => React.createElement("a", props, children) },
    "@/lib/cycling-api": {
      cyclingApi: (path, init) => {
        calls.push({ path, init });
        return new Promise((resolve) => pending.set(path, resolve));
      },
      postJson: (_, payload) => ({ method: "POST", body: JSON.stringify(payload) }),
    },
  };
  imports["@/components/charts"] = loadTsx("../components/charts.tsx", imports);
  imports["@/components/activity-table"] = loadTsx("../components/activity-table.tsx", imports);
  const { default: OverviewPage } = loadTsx("../app/(dashboard)/overview/page.tsx", imports);
  const output = new PassThrough();
  let html = "";
  output.on("data", (chunk) => { html += chunk.toString(); });
  const errors = [];
  let stream;
  const shell = new Promise((resolve, reject) => {
    stream = renderToPipeableStream(React.createElement("main", null, React.createElement(OverviewPage)), {
      onShellReady() { stream.pipe(output); resolve(); },
      onShellError: reject,
      onError: (error) => errors.push(error),
    });
  });
  t.after(() => stream.abort());
  await shell;
  assert.match(html, /<h1>Overview<\/h1>/);
  assert.match(html, /Upload FIT\/TCX/);
  assert.equal((html.match(/role="status"/g) ?? []).length, 4);
  assert.equal(calls.length, 4);
  assert.equal(pending.size, 4);
  assert.deepEqual(JSON.parse(calls.find((call) => call.path === "power-curves").init.body), {
    period: "90d", modality: "all",
  });

  const activityChunk = once(output, "data");
  pending.get("activities")({ data: { activities: Array.from({ length: 12 }, (_, index) => ({
    id: `ride-${index}`, source_name: `ride-${index}`, start_time: "2026-01-01", modality: "road", duration_s: 3600,
  })) } });
  await activityChunk;
  assert.match(html, /Activities: <strong class="metric-value">12<\/strong>/);
  assert.match(html, /ride-9/);
  assert.doesNotMatch(html, /ride-10/);
  assert.doesNotMatch(html, /metric-value">45\.6/); // Load is still pending.

  const weeklyChunk = once(output, "data");
  const weeklyCall = calls.find((call) => call.path.startsWith("weekly-cycling-training?end="));
  pending.get(weeklyCall.path)({ data: { weeks: [{ iso_week: "2026-W01", total_seconds: 7200 }] } });
  await weeklyChunk;
  assert.match(html, /title="2026-W01: 2"/);
  assert.doesNotMatch(html, /title="1m: 450"/); // Curve is still pending.

  const curveChunk = once(output, "data");
  pending.get("power-curves")({ data: { watts: { 60: 450, 1200: null } } });
  await curveChunk;
  assert.match(html, /title="1m: 450"/);
  assert.match(html, /title="20m: N\/A"/);
  const completed = once(output, "end");
  pending.get("load")({ data: { days: [{ date: "2026-01-01", ctl: 45.6, atl: 23.4, tsb: 22.2 }] } });
  await completed;
  assert.match(html, /metric-value">45\.6/);
  assert.match(html, /metric-value">23\.4/);
  assert.match(html, /metric-value">22\.2/);
  assert.equal(calls.length, 4);
  assert.deepEqual(errors, []);
});
