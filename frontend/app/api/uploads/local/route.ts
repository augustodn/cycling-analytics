import { auth } from "@/auth";
import { internalToken } from "@/lib/internal-token";

const maxUploadBytes = Number(process.env.MAX_UPLOAD_BYTES ?? 25_000_000);

export async function POST(request: Request) {
  if (process.env.NODE_ENV !== "development") {
    return Response.json({ detail: "Local upload adapter is disabled" }, { status: 404 });
  }
  const session = await auth();
  if (!session?.user?.id) return Response.json({ detail: "Sign in required" }, { status: 401 });
  const form = await request.formData();
  const file = form.get("file");
  if (!(file instanceof File) || file.size < 1 || file.size > maxUploadBytes) {
    return Response.json({ detail: "Choose a nonempty FIT/TCX file within the size limit" }, { status: 413 });
  }
  if (!/\.(fit|tcx)(\.gz)?$/i.test(file.name)) {
    return Response.json({ detail: "Only FIT and TCX files are supported" }, { status: 415 });
  }
  const api = process.env.CYCLING_API_URL;
  if (!api) return Response.json({ detail: "Cycling API is not configured" }, { status: 503 });
  const token = await internalToken(session.user.id, session.user.email);
  const localForm = new FormData();
  localForm.set("file", file);
  const result = await fetch(new URL("/api/v1/uploads", api), {
    method: "POST",
    headers: { authorization: `Bearer ${token}` },
    body: localForm,
    cache: "no-store",
  });
  return new Response(result.body, {
    status: result.status,
    headers: { "content-type": result.headers.get("content-type") ?? "application/json" },
  });
}
