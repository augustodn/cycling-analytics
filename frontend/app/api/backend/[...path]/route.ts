import { auth } from "@/auth";
import { internalToken } from "@/lib/internal-token";

type RouteContext = { params: Promise<{ path: string[] }> };

async function forward(request: Request, context: RouteContext) {
  const session = await auth();
  if (!session?.user?.id) return Response.json({ detail: "Sign in required" }, { status: 401 });

  const { path } = await context.params;
  if (path.some((part) => !/^[a-zA-Z0-9_-]+$/.test(part))) {
    return Response.json({ detail: "Invalid API path" }, { status: 400 });
  }
  const base = process.env.CYCLING_API_URL;
  if (!base) return Response.json({ detail: "Cycling API is not configured" }, { status: 503 });

  const target = new URL(`/api/v1/${path.join("/")}`, base);
  target.search = new URL(request.url).search;
  const token = await internalToken(session.user.id, session.user.email);
  const method = request.method;
  const response = await fetch(target, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      ...(request.headers.get("content-type") ? { "content-type": request.headers.get("content-type")! } : {}),
    },
    body: method === "GET" || method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
  });
}

export const GET = forward;
export const POST = forward;
export const DELETE = forward;
