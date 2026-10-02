import { auth } from "@/auth";
import { internalToken } from "@/lib/internal-token";

export interface ToolResult<T = Record<string, unknown>> {
  operation: string;
  algorithm_version: string;
  parameter_id: string | null;
  parameter_mode: string | null;
  computed_at: string;
  data: T;
}

export async function cyclingApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const session = await auth();
  if (!session?.user?.id) throw new Error("Sign in required");
  const base = process.env.CYCLING_API_URL;
  if (!base) throw new Error("Cycling API is not configured");
  const safePath = path.replace(/^\/+/, "");
  const response = await fetch(new URL(`/api/v1/${safePath}`, base), {
    ...init,
    headers: {
      authorization: `Bearer ${await internalToken(session.user.id, session.user.email)}`,
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...init.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.detail ?? `Cycling API failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function postJson(path: string, payload: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(payload) };
}
