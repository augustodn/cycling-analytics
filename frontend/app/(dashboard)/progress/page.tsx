import { LineChart } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

export default async function ProgressPage() {
  const result = await cyclingApi<ToolResult>("progress", postJson("", { period: "90d", modality: "all" }));
  const evolution = (result.data as { power_duration_evolution?: { per_activity?: Array<{ date: string; watts: Record<string, number | null> }> } }).power_duration_evolution;
  const points = evolution?.per_activity ?? [];
  const durations = ["300", "1200", "3600"];
  return <>
    <h1>Progress</h1>
    <p className="muted">Per-activity best observed power. This is recorded evidence, not a controlled maximal test.</p>
    {durations.map((duration) => <LineChart key={duration} title={`Power duration · ${Number(duration) / 60} min`} labels={points.map((point) => point.date)} series={[{ name: "Watts", values: points.map((point) => point.watts[duration]) }]} />)}
    <p className="muted">{points.length} activities evaluated in the latest 90 days.</p>
  </>;
}
