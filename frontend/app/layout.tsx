import type { Metadata } from "next";
import Link from "next/link";
import { auth } from "@/auth";
import { AuthButtons } from "@/components/auth-buttons";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cycling analytics",
  description: "Private cycling training analysis",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <header className="topbar">
            <Link className="brand" href="/">Cycling analytics</Link>
            <AuthButtons signedIn={Boolean(session?.user?.id)} />
          </header>
          <main className="content">{children}</main>
        </div>
      </body>
    </html>
  );
}
