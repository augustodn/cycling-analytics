import { ActivityTable, type Activity } from "@/components/activity-table";
import { cyclingApi, type ToolResult } from "@/lib/cycling-api";

export default async function ActivitiesPage() {
  const result = await cyclingApi<ToolResult<{ activities: Activity[] }>>("activities");
  return <><h1>Activities</h1><section className="panel"><ActivityTable activities={result.data.activities} /></section></>;
}
