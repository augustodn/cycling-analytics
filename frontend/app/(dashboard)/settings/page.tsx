import { cyclingApi, type ToolResult } from "@/lib/cycling-api";
import { ProfileForm, type Parameters } from "@/components/profile-form";

export default async function SettingsPage() {
  const result = await cyclingApi<ToolResult<{ parameters: Array<{ id: string; effective_date: string; settings: Parameters }> }>>("parameters");
  const history = result.data.parameters;
  const current = history.at(-1)?.settings;
  return <>
    <h1>Athlete settings</h1>
    <p className="muted">Settings are personal and append-only. Historical activities use the profile effective on that date.</p>
    <ProfileForm current={current} />
    <section className="panel"><h2>Parameter history</h2>{history.length ? <ul>{history.map((item) => <li key={item.id}>{item.effective_date} · FTP {item.settings.ftp_w} W · LTHR {item.settings.lthr_bpm} bpm</li>)}</ul> : <p className="muted">No profile yet. Add your thresholds before relying on zone-based metrics.</p>}</section>
  </>;
}
