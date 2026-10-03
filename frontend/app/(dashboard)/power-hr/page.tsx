import { PowerHRChart, type PowerHRSeries } from "@/components/power-hr-chart";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";
import { NUMBER_CONTROLS, PERIODS, deltaPowerHRGroups, groupLabel, powerHRRequest, queryValue, selectedGroups, zoneReference, type PowerHRData, type SearchParams } from "@/lib/power-hr";

export default async function PowerHRPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const params = await searchParams;
  const value = (name: string, fallback = "") => queryValue(params, name, fallback);
  let selection: ReturnType<typeof powerHRRequest> | undefined;
  let data: PowerHRData | undefined;
  let error = "";
  let activities: Array<{ id: string; start_time: string; source_name?: string; modality: string }> = [];
  try {
    let resolvedParams = params;
    if (value("view") === "individual") {
      const catalog = await cyclingApi<ToolResult<{ activities?: typeof activities }>>("activities");
      activities = (catalog.data.activities ?? []).filter((activity) =>
        ["indoor", "road", "mtb", "gravel", "unknown"].includes(activity.modality),
      );
      resolvedParams = {
        ...params,
        activity_id: queryValue(params, "activity_id", activities[0]?.id ?? ""),
      };
    }
    selection = powerHRRequest(resolvedParams);
    data = (await cyclingApi<ToolResult<PowerHRData>>("power-hr", postJson("", selection.request))).data;
  } catch (problem) {
    error = problem instanceof Error ? problem.message : "Power/HR analysis is unavailable.";
  }
  if (selection?.view === "individual" && !activities.length && !error) {
    try {
      const catalog = await cyclingApi<ToolResult<{ activities?: typeof activities }>>("activities");
      activities = (catalog.data.activities ?? []).filter((activity) =>
        ["indoor", "road", "mtb", "gravel", "unknown"].includes(activity.modality),
      );
    } catch (problem) {
      error = problem instanceof Error ? problem.message : "Activities are unavailable.";
    }
  }
  const select = (name: string, label: string, options: ReadonlyArray<readonly [string, string]>, fallback: string) => <label>{label}<select name={name} defaultValue={value(name, fallback)}>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
  const periodControls = (prefix = "", label = "Current") => <>
    {select(`${prefix}period`, `${label} period`, PERIODS.map((period) => [period, period === "all" ? "All history" : period === "custom" ? "Custom dates" : `Last ${period.slice(0, -1)} days`] as const), "90d")}
    <label>{label} custom start<input name={`${prefix}start_date`} type="date" defaultValue={value(`${prefix}start_date`)} /></label>
    <label>{label} custom end<input name={`${prefix}end_date`} type="date" defaultValue={value(`${prefix}end_date`)} /></label>
  </>;
  const describePeriod = (period: PowerHRData["current_period"] | undefined) => {
    const selection = period?.selection;
    if (selection?.activity_id) return `Activity ${selection.activity_id}`;
    if (selection?.start_date && selection.end_date) return `${selection.start_date}–${selection.end_date}`;
    if (selection?.period === "all") return "All history";
    return selection?.end_date
      ? `${selection?.period} ending ${selection.end_date}`
      : `${selection?.period ?? "Selected period"} ending at latest activity`;
  };
  const indoorOutdoorDeltas = selection && data && selection.request.environment === "both"
    ? deltaPowerHRGroups(
      data.current_period.environments.indoor?.groups ?? [],
      data.current_period.environments.outdoor?.groups ?? [],
      selection.grouping,
    )
    : [];
  return <>
    <h1>Power ↔ Heart rate</h1>
    <p className="muted">Descriptive, lag-aligned observations — not a causal fitness test. Indoor, outdoor, and unknown environments are never pooled.</p>
    <form action="/power-hr" method="get" className="panel form-grid">
      {select("view", "View", [["period", "Period"], ["individual", "Individual activity"], ["compare", "Compare periods"]], "period")}
      <label>Activity (individual view only)<select name="activity_id" defaultValue={selection?.request.activity_id as string ?? value("activity_id", activities[0]?.id ?? "")}>{activities.map((activity) => <option key={activity.id} value={activity.id}>{activity.start_time.slice(0, 10)} · {activity.source_name ?? activity.id} · {activity.modality}</option>)}</select></label>
      {periodControls()}
      {periodControls("compare_", "Comparison")}
      {select("environment", "Environment", [["both", "Both (separate charts)"], ["indoor", "Indoor"], ["outdoor", "Outdoor"]], "both")}
      {select("parameter_mode", "FTP / HR parameters", [["historical", "Historical per activity"], ["current", "Current profile"]], "historical")}
      {select("mode", "Sample mode", [["observed", "Observed"], ["stable", "Stable aerobic"]], "observed")}
      {select("grouping", "Curve groups", [["all", "All samples"], ["fatigue", "Fatigue / work bands"], ["half", "Elapsed halves"], ["third", "Elapsed thirds"]], "all")}
      <label>Fatigue boundaries (kJ, increasing)<input name="fatigue_thresholds_kj" defaultValue={value("fatigue_thresholds_kj", "500,1000,1500")} /></label>
      <label>Fixed-power targets (W, comma-separated)<input name="target_power_w" defaultValue={value("target_power_w", "180,190,200,210,220,240")} /></label>
      <details className="wide"><summary>Alignment, stability, and evidence controls</summary><div className="form-grid">
        {NUMBER_CONTROLS.map(([name, label, fallback, min, max, step]) => <label key={name}>{label}<input name={name} type="number" min={min} max={max} step={step} defaultValue={value(name, String(name === "min_activities" && value("view") === "individual" ? 1 : fallback))} required={fallback !== ""} /></label>)}
      </div></details>
      <p className="muted wide">Custom dates apply only to custom periods; comparison applies only to Compare. Individual ignores period controls. Stable mode requires FTP and cadence; optional aerobic bounds also filter observed mode. Work bands use cumulative mechanical work, not a fatigue diagnosis.</p>
      {value("view") === "compare" && <p className="muted wide">With a bounded current period, a relative comparison preset uses the equal-length window immediately before it. If current period is all history, the comparison preset ends at the latest activity. Custom ranges stay as selected.</p>}
      <button className="button" type="submit">Analyze</button>
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {data && selection && <>
      <p className="muted">{selection.view === "individual" ? "Individual P25–P75 describes paired samples within this ride." : "Period medians and P25–P75 describe activity-level bin medians, with equal activity weight."} Minimum evidence: {String(selection.request.min_activities)} activities and {String(selection.request.min_total_seconds)} valid seconds per bin. Lag: {String(selection.request.lag_s)} s.</p>
      <p className="muted">Current: {describePeriod(data.current_period)}{data.comparison_period ? ` · Comparison: ${describePeriod(data.comparison_period)}` : ""}</p>
      {(["indoor", "outdoor", "unknown"] as const).map((environment) => {
        const periods = [
          { name: "Current", bucket: data.current_period.environments[environment], comparison: false },
          { name: "Comparison", bucket: data.comparison_period?.environments[environment], comparison: true },
        ].filter((item) => item.bucket);
        if (!periods.length) return null;
        const comparisonBucket = data.comparison_period?.environments[environment];
        const periodDeltas = comparisonBucket
          ? deltaPowerHRGroups(
            periods[0].bucket!.groups,
            comparisonBucket.groups,
            selection.grouping,
          )
          : [];
        const activities = periods.flatMap((period) => Object.values(period.bucket!.activities));
        const { reference, differs } = zoneReference(activities);
        const series: PowerHRSeries[] = periods.flatMap((period) => {
          const bucket = period.bucket!;
          const groups = selection.view === "individual" ? Object.values(bucket.activities).flatMap((activity) => activity.analysis?.groups ?? []) : bucket.groups;
          const activity = selection.view === "individual" ? Object.values(bucket.activities)[0] : undefined;
          const context = activity?.context;
          const detail = activity
            ? [
              activity.date,
              activity.modality,
              `${Math.round(activity.duration_s / 60)} min`,
              activity.effective_ftp_w == null ? null : `FTP ${activity.effective_ftp_w} W`,
              context?.avg_power_w == null ? null : `avg ${context.avg_power_w} W`,
              context?.avg_hr_bpm == null ? null : `${context.avg_hr_bpm} bpm avg HR`,
              activity.total_work_kj == null ? null : `${activity.total_work_kj} kJ work`,
              context?.temperature_c == null ? null : `${context.temperature_c} °C`,
            ].filter(Boolean).join(" · ")
            : undefined;
          return selectedGroups(groups, selection.grouping).map((group) => ({
            name: `${period.name} · ${groupLabel(group)}`,
            comparison: period.comparison,
            points: group.bins.map((bin) => ({
              ...bin,
              x: bin.power_w,
              activity_count: bin.activity_count ?? 1,
              status: bin.status ?? ((bin.valid_seconds < Number(selection.request.min_total_seconds) || Number(selection.request.min_activities) > 1) ? "low_confidence" : "ok"),
              detail,
            })),
          }));
        });
        const targets = selection.request.target_power_w as number[];
        const trend: PowerHRSeries[] = periods.flatMap((period) => targets.flatMap((target) => {
          const points = period.bucket!.hr_at_fixed_power.filter((point) => point.target_power_w === target).map((point) => {
            const activity = period.bucket!.activities[point.activity_id];
            return { ...point, x: Date.parse(point.date), detail: `${point.activity_id} · ${activity?.modality ?? environment} · FTP ${activity?.effective_ftp_w ?? "N/A"} W · matched power ${point.matched_power_median_w ?? "N/A"} W` };
          });
          return [
            { name: `${period.name} · ${target} W per activity`, comparison: period.comparison, pointsOnly: true, points },
            { name: `${period.name} · ${target} W 3-activity median`, comparison: period.comparison, points: points.map((point) => ({ ...point, hr_median_bpm: point.rolling_median_hr_bpm ?? null, hr_p25_bpm: null, hr_p75_bpm: null })) },
          ];
        }));
        return <section key={environment}>
          <h2>{environment[0].toUpperCase() + environment.slice(1)}</h2>
          <p className="muted">{periods.map((period) => `${period.name}: ${Object.keys(period.bucket!.activities).length} activities`).join(" · ")}. {environment === "unknown" && "Unknown modality stays separate; it is not classified as outdoor."}</p>
          <p className="muted">{reference ? `HR zone reference: ${selection.request.parameter_mode === "current" ? "current profile" : `latest selected activity (${reference.date}, ${reference.activity_id})`}. Bounds: ${reference.hr_zone_bounds!.join(", ")} bpm.${differs ? " Activity HR bounds differ or are missing; these lines are a visual reference, not historical reclassification." : ""}` : "HR zone reference unavailable: no personalized bounds for these activities."}</p>
          <PowerHRChart title={`${environment} · Power vs heart rate`} series={series} bounds={reference?.hr_zone_bounds ?? []} />
          {periodDeltas.length > 0 && <div className="panel table-wrap"><h3>Current − earlier HR at matched power</h3><table><caption>{environment} comparison; low-confidence bins omitted</caption><thead><tr><th scope="col">Curve</th><th scope="col">Power</th><th scope="col">Earlier HR</th><th scope="col">Current HR</th><th scope="col">Δ HR</th><th scope="col">Activities</th></tr></thead><tbody>{periodDeltas.map((row) => <tr key={`${row.curve}-${row.power}`}><th scope="row">{row.curve}</th><td>{row.power}</td><td>{row.baseline_hr}</td><td>{row.primary_hr}</td><td>{row.delta_hr > 0 ? "+" : ""}{row.delta_hr.toFixed(1)} bpm</td><td>{row.baseline_activities} → {row.primary_activities}</td></tr>)}</tbody></table></div>}
          <p className="muted">Fixed-power trend uses all eligible aligned samples, independent of curve group selection. Targets ±{String(selection.request.target_power_tolerance_w)} W; minimum {String(selection.request.target_power_min_seconds)} s per ride. Missing matches stay unavailable, never interpolated.</p>
          <PowerHRChart title={`${environment} · HR at fixed power over time`} series={trend} bounds={reference?.hr_zone_bounds ?? []} timeAxis />
           <details className="panel table-wrap"><summary>Activity context and exclusions</summary><table><caption>{environment} activity context</caption><thead><tr><th scope="col">Period</th><th scope="col">Date / activity</th><th scope="col">Modality</th><th scope="col">Duration</th><th scope="col">Avg power</th><th scope="col">Avg HR</th><th scope="col">Temperature</th><th scope="col">Effective FTP</th><th scope="col">HR bounds</th><th scope="col">Sample / work context</th></tr></thead><tbody>
             {periods.flatMap((period) => Object.values(period.bucket!.activities).map((activity) => <tr key={`${period.name}-${activity.activity_id}`}><td>{period.name}</td><th scope="row">{activity.date} · {activity.context?.source_name ?? activity.activity_id}</th><td>{activity.modality}</td><td>{activity.duration_s ? `${Math.round(activity.duration_s / 60)} min` : "N/A"}</td><td>{activity.context?.avg_power_w ?? "N/A"} W</td><td>{activity.context?.avg_hr_bpm ?? "N/A"} bpm</td><td>{activity.context?.temperature_c ?? "N/A"}</td><td>{activity.effective_ftp_w ?? "N/A"} W</td><td>{activity.hr_zone_bounds?.join(", ") ?? "N/A"}</td><td>{activity.reason ?? "available"}{activity.parameter_warning ? ` · ${activity.parameter_warning}` : ""}{activity.work_complete === false ? " · incomplete work history" : ""}{activity.analysis?.fatigue_unclassified_seconds ? ` · ${activity.analysis.fatigue_unclassified_seconds} s excluded from work bands` : ""}</td></tr>))}
          </tbody></table></details>
        </section>;
      })}
      {indoorOutdoorDeltas.length > 0 && <div className="panel table-wrap"><h2>Indoor vs. outdoor HR penalty</h2><table><caption>Indoor minus outdoor HR at matched power; low-confidence bins omitted</caption><thead><tr><th scope="col">Curve</th><th scope="col">Power</th><th scope="col">Indoor HR</th><th scope="col">Outdoor HR</th><th scope="col">Δ HR</th></tr></thead><tbody>{indoorOutdoorDeltas.map((row) => <tr key={`${row.curve}-${row.power}`}><th scope="row">{row.curve}</th><td>{row.power}</td><td>{row.primary_hr}</td><td>{row.baseline_hr}</td><td>{row.delta_hr > 0 ? "+" : ""}{row.delta_hr.toFixed(1)} bpm</td></tr>)}</tbody></table></div>}
      <p className="muted">{data.caveat ?? "Heat, hydration, terrain, sensors, and lag can change HR independently of fitness."}</p>
    </>}
  </>;
}
