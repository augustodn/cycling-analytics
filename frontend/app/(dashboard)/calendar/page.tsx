import { ActivityTable, type Activity } from "@/components/activity-table";
import { cyclingApi, type ToolResult } from "@/lib/cycling-api";

export default async function CalendarPage() {
  const result = await cyclingApi<ToolResult<{ activities: Activity[] }>>("activities");
  return <><h1>Calendar</h1><p className="muted">Activities grouped by date, newest first.</p><section className="panel"><ActivityTable activities={result.data.activities} /></section></>;
}
