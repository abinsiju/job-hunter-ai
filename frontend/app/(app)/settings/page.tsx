"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { IconLink, IconLock, IconPerson, IconShield } from "@/components/icons";
import { PageTop } from "@/components/shared";
import { Button, Callout, ErrorNote, Pill, Row, Skeleton, useToast } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import type { SystemStatus } from "@/lib/types";

const RULES = [
  "Nothing is ever submitted for you. You apply yourself, then record it with proof.",
  "A vacancy you already applied to is blocked. The same employer with a different role is only a warning.",
  "Resumes and letters use only facts you verified. No model writes the text.",
  "Citizenship, PR or clearance requirements exclude a job. Work rights never appear on documents.",
  "Ads that ask you to pay a fee are excluded. Internships with no stated pay are flagged.",
  "Previews (such as Adzuna's) can never show as a clear match until you read the full ad.",
  "Where possible each job is checked against the employer's own ad. If the two disagree on work rights, you see both quotes before anything else.",
  "Form filling stops before Submit. It never touches CAPTCHAs, logins, SEEK, LinkedIn or Indeed, and Gmail only ever gets drafts.",
];

function Dot({ ok, idle }: { ok: boolean; idle?: boolean }) { return <span className={`status-dot ${ok ? "" : idle ? "idle" : "off"}`} aria-label={ok ? "Running" : "Not running"} />; }

export default function Settings() {
  const router = useRouter();
  const toast = useToast();
  const { data: s, error, reload } = useApi<SystemStatus>("/system", { refreshMs: 15000 });
  const { data: blocked, reload: reloadBlocked } = useApi<{ employer: string; reason: string }[]>("/blocklist");
  const [name, setName] = useState("");

  async function signOut() { await fetch("/api/session", { method: "DELETE" }); router.replace("/login"); router.refresh(); }
  async function block() {
    try { await api("/blocklist", { method: "POST", json: { employer: name } }); setName(""); reloadBlocked(); toast("Employer blocked. Re-score on the Jobs page to apply it."); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function unblock(employer: string) {
    try { await api(`/blocklist/${encodeURIComponent(employer)}`, { method: "DELETE" }); reloadBlocked(); toast("Removed from the blocklist"); }
    catch (e) { toast((e as Error).message, "err"); }
  }

  return (
    <>
      <PageTop title="Settings" />
      <ErrorNote error={error} retry={reload} />

      <h2 className="section-title">Account</h2>
      <div className="list">
        <Row href="/sources" lead={<div style={{ width: 40, height: 40, borderRadius: 12, background: "var(--blue-tint)", color: "var(--blue)", display: "grid", placeItems: "center" }}><IconLink style={{ width: 22, height: 22 }} /></div>} title="Job sources" sub="SEEK, Indeed, Jora and the rest: what each one really does" />
        <Row href="/profile" lead={<div style={{ width: 40, height: 40, borderRadius: 12, background: "var(--blue-tint)", color: "var(--blue)", display: "grid", placeItems: "center" }}><IconPerson style={{ width: 22, height: 22 }} /></div>} title="Your profile" sub="The verified facts behind every document" />
        <div className="row"><div className="row-main"><div className="row-title">Signed in as admin</div><div className="row-sub">Your session ends after 8 hours</div></div><Button variant="gray" size="sm" onClick={signOut}>Sign out</Button></div>
      </div>

      <h2 className="section-title">System</h2>
      <div className="list">
        {!s ? <div className="row"><Skeleton h={16} w="50%" /></div> : <>
          <div className="row"><Dot ok={s.services.database} /><div className="row-main"><div className="row-title">Database</div><div className="row-sub">PostgreSQL, localhost only</div></div></div>
          <div className="row"><Dot ok={s.services.redis} /><div className="row-main"><div className="row-title">Queue</div><div className="row-sub">Redis, localhost only</div></div></div>
          <div className="row"><Dot ok={s.services.ollama.ok} /><div className="row-main"><div className="row-title">Local AI</div><div className="row-sub">{s.services.ollama.ok ? `Ollama ${s.services.ollama.version} · ${s.services.ollama.loaded.length ? `${s.services.ollama.loaded[0]} loaded` : "model loads on first use"}` : "Not running. Start it with scripts/start.sh"}</div></div></div>
          <div className="row"><Dot ok={s.mail.configured} idle /><div className="row-main"><div className="row-title">Gmail drafts</div><div className="row-sub">{s.mail.configured ? `Connected as ${s.mail.account}. Creates drafts only: nothing is ever sent for you.` : "Not connected. Run scripts/set_gmail.sh in Terminal once to save a Gmail app password in your Keychain."}</div></div></div>
          <div className="row"><Dot ok={s.discovery.adzuna_configured} idle /><div className="row-main"><div className="row-title">Adzuna search</div><div className="row-sub">{s.discovery.adzuna_configured ? "Keys are set" : "Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env"}</div></div></div>
        </>}
      </div>
      {s && <div className="list" style={{ marginTop: 12 }}>
        <div className="row"><div className="row-main"><div className="row-title">Model</div><div className="row-sub">{s.ai.chat_model} · {s.ai.embed_model} · {s.ai.context.toLocaleString()} token context · reads job ads only, never writes your documents</div></div></div>
        <div className="row"><div className="row-main"><div className="row-title">Stored on this Mac</div><div className="row-sub">{s.counts.listings} jobs · {s.counts.applications} applications · {s.counts.facts} profile facts · {s.counts.generated_folders} document sets · {s.disk_free_gb} GB free</div></div></div>
        <div className="row"><div className="row-main"><div className="row-title">Backup</div><div className="row-sub">{s.backup_hint}</div></div></div>
      </div>}

      <h2 className="section-title">Blocked employers</h2>
      <div className="list">
        {blocked && blocked.length > 0 ? blocked.map((b) => (
          <div className="row" key={b.employer}><div className="row-main"><div className="row-title">{b.employer}</div>{b.reason && <div className="row-sub">{b.reason}</div>}</div><Button variant="plain" size="sm" onClick={() => unblock(b.employer)}>Remove</Button></div>
        )) : <div className="row muted">No blocked employers. Block one from any job&apos;s page.</div>}
        <div className="row"><input className="input" placeholder="Block an employer by name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Employer to block" onKeyDown={(e) => { if (e.key === "Enter" && name.trim()) block(); }} /><Button variant="tint" disabled={!name.trim()} onClick={block}>Block</Button></div>
      </div>

      <h2 className="section-title">Your safety rules</h2>
      <div className="card card-pad stack">{RULES.map((r) => <div className="row-flex" key={r} style={{ alignItems: "flex-start", gap: 12 }}><IconShield style={{ width: 20, height: 20, color: "var(--green)", flex: "none", marginTop: 2 }} /><span>{r}</span></div>)}</div>

      <h2 className="section-title">Use it on your phone</h2>
      <div className="card card-pad stack">
        <div className="row-flex" style={{ gap: 12, alignItems: "flex-start" }}><IconLock style={{ width: 20, height: 20, flex: "none", marginTop: 2 }} /><div>
          <div style={{ fontWeight: 600 }}>Private access through Tailscale</div>
          <div className="muted">This app only listens on this Mac. To reach it from your phone without exposing it to the internet, install Tailscale on both devices and sign in to the same account. Set a long password first.</div></div></div>
        <Callout tone="warn">Tailscale is not installed yet. When your Mac sleeps or is off, the app and any running searches are unavailable.</Callout>
        <div className="muted small">Then open the Share menu on your phone and choose <b>Add to Home Screen</b> to install Job Hunter like an app.</div>
      </div>

      <p className="muted small" style={{ textAlign: "center", marginTop: 28 }}>Job Hunter · runs entirely on your Mac · <Link className="link" href="/profile">Profile</Link></p>
      {s && <Pill>{s.env}</Pill>}
    </>
  );
}
