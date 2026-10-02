import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { AuthButtons } from "@/components/auth-buttons";

export default async function Home() {
  const session = await auth();

  if (!session?.user?.id) {
    return (
      <section className="panel" style={{ maxWidth: 560, margin: "10vh auto" }}>
        <p className="muted">Private training workspace</p>
        <h1>Analyze your cycling data</h1>
        <p className="muted">Sign in with an invited Google account to upload FIT/TCX files and view your own metrics.</p>
        <AuthButtons />
      </section>
    );
  }

  redirect("/overview");
}
