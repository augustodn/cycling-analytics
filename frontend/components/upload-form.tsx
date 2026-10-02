"use client";

import { upload } from "@vercel/blob/client";
import { useState } from "react";

export function UploadForm({ owner, useBlob }: { owner: string; useBlob: boolean }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(form: FormData) {
    const file = form.get("file");
    if (!(file instanceof File)) return;
    setBusy(true);
    setMessage("Uploading activity…");
    try {
      if (!useBlob) {
        const local = new FormData();
        local.set("file", file);
        const response = await fetch("/api/uploads/local", { method: "POST", body: local });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail ?? "Upload failed");
      } else {
        const extension = file.name.match(/\.(fit|tcx)(\.gz)?$/i)?.[0]?.toLowerCase();
        if (!extension) throw new Error("Only FIT and TCX files are supported");
        const pathname = `users/${owner}/originals/${crypto.randomUUID().replaceAll("-", "")}${extension}`;
        await upload(pathname, file, {
          access: "private",
          handleUploadUrl: "/api/uploads",
          clientPayload: JSON.stringify({ filename: file.name }),
        });
      }
      setMessage("Activity processed. Refresh to see it in your activity list.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form action={submit} className="panel" style={{ display: "grid", gap: ".75rem", maxWidth: 560 }}>
      <label htmlFor="activity-file">Activity file (.fit, .fit.gz, .tcx, .tcx.gz)</label>
      <input id="activity-file" name="file" type="file" accept=".fit,.fit.gz,.tcx,.tcx.gz" required disabled={busy} />
      <button className="button" disabled={busy}>{busy ? "Processing…" : "Upload activity"}</button>
      <p aria-live="polite" className="muted">{message}</p>
    </form>
  );
}
