import { MetricGrid } from "@/components/charts";
import { cyclingApi, type ToolResult } from "@/lib/cycling-api";

export default async function FtpCalibrationPage() {
  const result = await cyclingApi<ToolResult>("power-hr-zone-mismatch?period=90d&environment=outdoor");
  const data = result.data as Record<string, unknown>;
  const points = (data.points as unknown[] | undefined) ?? [];
  return <>
    <h1>FTP calibration evidence</h1>
    <p className="muted">Zone mismatch is an accumulating comparison, not an FTP test. Do not adjust FTP from this signal alone.</p>
    <MetricGrid values={[
      ["Declared FTP", data.declared_ftp_w == null ? "N/A" : `${Number(data.declared_ftp_w).toFixed(0)} W`],
      ["30-day zone mismatch", data.mismatch_30d == null ? "N/A" : Number(data.mismatch_30d).toFixed(2)],
      ["Valid windows", Number(data.valid_windows ?? 0)],
      ["Data confidence", String(data.data_confidence ?? "LOW")],
    ]} />
    <p className="muted">{points.length} valid power/HR windows. Windows require ≥90% paired sensor coverage.</p>
  </>;
}
