import { HR_ZONE_COLORS, type HRPoint } from "@/lib/power-hr";

export type PowerHRSeries = { name: string; comparison?: boolean; pointsOnly?: boolean; points: Array<HRPoint & { x: number; detail?: string }> };

export function PowerHRChart({ title, series, bounds = [], timeAxis = false }: {
  title: string;
  series: PowerHRSeries[];
  bounds?: number[];
  timeAxis?: boolean;
}) {
  const usable = (point: HRPoint & { x: number }) => Number.isFinite(point.x) && point.hr_median_bpm !== null && Number.isFinite(point.hr_median_bpm);
  const points = series.flatMap((item) => item.points.filter(usable));
  const width = 800, height = 310, left = 62, right = 88, top = 24, bottom = 54;
  const xs = points.length ? points.map((point) => point.x) : [0];
  const hrs = [...bounds, ...points.flatMap((point) => [point.hr_median_bpm, point.hr_p25_bpm, point.hr_p75_bpm].filter((value): value is number => value !== null && Number.isFinite(value)))];
  if (!hrs.length) hrs.push(100, 180);
  const minX = timeAxis ? Math.min(...xs) : Math.max(0, Math.min(...xs) - 10);
  const maxX = Math.max(minX + (timeAxis ? 86400000 : 20), ...xs);
  const minY = Math.max(0, Math.floor((Math.min(...hrs) - 5) / 10) * 10);
  const maxY = Math.max(minY + 10, Math.ceil((Math.max(...hrs) + 5) / 10) * 10);
  const x = (value: number) => left + (value - minX) / (maxX - minX) * (width - left - right);
  const y = (value: number) => height - bottom - (value - minY) / (maxY - minY) * (height - top - bottom);
  const formatX = (value: number) => timeAxis ? new Date(value).toISOString().slice(0, 10) : String(Math.round(value));
  const colors = ["#17231f", "#167c52", "#5966bd", "#b65d21", "#8d3478", "#246c83"];
  const zoneName = (index: number) => bounds.length === 6 && index >= 4 ? ["Z5a", "Z5b", "Z5c"][index - 4] : `Z${index + 1}`;
  const low = points.filter((point) => point.status && point.status !== "ok").length;
  return <section className="panel chart-panel">
    <h2>{title}</h2>
    {!points.length ? <p className="muted">No eligible paired power/HR samples for this selection.</p> : <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. ${timeAxis ? "Date" : "Power in watts"} on X, heart rate in bpm on Y. Points show median; vertical bars show P25–P75. Sample evidence follows.`}>
      <title>{title}</title>
      <desc>Solid curves: current selection. Dashed curves: comparison. Hollow points: low confidence. Horizontal colored lines mark reference HR zone boundaries.</desc>
      {bounds.length > 0 && Array.from({ length: bounds.length + 1 }, (_, index) => {
        const lower = Math.max(minY, bounds[index - 1] ?? minY);
        const upper = Math.min(maxY, bounds[index] ?? maxY);
        return upper > lower && <rect key={`zone-${index}`} x={left} y={y(upper)} width={width - left - right} height={y(lower) - y(upper)} fill={HR_ZONE_COLORS[index % HR_ZONE_COLORS.length]} opacity="0.09" />;
      })}
      {Array.from({ length: 5 }, (_, index) => {
        const value = minY + (maxY - minY) * index / 4;
        return <g key={`y-${index}`}><line x1={left} x2={width - right} y1={y(value)} y2={y(value)} stroke="#dce5e0" /><text x={left - 8} y={y(value) + 4} textAnchor="end" fontSize="12">{Math.round(value)}</text></g>;
      })}
      {bounds.map((bound, index) => <g key={bound}>
        <line x1={left} x2={width - right} y1={y(bound)} y2={y(bound)} stroke={HR_ZONE_COLORS[(index + 1) % HR_ZONE_COLORS.length]} strokeWidth="2" />
         <text x={width - right + 6} y={y(bound) + 4} fontSize="11">{zoneName(index)} / {zoneName(index + 1)} · {bound} bpm</text>
      </g>)}
      <line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} stroke="#64736d" />
      <line x1={left} x2={left} y1={top} y2={height - bottom} stroke="#64736d" />
      {[0, 0.5, 1].map((fraction) => <text key={fraction} x={x(minX + (maxX - minX) * fraction)} y={height - bottom + 20} textAnchor="middle" fontSize="11">{formatX(minX + (maxX - minX) * fraction)}</text>)}
      <text x={(left + width - right) / 2} y={height - 8} textAnchor="middle" fontSize="13">{timeAxis ? "Date" : "Power (W)"}</text>
      <text transform={`translate(16 ${(top + height - bottom) / 2}) rotate(-90)`} textAnchor="middle" fontSize="13">Heart rate (bpm)</text>
      {series.map((item, index) => {
         const color = colors[(timeAxis ? Math.floor(index / 2) : index) % colors.length];
        let drawing = false;
        const sorted = [...item.points].sort((a, b) => a.x - b.x);
        const path = sorted.map((point) => {
          if (!usable(point)) { drawing = false; return ""; }
          const command = drawing ? "L" : "M";
          drawing = true;
          return `${command}${x(point.x)},${y(point.hr_median_bpm!)}`;
        }).join(" ");
         return <g key={item.name}>
           {!item.pointsOnly && <path d={path} fill="none" stroke={color} strokeWidth="2" strokeDasharray={item.comparison ? "7 5" : undefined} />}
          {sorted.filter(usable).map((point, pointIndex) => <g key={`${point.x}-${pointIndex}`}>
            <title>{`${item.name}: ${formatX(point.x)}${timeAxis ? "" : " W"}; median ${point.hr_median_bpm} bpm; P25–P75 ${point.hr_p25_bpm ?? "N/A"}–${point.hr_p75_bpm ?? "N/A"} bpm; ${point.activity_count ?? 1} activities; ${point.valid_seconds} valid seconds; ${point.status ?? "ok"}. ${point.detail ?? ""}`}</title>
            {point.hr_p25_bpm !== null && point.hr_p75_bpm !== null && Number.isFinite(point.hr_p25_bpm) && Number.isFinite(point.hr_p75_bpm) && <line x1={x(point.x)} x2={x(point.x)} y1={y(point.hr_p25_bpm)} y2={y(point.hr_p75_bpm)} stroke={color} strokeWidth="5" opacity="0.45" />}
            <circle cx={x(point.x)} cy={y(point.hr_median_bpm!)} r="3.5" fill={point.status && point.status !== "ok" ? "white" : color} stroke={color} strokeWidth="1.5" />
          </g>)}
        </g>;
      })}
    </svg>}
    <div className="chart-legend">{series.map((item, index) => <span key={item.name}><i style={{ background: colors[(timeAxis ? Math.floor(index / 2) : index) % colors.length] }} />{item.name} ({item.pointsOnly ? "activity" : item.comparison ? "dashed" : "solid"})</span>)}</div>
     {bounds.length > 0 && <div className="chart-legend">{Array.from({ length: bounds.length + 1 }, (_, index) => <span key={index}><i style={{ background: HR_ZONE_COLORS[index % HR_ZONE_COLORS.length] }} />{zoneName(index)}</span>)}</div>}
    <p className="muted">Median and P25–P75. {low} low-confidence points (hollow); {points.length} plotted points. Unavailable values are not interpolated.</p>
    <details className="table-wrap"><summary>Sample evidence ({series.reduce((total, item) => total + item.points.length, 0)} points)</summary>
       <table><caption>{title} — sample evidence</caption><thead><tr><th scope="col">Series</th><th scope="col">{timeAxis ? "Date" : "Power (W)"}</th><th scope="col">Median HR</th><th scope="col">P25–P75 HR</th><th scope="col">W / bpm</th><th scope="col">Activities</th><th scope="col">Valid seconds</th><th scope="col">Status / context</th></tr></thead><tbody>
         {series.flatMap((item) => item.points.map((point, index) => <tr key={`${item.name}-${index}`}><th scope="row">{item.name}</th><td>{Number.isFinite(point.x) ? formatX(point.x) : "N/A"}</td><td>{point.hr_median_bpm ?? "N/A"}</td><td>{point.hr_p25_bpm ?? "N/A"}–{point.hr_p75_bpm ?? "N/A"}</td><td>{point.efficiency_w_per_bpm?.toFixed(2) ?? "N/A"}</td><td>{point.activity_count ?? 1}</td><td>{point.valid_seconds}</td><td>{point.status ?? "ok"}{point.confidence_reasons?.length ? ` (${point.confidence_reasons.join(", ")})` : ""}{point.detail ? ` · ${point.detail}` : ""}</td></tr>))}
      </tbody></table>
    </details>
  </section>;
}
