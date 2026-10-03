export default function DashboardLoading() {
  return (
    <section aria-busy="true">
      <h1>Loading page…</h1>
      <p className="muted" role="status" aria-live="polite">Fetching your cycling data.</p>
      <div className="grid" aria-hidden="true">
        <div className="panel loading-skeleton" />
        <div className="panel loading-skeleton" />
        <div className="panel loading-skeleton" />
      </div>
    </section>
  );
}
