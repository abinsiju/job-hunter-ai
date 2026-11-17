"use client";
import { IconLock, IconShield } from "@/components/icons";
import { PageTop } from "@/components/shared";
import { Callout, ErrorNote, Pill, Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Fact, Profile } from "@/lib/types";

const pretty = (k: string) => k.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const fmtMonth = (s: string) => { const m = /^(\d{4})-(\d{2})/.exec(s); return m ? new Date(Number(m[1]), Number(m[2]) - 1, 1).toLocaleDateString("en-AU", { month: "short", year: "numeric" }) : s; };

function Value({ v, k }: { v: unknown; k?: string }) {
  if (v == null || v === "") return <span className="muted">Not set</span>;
  if (typeof v === "boolean") return <>{v ? "Yes" : "No"}</>;
  if (typeof v === "string" || typeof v === "number") return <>{k && /^(start|end|expected_completion|period)$/.test(k) && typeof v === "string" ? fmtMonth(v) : String(v)}</>;
  if (Array.isArray(v)) return <ul style={{ margin: 0, paddingLeft: 18, textAlign: "left", display: "grid", gap: 4, fontWeight: 400 }}>{v.map((x, i) => <li key={i}><Value v={x} /></li>)}</ul>;
  return <div style={{ display: "grid", gap: 2 }}>{Object.entries(v as Record<string, unknown>).filter(([kk]) => !["relevant_to_it", "it_relevance_factor", "title_source"].includes(kk)).map(([kk, vv]) => <div key={kk}><span className="muted">{pretty(kk)}: </span><Value v={vv} k={kk} /></div>)}</div>;
}

function Visibility({ f }: { f: Fact }) {
  if (f.status !== "verified") return <Pill tone="orange">Needs confirmation</Pill>;
  return f.visibility === "document" ? <Pill tone="green" icon={<IconShield />}>Used in documents</Pill> : f.visibility === "forms_only" ? <Pill tone="blue">Forms only</Pill> : <Pill icon={<IconLock />}>Private</Pill>;
}

export default function ProfilePage() {
  const { data, error, loading, reload } = useApi<Profile>("/profile");
  return (
    <>
      <PageTop title="Profile" subtitle="The facts every resume and cover letter is built from" />
      <ErrorNote error={error} retry={reload} />
      {loading && !data && <div style={{ display: "grid", gap: 12 }}><Skeleton h={90} r={20} /><Skeleton h={200} r={20} /></div>}
      {data && (<>
        <div className="stats" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <div className="stat"><div className="stat-label">Verified</div><div className="stat-value num">{data.summary.verified}</div><div className="stat-note">of {data.summary.total} facts</div></div>
          <div className="stat"><div className="stat-label">Used in documents</div><div className="stat-value num">{data.summary.used_in_documents}</div></div>
          <div className="stat"><div className="stat-label">To confirm</div><div className="stat-value num">{data.summary.needs_confirmation}</div><div className="stat-note">Never used until verified</div></div>
        </div>
        <div style={{ marginTop: 16 }}><Callout tone="info">Documents may only use facts marked <b>Used in documents</b>. Work rights, referees and eligibility rules stay private or appear only on forms that ask. To change a fact, edit it through the command line so every change is deliberate.</Callout></div>

        {data.groups.map((g) => (
          <section key={g.category}>
            <h2 className="section-title">{g.label}</h2>
            {g.category === "skill" ? (
              <div className="card card-pad stack">
                {(["strong", "working", "basic"] as const).map((lvl) => {
                  const rows = g.facts.filter((f) => f.status === "verified" && (f.value as { level?: string })?.level === lvl);
                  return rows.length ? <div key={lvl}><div className="muted small" style={{ marginBottom: 8, textTransform: "capitalize" }}>{lvl}</div><div className="chips">{rows.map((f) => <span className={`chip ${lvl === "strong" ? "good" : ""}`} key={f.id}>{f.key}</span>)}</div></div> : null;
                })}
                {g.facts.some((f) => f.status !== "verified") && <div><div className="muted small" style={{ marginBottom: 8 }}>Needs confirmation</div><div className="chips">{g.facts.filter((f) => f.status !== "verified").map((f) => <span className="chip bad" key={f.id}>{f.key}</span>)}</div></div>}
              </div>
            ) : (
              <div className="list">
                {g.facts.map((f) => (
                  <div className="row" key={f.id} style={{ alignItems: "flex-start" }}>
                    <div className="row-main">
                      <div className="row-title" style={{ whiteSpace: "normal" }}>{pretty(f.key)}</div>
                      <div style={{ marginTop: 4, fontSize: 14.5 }}><Value v={f.value} /></div>
                      <div className="row-meta"><Visibility f={f} />{f.last_confirmed && <span className="muted small" style={{ alignSelf: "center" }}>Confirmed {shortDate(f.last_confirmed)}</span>}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        ))}
      </>)}
    </>
  );
}
