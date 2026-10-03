// Run with: node --test tests/power-hr.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import * as jsxRuntime from "react/jsx-runtime";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

function loadTsx(path, imports) {
  const exports = {};
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  vm.runInNewContext(outputText, {
    exports,
    require: (name) => { assert.ok(name in imports, `Unexpected import: ${name}`); return imports[name]; },
  }, { filename: path });
  return exports;
}

test("Power-HR server GET controls, separated SVG curves, and sample evidence", async (t) => {
  const imports = { "react/jsx-runtime": jsxRuntime };
  const helpers = loadTsx("../lib/power-hr.ts", imports);
  imports["@/lib/power-hr"] = helpers;
  const charts = loadTsx("../components/power-hr-chart.tsx", imports);
  imports["@/components/power-hr-chart"] = charts;
  assert.deepEqual(Array.from(helpers.HR_ZONE_COLORS), ["#808080", "#87CEEB", "#228B22", "#FFD700", "#FF69B4", "#FF0000", "#8A2BE2"]);
  const delta = helpers.deltaPowerHRGroups(
    [{ group_id: "all", kind: "all", bins: [{ power_low_w: 200, power_high_w: 210, power_w: 205, hr_median_bpm: 132, hr_p25_bpm: 130, hr_p75_bpm: 134, activity_count: 3, valid_seconds: 400, status: "ok" }] }],
    [{ group_id: "all", kind: "all", bins: [{ power_low_w: 200, power_high_w: 210, power_w: 205, hr_median_bpm: 136, hr_p25_bpm: 134, hr_p75_bpm: 138, activity_count: 4, valid_seconds: 500, status: "ok" }] }],
    "all",
  );
  assert.equal(delta[0].delta_hr, -4);
  const bin = { power_w: 200, hr_median_bpm: 136, hr_p25_bpm: 130, hr_p75_bpm: 140, activity_count: 1, valid_seconds: 90, status: "low_confidence", confidence_reasons: ["insufficient_activities"] };
  const activity = (id, date, bounds) => ({ activity_id: id, date, duration_s: 3600, modality: "road", effective_ftp_w: 285, hr_zone_bounds: bounds, analysis: { groups: [{ group_id: "all", kind: "all", bins: [{ ...bin, hr_p25_bpm: 125 }] }] } });
  const bucket = (ride) => ({
    groups: [{ group_id: "all", kind: "all", bins: [bin] }, { group_id: "work_1", kind: "work_band", start_kj: 0, end_kj: 500, bins: [bin] }],
    activities: { [ride.activity_id]: ride },
    hr_at_fixed_power: [{ ...bin, activity_id: ride.activity_id, date: ride.date, target_power_w: 200, matched_power_median_w: 201 }],
  });
  const calls = [];
  let result = { data: {
    current_period: { environments: {
      outdoor: bucket(activity("new-ride", "2026-10-01", [127, 141, 147, 158])),
      indoor: bucket({ ...activity("trainer", "2026-09-30", [127, 141, 147, 158]), modality: "indoor" }),
      unknown: { groups: [], activities: {}, hr_at_fixed_power: [] },
    } },
    comparison_period: { environments: { outdoor: bucket(activity("old-ride", "2026-09-01", [130, 145, 158, 170])) } },
  } };
  imports["@/lib/cycling-api"] = {
    cyclingApi: async (path, init) => { calls.push({ path, init }); return path === "activities" ? { data: { activities: [{ id: "ride", start_time: "2026-10-01T10:00:00Z", modality: "road" }] } } : result; },
    postJson: (_, payload) => ({ method: "POST", body: JSON.stringify(payload) }),
  };
  const { default: Page } = loadTsx("../app/(dashboard)/power-hr/page.tsx", imports);
  const render = async (params) => renderToStaticMarkup(await Page({ searchParams: Promise.resolve(params) }));

  await t.test("custom comparison posts only contract keys and renders confidence, zones, and trends", async () => {
    const html = await render({ view: "compare", period: "custom", start_date: "2026-09-20", end_date: "2026-10-02", compare_period: "28d", grouping: "fatigue", fatigue_thresholds_kj: "500,1000", target_power_w: "200", mode: "stable", lag_s: "45" });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].path, "power-hr");
    assert.equal(calls[0].init.method, "POST");
    const payload = JSON.parse(calls[0].init.body);
    assert.equal(payload.period, "custom");
    assert.equal(payload.start_date, "2026-09-20");
    assert.deepEqual(payload.compare_period, { period: "28d" });
    assert.deepEqual(payload.fatigue_thresholds_kj, [500, 1000]);
    assert.deepEqual(payload.target_power_w, [200]);
    assert.equal(payload.lag_s, 45);
    assert.equal(payload.mode, "stable");
    assert.equal(payload.environment, "both");
    assert.equal(payload.parameter_mode, "historical");
    assert.ok(!("grouping" in payload) && !("view" in payload));
    assert.match(html, /action="\/power-hr" method="get"/);
    assert.match(html, /Current · 0–500 kJ/);
    assert.match(html, /Comparison · 0–500 kJ/);
    assert.doesNotMatch(html, /Current · All eligible samples/);
    assert.match(html, /stroke-dasharray="7 5"/);
    assert.match(html, /Power in watts.*heart rate in bpm/);
    assert.match(html, /HR at fixed power over time/);
    assert.match(html, /low_confidence \(insufficient_activities\)/);
    assert.match(html, /latest selected activity \(2026-10-01, new-ride\)/);
    assert.match(html, /Activity HR bounds differ or are missing/);
    assert.match(html, /matched power 201 W/);
    assert.match(html, /<title>Current · 0–500 kJ: 200 W; median 136 bpm/);
    assert.match(html, /<h2>Indoor<\/h2>/);
    assert.match(html, /<h2>Outdoor<\/h2>/);
    assert.match(html, /<h2>Unknown<\/h2>/);
    for (const color of Array.from(helpers.HR_ZONE_COLORS).slice(0, 5)) assert.ok(html.includes(color), color);
    assert.doesNotMatch(html, /NaN|Infinity/);
  });

  await t.test("individual uses local sample quartiles and excludes all period keys", async () => {
    result = { data: { current_period: { environments: { outdoor: bucket(activity("ride", "2026-10-01", [127, 141, 147, 158])) } } } };
    const html = await render({ view: "individual", activity_id: "ride", period: "custom", start_date: "bad", compare_period: "custom", environment: "outdoor", target_power_w: "200" });
    const payload = JSON.parse(calls.findLast((call) => call.path === "power-hr").init.body);
    assert.equal(payload.activity_id, "ride");
    assert.equal(payload.min_activities, 1);
    for (const key of ["period", "start_date", "end_date", "compare_period"]) assert.ok(!(key in payload));
    assert.match(html, /125–140/); // Activity-local P25, not aggregate P25 = 130.
    assert.match(html, /paired samples within this ride/);
    assert.equal(helpers.powerHRRequest({ grouping: "half", period: "42d" }).request.elapsed_splits, 2);
    assert.equal(helpers.powerHRRequest({ grouping: "third", period: "365d" }).request.elapsed_splits, 3);
  });

  await t.test("invalid GET input never calls authenticated API; unavailable trend retains evidence", async () => {
    const count = calls.length;
    const html = await render({ period: "custom", start_date: "2026-02-30", end_date: "2026-10-01" });
    assert.equal(calls.length, count);
    assert.match(html, /role="alert"/);
    for (const params of [{ lag_s: "12" }, { lag_s: "30.5" }, { min_total_seconds: "" }, { environment: "wrong" }, { grouping: "fatigue", fatigue_thresholds_kj: "500,500" }, { target_power_w: "200,200" }, { mode: "stable", aerobic_ceiling_ftp_fraction: "0.39" }]) assert.throws(() => helpers.powerHRRequest(params));
    const empty = renderToStaticMarkup(jsxRuntime.jsx(charts.PowerHRChart, { title: "Unavailable", timeAxis: true, series: [{ name: "200 W", points: [{ x: Date.parse("2026-10-01"), hr_median_bpm: null, hr_p25_bpm: null, hr_p75_bpm: null, valid_seconds: 0, status: "unavailable" }] }] }));
    assert.match(empty, /No eligible paired/);
    assert.match(empty, /<td>unavailable<\/td>/);
    assert.doesNotMatch(empty, /NaN|Infinity|<svg/);
    const sevenZones = renderToStaticMarkup(jsxRuntime.jsx(charts.PowerHRChart, { title: "Seven zones", bounds: [110, 125, 140, 150, 160, 170], series: [{ name: "Ride", points: [{ ...bin, x: 200 }] }] }));
    for (const color of helpers.HR_ZONE_COLORS) assert.ok(sevenZones.includes(color), color);
    for (const label of ["Z5a", "Z5b", "Z5c"]) assert.ok(sevenZones.includes(label), label);
  });
});
