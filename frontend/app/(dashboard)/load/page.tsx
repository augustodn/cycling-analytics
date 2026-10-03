import { LineChart, MetricGrid } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

export default async function LoadPage() {
  const now = new Date();
  const end = now.toISOString().slice(0, 10);
  const start = new Date(now.getTime() - 180 * 86_400_000).toISOString().slice(0, 10);
  const result = await cyclingApi<ToolResult>("load", postJson("", { start, end, modality: "all" }));
  const data = result.data as { days?: Array<{ date: string; ctl?: number; atl?: number; tsb?: number; total_load?: number }> };
  const days = data.days ?? [];
  const last = days.at(-1);
  return <>
    <h1>Training load</h1>
    <p className="muted">UTC daily series; CTL/ATL are load-based estimates, not direct measures of fitness or fatigue.</p>
    <MetricGrid values={[["CTL · 42 days", last?.ctl?.toFixed(1)], ["ATL · 7 days", last?.atl?.toFixed(1)], ["TSB", last?.tsb?.toFixed(1)]]} />
    <LineChart title="Training load" labels={days.map((day) => day.date)} series={[
      { name: "CTL", values: days.map((day) => day.ctl ?? null) },
      { name: "ATL", values: days.map((day) => day.atl ?? null) },
      { name: "TSB", values: days.map((day) => day.tsb ?? null) },
    ]} />
  </>;
}
