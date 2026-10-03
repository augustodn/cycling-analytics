import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { admitGoogleUser } from "@/lib/google-login";

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  session: { strategy: "jwt" },
  providers: [Google],
  callbacks: {
    async signIn({ account, profile }) {
      if (account?.provider !== "google" || !profile) return false;
      const googleProfile = profile as typeof profile & {
        sub?: string;
        email_verified?: boolean;
      };
      if (typeof googleProfile.email !== "string" || typeof googleProfile.sub !== "string") {
        return false;
      }
      return admitGoogleUser({
        sub: googleProfile.sub,
        email: googleProfile.email,
        emailVerified: googleProfile.email_verified === true,
      });
    },
    async jwt({ token, account }) {
      if (account?.provider === "google") token.sub = account.providerAccountId;
      return token;
    },
    async session({ session, token }) {
      if (session.user && token.sub) session.user.id = token.sub;
      return session;
    },
  },
});
