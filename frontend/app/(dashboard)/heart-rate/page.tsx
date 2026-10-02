import { BarChart, MetricGrid } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

export default async function HeartRatePage() {
  const result = await cyclingApi<ToolResult>("period-hr-distributions", postJson("", { period: "90d", modality: "all" }));
  const data = result.data as {
    seconds?: number[];
    percentages?: number[];
    total_seconds?: number;
    unknown_seconds?: number;
    zones?: Array<{ label: string; hr_range: string }>;
    parameter_groups?: Array<{ parameter_id: string; zones: Array<{ label: string; hr_range: string }>; seconds: number[]; percentages: number[]; activity_ids: string[] }>;
  };
  const groups = data.parameter_groups?.length
    ? data.parameter_groups
    : [{ parameter_id: "current", zones: data.zones ?? [], seconds: data.seconds ?? [], percentages: data.percentages ?? [], activity_ids: [] }];
  return <>
    <h1>Heart-rate distribution</h1>
    <p className="muted">Last 90 days. Percentages use known HR time; missing samples remain unknown.</p>
    <MetricGrid values={[["Known HR time", `${Math.round((data.total_seconds ?? 0) / 3600)} h`], ["Unknown HR time", `${Math.round((data.unknown_seconds ?? 0) / 60)} min`]]} />
    {groups.map((group) => <section key={`${group.parameter_id}-${group.zones.length}`}>
      {groups.length > 1 && <p className="muted">Profile parameter set {group.parameter_id}; {group.activity_ids.length} activities.</p>}
      <BarChart title="Time by heart-rate zone (minutes)" labels={group.zones.map((zone) => zone.label)} values={group.seconds.map((value) => value / 60)} />
      <div className="panel table-wrap"><table><thead><tr><th>Zone</th><th>HR range</th><th>Time</th><th>Known time</th></tr></thead><tbody>
        {group.zones.map((zone, index) => <tr key={zone.label}><td>{zone.label}</td><td>{zone.hr_range}</td><td>{Math.round(group.seconds[index] / 60)} min</td><td>{(group.percentages[index] ?? 0).toFixed(1)}%</td></tr>)}
      </tbody></table></div>
    </section>)}
  </>;
}
