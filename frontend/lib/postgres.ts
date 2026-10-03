import { Pool } from "pg";

declare global {
  var cyclingPgPool: Pool | undefined;
}

export function postgresPool() {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error("DATABASE_URL is required");
  globalThis.cyclingPgPool ??= new Pool({
    connectionString,
    max: 2,
    idleTimeoutMillis: 10_000,
    connectionTimeoutMillis: 5_000,
  });
  return globalThis.cyclingPgPool;
}
