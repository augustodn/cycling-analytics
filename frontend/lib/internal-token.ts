import { SignJWT } from "jose";

const issuer = "cycling-nextjs";
const audience = "cycling-fastapi";

export async function internalToken(userId: string, email?: string | null) {
  const secret = process.env.INTERNAL_API_SECRET;
  if (!secret || Buffer.byteLength(secret) < 32) {
    throw new Error("INTERNAL_API_SECRET must contain at least 32 bytes");
  }
  return new SignJWT({ email: email ?? null })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuer(issuer)
    .setAudience(audience)
    .setSubject(userId)
    .setIssuedAt()
    .setExpirationTime("60s")
    .sign(new TextEncoder().encode(secret));
}
