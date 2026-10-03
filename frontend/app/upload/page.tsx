import { createHash } from "node:crypto";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { UploadForm } from "@/components/upload-form";

export default async function UploadPage() {
  const session = await auth();
  if (!session?.user?.id) redirect("/");
  const owner = createHash("sha256").update(session.user.id).digest("hex");
  return (
    <>
      <h1>Upload an activity</h1>
      <p className="muted">Original files and GPS/sensor samples remain private to your account.</p>
      <UploadForm
        owner={owner}
        useBlob={Boolean(process.env.BLOB_READ_WRITE_TOKEN) && process.env.CYCLING_LOCAL_OBJECTS !== "1"}
      />
    </>
  );
}
