import Link from "next/link";
import { BarChart, MetricGrid } from "@/components/charts";
import { cyclingApi, postJson, type ToolResult } from "@/lib/cycling-api";

type Activity = { id: string; start_time: string; source_name?: string; modality: string; duration_s: number };
type Props = { searchParams: Promise<{ activity_id?: string }> };

export default async function DurabilityPage({ searchParams }: Props) {
  const { activity_id: activityId } = await searchParams;
  const activitiesResult = await cyclingApi<ToolResult<{ activities: Activity[] }>>("activities");
  const activities = activitiesResult.data.activities.filter((activity) => activity.duration_s >= 3600);
  if (!activityId) return <>
    <h1>Durability</h1><p className="muted">Choose a ride to inspect power retention after accumulated work.</p>
    <section className="panel"><ul>{activities.map((activity) => <li key={activity.id}><Link href={`/durability?activity_id=${encodeURIComponent(activity.id)}`}>{activity.start_time.slice(0, 10)} · {activity.source_name ?? activity.id.slice(0, 12)} · {Math.round(activity.duration_s / 60)} min</Link></li>)}</ul></section>
  </>;

  const result = await cyclingApi<ToolResult>("durability", postJson("", { activity_id: activityId }));
  const data = result.data as { available?: boolean; reason?: string; points?: Array<{ duration_s: number; threshold_kj: number; retention_pct: number | null }>; total_work_kj?: number };
  if (!data.available) return <><h1>Durability</h1><section className="panel"><p>{data.reason ?? "Insufficient power data for this analysis."}</p><Link href="/durability">Choose another ride</Link></section></>;
  const points = data.points ?? [];
  return <>
    <p><Link href="/durability">← Choose another ride</Link></p><h1>Durability</h1>
    <p className="muted">Observed efforts after prior work; this is not a controlled fatigue test.</p>
    <MetricGrid values={[["Recorded work", `${Number(data.total_work_kj ?? 0).toFixed(0)} kJ`], ["Effort points", points.length]]} />
    <BarChart title="Power retention by accumulated work" labels={points.map((point) => `${point.duration_s / 60}m · ${point.threshold_kj} kJ`)} values={points.map((point) => point.retention_pct)} />
  </>;
}
