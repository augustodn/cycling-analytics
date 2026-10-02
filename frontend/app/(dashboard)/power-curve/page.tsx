import { BarChart } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

export default async function PowerCurvePage() {
  const result = await cyclingApi<ToolResult>("power-curves", postJson("", { period: "90d", modality: "all" }));
  const data = result.data as { watts?: Record<string, number | null>; activities_evaluated?: number };
  const entries = Object.entries(data.watts ?? {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  return <>
    <h1>Power curve</h1>
    <p className="muted">Best eligible observed power in the last 90 days across {data.activities_evaluated ?? 0} activities.</p>
    <BarChart title="Best observed power by duration" labels={entries.map(([seconds]) => `${Math.round(Number(seconds) / 60)}m`)} values={entries.map(([, watts]) => watts)} />
  </>;
}
