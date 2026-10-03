import Link from "next/link";

export type Activity = { id: string; start_time: string; source_name?: string; modality: string; duration_s: number };

export function ActivityTable({ activities }: { activities: Activity[] }) {
  if (!activities.length) return <p className="muted">No activities yet. <Link href="/upload">Upload a FIT or TCX file</Link>.</p>;
  return <div className="table-wrap"><table><thead><tr><th>Date</th><th>Activity</th><th>Type</th><th>Duration</th></tr></thead><tbody>
    {activities.map((activity) => <tr key={activity.id}>
      <td>{activity.start_time.slice(0, 10)}</td>
      <td><Link href={`/activities/${encodeURIComponent(activity.id)}`}>{activity.source_name || activity.id.slice(0, 12)}</Link></td>
      <td>{activity.modality}</td>
      <td>{Math.round(activity.duration_s / 60)} min</td>
    </tr>)}
  </tbody></table></div>;
}
