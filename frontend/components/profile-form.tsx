"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export type Parameters = {
  ftp_w: number;
  lthr_bpm: number;
  max_hr_bpm: number;
  weight_kg: number;
  hr_zone_bounds: number[];
  power_zone_fractions: number[];
  ctl_days: number;
  atl_days: number;
};

export function ProfileForm({ current }: { current?: Parameters }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const numbers = (name: string) => String(form.get(name) ?? "").split(",").map((value) => Number(value.trim()));
    const payload = {
      effective_date: String(form.get("effective_date")),
      ftp_w: Number(form.get("ftp_w")),
      lthr_bpm: Number(form.get("lthr_bpm")),
      max_hr_bpm: Number(form.get("max_hr_bpm")),
      weight_kg: Number(form.get("weight_kg")),
      hr_zone_bounds: numbers("hr_zone_bounds"),
      power_zone_fractions: numbers("power_zone_fractions"),
      ctl_days: Number(form.get("ctl_days")),
      atl_days: Number(form.get("atl_days")),
      hr_load_factor: Number(form.get("hr_load_factor")),
      notes: String(form.get("notes") ?? "Declared athlete settings"),
    };
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/backend/parameters", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not save settings");
      setMessage("Settings saved.");
      router.refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not save settings");
    } finally {
      setBusy(false);
    }
  }

  return <form className="panel form-grid" onSubmit={submit}>
    <label>Effective date<input name="effective_date" type="date" defaultValue={new Date().toISOString().slice(0, 10)} required /></label>
    <label>FTP (W)<input name="ftp_w" type="number" min="1" max="1000" step="1" defaultValue={current?.ftp_w} required /></label>
    <label>LTHR (bpm)<input name="lthr_bpm" type="number" min="1" max="240" step="1" defaultValue={current?.lthr_bpm} required /></label>
    <label>Maximum HR (bpm)<input name="max_hr_bpm" type="number" min="1" max="250" step="1" defaultValue={current?.max_hr_bpm} required /></label>
    <label>Weight (kg)<input name="weight_kg" type="number" min="1" max="300" step="0.1" defaultValue={current?.weight_kg} required /></label>
    <label>HR zone upper bounds (bpm, comma-separated)<input name="hr_zone_bounds" placeholder="127, 141, 147, 158" defaultValue={current?.hr_zone_bounds.join(", ")} required /></label>
    <label>Power zone fractions (comma-separated)<input name="power_zone_fractions" defaultValue={current?.power_zone_fractions.join(", ") ?? "0.55, 0.75, 0.90, 1.05, 1.20"} required /></label>
    <label>CTL days<input name="ctl_days" type="number" min="1" max="365" step="1" defaultValue={current?.ctl_days ?? 42} required /></label>
    <label>ATL days<input name="atl_days" type="number" min="1" max="365" step="1" defaultValue={current?.atl_days ?? 7} required /></label>
    <label>HR load factor<input name="hr_load_factor" type="number" min="0.01" max="5" step="0.01" defaultValue={1} required /></label>
    <label className="wide">Notes<input name="notes" maxLength={500} defaultValue="Declared athlete settings" /></label>
    <button className="button" disabled={busy}>{busy ? "Saving…" : "Save settings"}</button>
    <p className="muted" aria-live="polite">{message}</p>
  </form>;
}
