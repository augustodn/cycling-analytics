import { postgresPool } from "@/lib/postgres";

export async function isActiveGoogleUser(userId: string): Promise<boolean> {
  const result = await postgresPool().query(
    "SELECT 1 FROM cycling_users WHERE user_id=$1 AND is_active=true AND email_verified=true",
    [userId],
  );
  return result.rowCount === 1;
}

export async function admitGoogleUser(profile: {
  sub: string;
  email: string;
  emailVerified: boolean;
}): Promise<boolean> {
  const googleSub = profile.sub.trim();
  const email = profile.email.trim().toLowerCase();
  if (!googleSub || !email || !profile.emailVerified || googleSub.length > 256) return false;

  const pool = postgresPool();
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const existing = await client.query<{ user_id: string; is_active: boolean }>(
      "SELECT user_id,is_active FROM cycling_users WHERE google_sub = $1 FOR UPDATE",
      [googleSub],
    );
    if (existing.rowCount) {
      if (!existing.rows[0].is_active) {
        await client.query("ROLLBACK");
        return false;
      }
      await client.query(
        "UPDATE cycling_users SET email = $2, last_login_at = clock_timestamp() WHERE user_id = $1",
        [existing.rows[0].user_id, email],
      );
      await client.query("COMMIT");
      return true;
    }

    const invite = await client.query<{ email: string }>(
      "SELECT email FROM cycling_invites WHERE email = $1 AND accepted_user_id IS NULL FOR UPDATE",
      [email],
    );
    if (!invite.rowCount) {
      await client.query("ROLLBACK");
      return false;
    }

    await client.query(
      "INSERT INTO cycling_users (user_id, google_sub, email, email_verified) VALUES ($1, $1, $2, true)",
      [googleSub, email],
    );
    const accepted = await client.query(
      "UPDATE cycling_invites SET accepted_user_id = $2, accepted_at = clock_timestamp() " +
        "WHERE email = $1 AND accepted_user_id IS NULL",
      [email, googleSub],
    );
    if (accepted.rowCount !== 1) throw new Error("Invite was claimed concurrently");
    await client.query("COMMIT");
    return true;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    if ((error as { code?: string }).code === "23505") return false;
    throw error;
  } finally {
    client.release();
  }
}
