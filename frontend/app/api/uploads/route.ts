import { createHash } from "node:crypto";
import { handleUpload, type HandleUploadBody } from "@vercel/blob/client";
import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { isActiveGoogleUser } from "@/lib/google-login";
import { internalToken } from "@/lib/internal-token";

const allowedExtension = /\.(fit|tcx)(\.gz)?$/i;
const maxUploadBytes = Number(process.env.MAX_UPLOAD_BYTES ?? 25_000_000);

export async function POST(request: Request) {
  const body = (await request.json()) as HandleUploadBody;
  try {
    const response = await handleUpload({
      body,
      request,
      onBeforeGenerateToken: async (pathname, clientPayload) => {
        const session = await auth();
        if (!session?.user?.id) throw new Error("Sign in required");
        if (!(await isActiveGoogleUser(session.user.id))) throw new Error("Account is disabled");
        const owner = createHash("sha256").update(session.user.id).digest("hex");
        let originalFilename = "";
        try {
          originalFilename = String(JSON.parse(clientPayload ?? "{}").filename ?? "");
        } catch {
          throw new Error("Invalid upload filename");
        }
        const filenameSuffix = originalFilename.match(/\.(fit|tcx)(\.gz)?$/i)?.[0]?.toLowerCase();
        const pathSuffix = pathname.match(/\.(fit|tcx)(\.gz)?$/i)?.[0]?.toLowerCase();
        if (
          !pathname.startsWith(`users/${owner}/originals/`) ||
          !/users\/[0-9a-f]{64}\/originals\/[0-9a-f]{32}\.(fit|tcx)(\.gz)?$/i.test(pathname) ||
          !allowedExtension.test(pathname) ||
          !filenameSuffix ||
          filenameSuffix !== pathSuffix ||
          originalFilename.length > 255 ||
          originalFilename.includes("/") ||
          originalFilename.includes("\\") ||
          originalFilename.includes("\0")
        ) {
          throw new Error("Invalid upload path or file type");
        }
        return {
          allowedContentTypes: [
            "application/octet-stream",
            "application/xml",
            "text/xml",
            "application/gzip",
            "application/x-gzip",
          ],
          maximumSizeInBytes: maxUploadBytes,
          addRandomSuffix: false,
          tokenPayload: JSON.stringify({ userId: session.user.id, owner, filename: originalFilename }),
        };
      },
      onUploadCompleted: async ({ blob, tokenPayload }) => {
        const payload = JSON.parse(tokenPayload ?? "{}") as {
          userId?: string;
          owner?: string;
          filename?: string;
        };
        if (!payload.userId || !payload.owner || !payload.filename) throw new Error("Invalid upload owner");
        if (!(await isActiveGoogleUser(payload.userId))) throw new Error("Account is disabled");
        const expectedPrefix = `users/${payload.owner}/originals/`;
        const objectSuffix = blob.pathname.match(/\.(fit|tcx)(\.gz)?$/i)?.[0]?.toLowerCase();
        const filenameSuffix = payload.filename.match(/\.(fit|tcx)(\.gz)?$/i)?.[0]?.toLowerCase();
        if (
          !blob.pathname.startsWith(expectedPrefix) ||
          !/users\/[0-9a-f]{64}\/originals\/[0-9a-f]{32}\.(fit|tcx)(\.gz)?$/i.test(blob.pathname) ||
          !allowedExtension.test(payload.filename) ||
          objectSuffix !== filenameSuffix
        ) {
          throw new Error("Completed upload does not match authorized object");
        }
        const api = process.env.CYCLING_API_URL;
        if (!api) throw new Error("Cycling API is not configured");
        const result = await fetch(new URL("/api/v1/uploads/process", api), {
          method: "POST",
          headers: {
            authorization: `Bearer ${await internalToken(payload.userId)}`,
            "content-type": "application/json",
          },
          body: JSON.stringify({ filename: payload.filename, object_key: blob.pathname }),
          cache: "no-store",
        });
        if (!result.ok) throw new Error(`Activity processing failed (${result.status})`);
      },
    });
    return NextResponse.json(response);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Upload failed" },
      { status: 400 },
    );
  }
}
