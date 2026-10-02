import Link from "next/link";
import { ActivityTable, type Activity } from "@/components/activity-table";
import { BarChart, MetricGrid } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

export default async function OverviewPage() {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const start = new Date(now.getTime() - 90 * 86_400_000).toISOString().slice(0, 10);
  const [activityResult, weeklyResult, loadResult, curveResult] = await Promise.all([
    cyclingApi<ToolResult<{ activities: Activity[] }>>("activities"),
    cyclingApi<ToolResult>(`weekly-cycling-training?end=${today}`),
    cyclingApi<ToolResult>("load", postJson("", { start, end: today, modality: "all" })),
    cyclingApi<ToolResult>("power-curves", postJson("", { period: "90d", modality: "all" })),
  ]);
  const weeks = (weeklyResult.data as { weeks?: Array<{ iso_week: string; total_seconds: number }> }).weeks ?? [];
  const days = (loadResult.data as { days?: Array<{ date: string; ctl?: number; atl?: number; tsb?: number }> }).days ?? [];
  const curve = (curveResult.data as { watts?: Record<string, number | null> }).watts ?? {};
  const activities = activityResult.data.activities;
  const lastDay = days.at(-1);

  return <>
    <h1>Overview</h1>
    <p className="muted">Training evidence from your uploaded activities. Missing sensor data remains unavailable, not zero.</p>
    <MetricGrid values={[
      ["Activities", activities.length],
      ["CTL", lastDay?.ctl?.toFixed(1) ?? "N/A"],
      ["ATL", lastDay?.atl?.toFixed(1) ?? "N/A"],
      ["TSB", lastDay?.tsb?.toFixed(1) ?? "N/A"],
    ]} />
    <div className="grid">
      <BarChart title="Weekly cycling time (hours)" labels={weeks.map((week) => week.iso_week)} values={weeks.map((week) => week.total_seconds / 3600)} />
      <BarChart title="Best observed power (90 days)" labels={Object.keys(curve).map((key) => `${Number(key) / 60}m`)} values={Object.values(curve)} />
    </div>
    <section className="panel">
      <h2>Recent activities</h2>
      <ActivityTable activities={activities.slice(0, 10)} />
      <Link href="/activities">All activities</Link>
    </section>
  </>;
}
