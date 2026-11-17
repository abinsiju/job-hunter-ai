"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { IconLogo } from "@/components/icons";
import { Button } from "@/components/ui";

function Form() {
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const r = await fetch("/api/session", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, password }) });
      if (!r.ok) { setError(((await r.json().catch(() => null)) as { message?: string } | null)?.message ?? "Sign in failed."); setBusy(false); return; }
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
      router.refresh();
    } catch { setError("Can't reach the Job Hunter service."); setBusy(false); }
  }

  return (
    <form className="login-form" onSubmit={submit}>
      <div className="field"><label htmlFor="u">Username</label><input id="u" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} /></div>
      <div className="field"><label htmlFor="p">Password</label><input id="p" className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></div>
      <div className="login-err" role="alert" aria-live="polite">{error}</div>
      <Button type="submit" size="lg" block busy={busy} disabled={!password}>Sign in</Button>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-mark"><IconLogo /></div>
        <h1>Job Hunter</h1>
        <p>Your private job search, running on this Mac.</p>
        <Suspense><Form /></Suspense>
      </div>
    </div>
  );
}
