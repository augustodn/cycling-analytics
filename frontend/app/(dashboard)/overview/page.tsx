import Link from "next/link";
import { Suspense } from "react";
import { ActivityTable, type Activity } from "@/components/activity-table";
import { BarChart, MetricGrid } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

async function RecentActivities() {
  const activityResult = await cyclingApi<ToolResult<{ activities: Activity[] }>>("activities");
  const activities = activityResult.data.activities;
  return <section className="panel">
    <h2>Recent activities</h2>
    <p className="muted">Activities: <strong className="metric-value">{activities.length}</strong></p>
    <ActivityTable activities={activities.slice(0, 10)} />
    <Link href="/activities">All activities</Link>
  </section>;
}

async function WeeklyTraining({ today }: { today: string }) {
  const weeklyResult = await cyclingApi<ToolResult>(`weekly-cycling-training?end=${today}`);
  const weeks = (weeklyResult.data as { weeks?: Array<{ iso_week: string; total_seconds: number }> }).weeks ?? [];
  return <BarChart title="Weekly cycling time (hours)" labels={weeks.map((week) => week.iso_week)} values={weeks.map((week) => week.total_seconds / 3600)} />;
}

async function LoadMetrics({ start, today }: { start: string; today: string }) {
  const loadResult = await cyclingApi<ToolResult>("load", postJson("", { start, end: today, modality: "all" }));
  const days = (loadResult.data as { days?: Array<{ date: string; ctl?: number; atl?: number; tsb?: number }> }).days ?? [];
  const lastDay = days.at(-1);
  return <MetricGrid values={[
    ["CTL", lastDay?.ctl?.toFixed(1) ?? "N/A"],
    ["ATL", lastDay?.atl?.toFixed(1) ?? "N/A"],
    ["TSB", lastDay?.tsb?.toFixed(1) ?? "N/A"],
  ]} />;
}

async function PowerCurve() {
  // Keep the API's newest-activity anchor; do not pass today's end_date.
  const curveResult = await cyclingApi<ToolResult>("power-curves", postJson("", { period: "90d", modality: "all" }));
  const curve = (curveResult.data as { watts?: Record<string, number | null> }).watts ?? {};
  return <BarChart title="Best observed power (90 days)" labels={Object.keys(curve).map((key) => `${Number(key) / 60}m`)} values={Object.values(curve)} />;
}

function PanelFallback({ title }: { title: string }) {
  return <section className="panel chart-panel" aria-busy="true">
    <h2>{title}</h2>
    <p className="muted" role="status">Loading…</p>
  </section>;
}

export default function OverviewPage() {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const start = new Date(now.getTime() - 90 * 86_400_000).toISOString().slice(0, 10);

  return <>
    <h1>Overview</h1>
    <p className="muted">Training evidence from your uploaded activities. Missing sensor data remains unavailable, not zero.</p>
    <p><Link className="button" href="/upload">Upload FIT/TCX</Link></p>
    <Suspense fallback={<PanelFallback title="Training load" />}>
      <LoadMetrics start={start} today={today} />
    </Suspense>
    <div className="grid">
      <Suspense fallback={<PanelFallback title="Weekly cycling time (hours)" />}>
        <WeeklyTraining today={today} />
      </Suspense>
      <Suspense fallback={<PanelFallback title="Best observed power (90 days)" />}>
        <PowerCurve />
      </Suspense>
    </div>
    <Suspense fallback={<PanelFallback title="Recent activities" />}>
      <RecentActivities />
    </Suspense>
  </>;
}
