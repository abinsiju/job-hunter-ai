"use client";
import { useState } from "react";
import { IconLink } from "@/components/icons";
import { Button, Callout, Pill, useToast } from "@/components/ui";
import { api, invalidate } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Confidence, Route, Verification } from "@/lib/types";

export const CONFIDENCE: Record<Confidence, { label: string; tone: string; hint: string }> = {
  VERIFIED: { label: "Verified with employer", tone: "green", hint: "Read from the employer's own posting." },
  THIRD_PARTY_FULL: { label: "Third-party full ad", tone: "blue", hint: "The full ad from a job board. The employer's own version was not found." },
  SNIPPET_ONLY: { label: "Snippet only", tone: "orange", hint: "Only a preview of the ad. The work-rights line has not been seen." },
};
export const ROUTE_LABEL: Record<Route, string> = {
  employer_ats: "Employer's application form", employer_site: "Employer's website", recruiter: "Through a recruiter", email: "Apply by email",
  seek_quick_apply: "SEEK quick apply (apply there yourself)", unknown: "Not known yet",
};

export function ConfidenceBadge({ confidence }: { confidence: Confidence | null }) {
  if (!confidence) return <Pill>Not checked</Pill>;
  const c = CONFIDENCE[confidence];
  return <span title={c.hint}><Pill tone={c.tone}>{c.label}</Pill></span>;
}

/** The work-rights line, with how far it can be trusted next to it. */
export function WorkRightsLine({ v, failReasons }: { v: Verification; failReasons: string[] }) {
  const wr = failReasons.find((r) => /citizen|permanent resid|clearance|work rights/i.test(r));
  const text = wr ? `Work rights: ${wr}` : v.confidence === "SNIPPET_ONLY" || !v.confidence ? "Work rights: not seen in the ad" : "Work rights: no citizenship, PR or clearance requirement found";
  return <div className="row-flex wrap" style={{ gap: 8, alignItems: "center" }} data-testid="work-rights-line"><span style={{ fontSize: 14.5 }}>{text}</span><ConfidenceBadge confidence={v.confidence} /></div>;
}

export function VerificationCard({ id, v, onChanged, snippetOnly }: { id: number; v: Verification; onChanged: () => void; snippetOnly: boolean }) {
  const toast = useToast();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  async function run(withUrl: boolean) {
    setBusy(true);
    try {
      const r = await api<{ confidence: Confidence; open_status: string }>(`/listings/${id}/verify`, { method: "POST", json: withUrl ? { url: url.trim() } : {}, signal: AbortSignal.timeout(180000) });
      invalidate(); onChanged(); setUrl("");
      toast(r.open_status === "closed" ? "That job is closed" : r.confidence === "VERIFIED" ? "Checked against the employer's own ad" : "Checked. The employer's version could not be read.", r.confidence === "SNIPPET_ONLY" && withUrl ? "err" : "ok");
    } catch (e) { toast((e as Error).message, "err"); } finally { setBusy(false); }
  }
  const review = v.discrepancies.filter((d) => d.severity === "review");
  const problem = review.length > 0 || v.open_status === "closed";
  const [expanded, setExpanded] = useState(false);
  if (!problem && !expanded) {
    return (
      <div className="card card-pad row-flex wrap" style={{ gap: 8, justifyContent: "space-between", alignItems: "center" }} data-testid="verification-card">
        <span style={{ fontSize: 14.5 }}>Source check: <ConfidenceBadge confidence={v.confidence} /></span>
        <button className="link" onClick={() => setExpanded(true)}>Details</button>
      </div>
    );
  }
  const notes = v.discrepancies.filter((d) => d.severity !== "review");
  return (
    <div className="card card-pad stack" data-testid="verification-card">
      <div className="row-flex wrap" style={{ gap: 8, justifyContent: "space-between", alignItems: "center" }}>
        <h2 className="section-title" style={{ margin: 0 }}>Source check</h2><ConfidenceBadge confidence={v.confidence} />
      </div>
      {v.confidence && <p className="muted" style={{ margin: 0, fontSize: 14.5 }}>{CONFIDENCE[v.confidence].hint}{v.verified_at ? ` Checked ${shortDate(v.verified_at)}.` : ""}</p>}
      {v.open_status === "closed" && <Callout tone="bad"><b>Closed.</b> {v.closed_reason ?? "The employer's posting is no longer open."} Nothing will be drafted for it.</Callout>}
      {v.open_status === "open" && <Callout tone="good">The employer&apos;s posting is still open.</Callout>}
      {review.map((d) => (
        <Callout key={d.field + d.label} tone="warn">
          <b>The two ads disagree: {d.label}.</b> Read both before you decide.
          <div className="quote"><span className="muted small">Job board says</span><div>{d.third_party}</div></div>
          <div className="quote"><span className="muted small">Employer says</span><div>{d.employer}</div></div>
        </Callout>
      ))}
      {notes.length > 0 && <div><div className="muted small" style={{ marginBottom: 6 }}>Smaller differences</div>{notes.map((d) => (
        <div className="kv-block" key={d.field + d.label}><div className="kv-label">{d.label}</div>
          <div style={{ fontSize: 14.5 }}><span className="muted">Board:</span> {d.third_party}</div><div style={{ fontSize: 14.5 }}><span className="muted">Employer:</span> {d.employer}</div></div>))}</div>}
      <div className="kv"><span>Where to apply</span><span>{v.application_route ? ROUTE_LABEL[v.application_route] : "Not checked"}</span></div>
      {v.employer_url && <div className="kv"><span>Employer&apos;s ad</span><a className="link" href={v.employer_url} target="_blank" rel="noopener noreferrer"><IconLink style={{ width: 14, height: 14, verticalAlign: "-2px" }} /> Open</a></div>}
      {v.notes.length > 0 && <details><summary className="link" style={{ cursor: "pointer" }}>What was checked</summary><ul className="plain-list">{v.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></details>}
      <div className="stack" style={{ gap: 8 }}>
        <label className="muted small" htmlFor="emp-url">{snippetOnly ? "Paste the employer's link to read the full ad" : "Paste the employer's link to check against it"}</label>
        <div className="row-flex" style={{ gap: 8 }}>
          <input id="emp-url" className="input" inputMode="url" placeholder="https://careers.example.com/job/…" value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button variant="tint" disabled={!/^https?:\/\//i.test(url.trim())} busy={busy} onClick={() => run(true)}>Check</Button>
        </div>
        <Button variant="gray" busy={busy && !url} onClick={() => run(false)}>Look for the employer&apos;s own ad</Button>
        <p className="muted small" style={{ margin: 0 }}>Only the employer&apos;s public job feeds and pages that allow it are read. Job boards, logins and CAPTCHAs are never touched.</p>
      </div>
    </div>
  );
}
