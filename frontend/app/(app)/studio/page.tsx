"use client";
import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { IconCheck, IconDoc, IconShield } from "@/components/icons";
import { Breakdown, DocumentsCard, PageTop } from "@/components/shared";
import { Button, Callout, GatePill, Pill, ScoreRing, StatusPill, Switch } from "@/components/ui";
import { useApi, useJob } from "@/lib/api";
import { EMPLOYMENT_LABEL, GATE_LABEL } from "@/lib/format";
import type { DraftResult, ListingDetail } from "@/lib/types";

const SOURCES = [["manual", "Pasted ad"], ["adzuna", "Adzuna"], ["career-page", "Employer careers page"], ["recruiter", "Recruiter"], ["seek", "Seek"], ["linkedin", "LinkedIn"], ["other", "Other"]];

function Studio() {
  const listingId = useSearchParams().get("listing");
  const { data: listing } = useApi<ListingDetail>(listingId ? `/listings/${listingId}` : null);
  const [text, setText] = useState("");
  const [urlEdit, setUrlEdit] = useState<string | null>(null);
  const [sourceEdit, setSourceEdit] = useState<string | null>(null);
  const url = urlEdit ?? listing?.url ?? "";  // prefilled from the job you came from, until you change it
  const source = sourceEdit ?? listing?.source.split(":")[0] ?? "manual";
  const setUrl = setUrlEdit, setSource = setSourceEdit;
  const [scoreOnly, setScoreOnly] = useState(false);
  const { job, running, run, reset } = useJob<DraftResult>();

  const r = job?.status === "done" ? job.result : null;
  const ready = text.trim().length >= 40;

  return (
    <>
      <PageTop title="Draft studio" subtitle="Paste a full job ad. You get a score, a rules check and tailored documents." />
      <div className="grid-2">
        <div className="stack">
          <div className="card card-pad stack">
            {listing && <Callout tone="info">Drafting for <b>{listing.title}</b>{listing.employer ? ` at ${listing.employer}` : ""}. Open <a className="link" href={listing.url} target="_blank" rel="noopener noreferrer">the original ad</a>, copy the whole text, and paste it below.</Callout>}
            <div className="field"><label htmlFor="ad">Job ad</label>
              <textarea id="ad" className="textarea" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the full text of the job ad here…" /></div>
            <div className="grid-3" style={{ gridTemplateColumns: "1fr", gap: 12 }}>
              <div className="field"><label htmlFor="url">Link to the ad (helps catch duplicates)</label><input id="url" className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" inputMode="url" /></div>
              <div className="field"><label htmlFor="src">Where did you find it?</label><select id="src" className="select" value={source} onChange={(e) => setSource(e.target.value)}>{SOURCES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
            </div>
            <Switch checked={scoreOnly} onChange={setScoreOnly} label="Score only, no documents" />
            <Button size="lg" block busy={running} disabled={!ready} onClick={() => run("draft", { text, url: url || null, source, score_only: scoreOnly }).catch(() => {})}>
              <IconDoc />{running ? job?.message ?? "Working" : scoreOnly ? "Score this job" : "Score & draft documents"}
            </Button>
            {!ready && text.length > 0 && <div className="muted small">Paste the complete ad: a few sentences at least.</div>}
            {running && <div className="muted small" style={{ textAlign: "center" }}>Reading the ad on your Mac. This takes about 20 seconds.</div>}
          </div>
          <div className="callout info"><IconShield /><div><b>Your rules are always on.</b> The documents use only facts you have verified, and nothing is ever submitted for you. You apply yourself, then record it in the tracker.</div></div>
        </div>

        <div className="stack">
          {job?.status === "error" && <Callout tone="bad">{job.error}</Callout>}
          {r && (<>
            <div className="card card-pad">
              <div className="row-flex" style={{ gap: 16 }}>
                <ScoreRing score={r.score} size={84} />
                <div className="grow">
                  <div style={{ fontWeight: 650, fontSize: 18, letterSpacing: "-0.02em" }}>{r.job.title}</div>
                  <div className="muted">{[r.job.employer, r.job.location].filter(Boolean).join(" · ") || "Employer not found in the ad"}</div>
                  <div className="row-flex wrap" style={{ marginTop: 8, gap: 6 }}><GatePill gate={r.gate.status} />{EMPLOYMENT_LABEL[r.job.employment_type] && <Pill>{EMPLOYMENT_LABEL[r.job.employment_type]}</Pill>}</div>
                </div>
              </div>
            </div>
            <div className="callouts">
              {r.skipped_reason && <Callout tone="bad"><b>No documents made.</b> {r.skipped_reason}</Callout>}
              {r.gate.fail_reasons.map((f) => <Callout key={f} tone="bad"><b>Not eligible.</b> {f}.</Callout>)}
              {r.gate.flags.map((f) => <Callout key={f} tone="warn">{f.charAt(0).toUpperCase() + f.slice(1)}{/[.!?]$/.test(f) ? "" : "."}</Callout>)}
              {r.same_employer.map((a) => <Callout key={a.id} tone="info">Same employer already tracked: <Link className="link" href={`/tracker/${a.id}`}>{a.title ?? "role not recorded"}</Link> (<StatusPill status={a.status} />). Different role, so this is not blocked.</Callout>)}
              {r.warnings.map((w) => <Callout key={w} tone="info">{w}.</Callout>)}
            </div>
            {r.application && r.files.length > 0 && (<>
              <Callout tone="good"><IconCheck style={{ display: "none" }} /><div><b>Draft saved to your tracker.</b> Read both documents, then approve them. <Link className="link" href={`/tracker/${r.application.id}`}>Open in tracker</Link></div></Callout>
              <DocumentsCard appId={r.application.id} files={r.files} employer={r.job.employer ?? undefined} />
            </>)}
            <div className="card card-pad"><h2 className="section-title" style={{ margin: "0 0 4px" }}>How it scored · {GATE_LABEL[r.gate.status ?? "REVIEW"]}</h2><Breakdown breakdown={r.breakdown} /></div>
            {(r.job.required_skills.length > 0) && <div className="card card-pad"><h2 className="section-title" style={{ margin: "0 0 10px" }}>Skills the ad names</h2><div className="chips">{r.job.required_skills.map((s) => <span className="chip" key={s}>{s}</span>)}</div></div>}
            <Button variant="gray" onClick={() => { reset(); setText(""); setUrlEdit(""); }}>Start another</Button>
          </>)}
          {!job && !running && (
            <div className="card card-pad muted" style={{ textAlign: "center", padding: 36 }}>
              <div style={{ fontSize: 15 }}>Your result appears here.</div>
              <div className="small" style={{ marginTop: 6 }}>Score, rules check, skill match, and a resume and cover letter as PDF and Word.</div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default function Page() { return <Suspense><Studio /></Suspense>; }
