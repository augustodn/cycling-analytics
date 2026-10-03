"use client";

import { signIn, signOut } from "next-auth/react";

export function AuthButtons({ signedIn = false }: { signedIn?: boolean }) {
  return signedIn ? (
    <button className="button secondary" onClick={() => signOut({ callbackUrl: "/" })}>
      Sign out
    </button>
  ) : (
    <button className="button" onClick={() => signIn("google", { callbackUrl: "/" })}>
      Sign in with Google
    </button>
  );
}
