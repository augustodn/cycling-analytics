import Link from "next/link";
import { JsonPanel, LineChart, MetricGrid } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

type Props = { params: Promise<{ id: string }> };

export default async function ActivityDetailPage({ params }: Props) {
  const { id } = await params;
  const [detail, analysis, stream] = await Promise.all([
    cyclingApi<ToolResult>("activity", postJson("", { activity_id: id })),
    cyclingApi<ToolResult>("activity/analyze", postJson("", { activity_id: id })),
    cyclingApi<ToolResult>("stream", postJson("", { activity_id: id, max_points: 1500 })),
  ]);
  const activity = (detail.data as { activity: Record<string, unknown> }).activity;
  const metrics = (analysis.data as { metrics: Record<string, unknown> }).metrics;
  const power = metrics.power as { np_w?: number | null; intensity_factor?: number | null; coverage?: { fraction?: number | null } } | undefined;
  const samples = (stream.data as { samples?: Array<{ elapsed_s: number; power_w: number | null; hr_bpm: number | null }> }).samples ?? [];
  return <>
    <p><Link href="/activities">← Activities</Link></p>
    <h1>{String(activity.source_name ?? id.slice(0, 12))}</h1>
    <p className="muted">{String(activity.start_time)} · {String(activity.modality)} · UTC</p>
    <MetricGrid values={[
      ["Duration", `${Math.round(Number(activity.duration_s) / 60)} min`],
      ["Normalized power", power?.np_w == null ? "N/A" : `${power.np_w.toFixed(0)} W`],
      ["Intensity factor", power?.intensity_factor == null ? "N/A" : power.intensity_factor.toFixed(2)],
      ["Power coverage", power?.coverage?.fraction == null ? "N/A" : `${(power.coverage.fraction * 100).toFixed(1)}%`],
    ]} />
    <LineChart title="Power stream (display-decimated)" labels={samples.map((sample) => `${Math.round(sample.elapsed_s / 60)}m`)} series={[{ name: "Power W", values: samples.map((sample) => sample.power_w) }]} />
    <LineChart title="Heart-rate stream (display-decimated)" labels={samples.map((sample) => `${Math.round(sample.elapsed_s / 60)}m`)} series={[{ name: "Heart rate bpm", values: samples.map((sample) => sample.hr_bpm) }]} />
    <JsonPanel title="Analysis evidence and quality" value={metrics} />
  </>;
}
