type Series = { name: string; values: Array<number | null>; color?: string };

function finite(values: Array<number | null>) {
  return values.filter((value): value is number => value !== null && Number.isFinite(value));
}

export function LineChart({ title, labels, series }: { title: string; labels: string[]; series: Series[] }) {
  const values = series.flatMap((item) => finite(item.values));
  if (!values.length || !labels.length) return <p className="muted">No chart data for this range.</p>;
  const min = Math.min(0, ...values);
  const max = Math.max(...values, min + 1);
  const width = 760;
  const height = 250;
  const pad = 24;
  const x = (index: number) => pad + (labels.length < 2 ? 0 : index * (width - 2 * pad)) / (labels.length - 1);
  const y = (value: number) => height - pad - ((value - min) / (max - min)) * (height - 2 * pad);

  return (
    <section className="panel chart-panel">
      <h2>{title}</h2>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>
        <line x1={pad} x2={width - pad} y1={height - pad} y2={height - pad} stroke="#cbd7d0" />
        <line x1={pad} x2={pad} y1={pad} y2={height - pad} stroke="#cbd7d0" />
        {series.map((item, seriesIndex) => {
          let drawing = false;
          const path = item.values.map((value, index) => {
            if (value === null || !Number.isFinite(value)) {
              drawing = false;
              return "";
            }
            const command = drawing ? "L" : "M";
            drawing = true;
            return `${command}${x(index)},${y(value)}`;
          }).filter(Boolean).join(" ");
          return <path key={item.name} d={path} fill="none" stroke={item.color ?? ["#167c52", "#dc7433", "#5966bd"][seriesIndex % 3]} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />;
        })}
      </svg>
      <div className="chart-legend">{series.map((item, index) => <span key={item.name}><i style={{ background: item.color ?? ["#167c52", "#dc7433", "#5966bd"][index % 3] }} />{item.name}</span>)}</div>
      <div className="chart-labels"><span>{labels[0]}</span><span>{labels.at(-1)}</span></div>
    </section>
  );
}

export function BarChart({ title, labels, values }: { title: string; labels: string[]; values: Array<number | null> }) {
  const max = Math.max(1, ...finite(values));
  return (
    <section className="panel chart-panel">
      <h2>{title}</h2>
      {values.every((value) => value === null) ? <p className="muted">No chart data for this range.</p> : (
        <div className="bars" role="img" aria-label={title}>
          {labels.map((label, index) => {
            const value = values[index];
            return <div className="bar-item" key={`${label}-${index}`} title={`${label}: ${value ?? "N/A"}`}>
              <span>{value === null ? "—" : Math.round(value)}</span>
              <div className="bar-track"><div className="bar-fill" style={{ height: value === null ? "0%" : `${Math.max(2, (value / max) * 100)}%` }} /></div>
              <small>{label}</small>
            </div>;
          })}
        </div>
      )}
    </section>
  );
}

export function MetricGrid({ values }: { values: Array<[string, string | number | null | undefined]> }) {
  return <div className="grid">{values.map(([label, value]) => <article className="panel" key={label}><p className="muted">{label}</p><strong className="metric-value">{value ?? "N/A"}</strong></article>)}</div>;
}

export function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return <details className="panel"><summary>{title}</summary><pre className="json-output">{JSON.stringify(value, null, 2)}</pre></details>;
}
