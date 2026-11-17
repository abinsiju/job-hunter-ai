"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { DocumentsCard, PageTop } from "@/components/shared";
import { Button, Callout, Pill, ScoreRing, useToast } from "@/components/ui";
import { api, invalidate } from "@/lib/api";
import { normaliseUrl, tidy } from "@/lib/forgive";
import type { QuickDraftResult } from "@/lib/types";

const LEVELS: [string, string][] = [["basic", "Basic"], ["working", "Working"], ["strong", "Strong"]];

export default function QuickDraft() {
  const toast = useToast();
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [employer, setEmployer] = useState("");
  const [busy, setBusy] = useState(false);
  const [secs, setSecs] = useState(0);
  const [res, setRes] = useState<QuickDraftResult | null>(null);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState<string | null>(null);
  const [showAllKw, setShowAllKw] = useState(false);
  const [showAllMissing, setShowAllMissing] = useState(false);
  const resRef = useRef<HTMLDivElement | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  async function generate(adText = text) {
    setBusy(true); setErr(""); setSecs(0);
    let ticks = 0;
    timer.current = setInterval(() => setSecs(++ticks / 10), 100);
    try {
      const r = await api<QuickDraftResult>("/quick-draft", { method: "POST", json: { text: adText, url: normaliseUrl(url) || null, title: tidy(title), employer: tidy(employer) }  /* 18 Postel: a link without https:// or with stray spaces still works */ });
      setRes(r); invalidate();
    } catch (e) { setErr((e as Error).message); setRes(null); }
    finally { if (timer.current) clearInterval(timer.current); setBusy(false); }
  }
  async function confirmSkills(names: string[], level: string) {
    try {
      for (const name of names) await api("/quick-draft/confirm-skill", { method: "POST", json: { name, level } });
      toast(`${names.join(", ")} added to your profile as ${level}. Drafting again…`); setAdding(null);
      await generate();
    } catch (e) { toast((e as Error).message, "err"); }
  }
  const confirmSkill = (name: string, level: string) => confirmSkills([name], level);
  // 3 staging: when the result arrives the page brings it into view
  useEffect(() => { if (res) resRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); }, [res]);
  const ats = res?.ats;
  const blocked = res?.skipped_reason;

  return (
    <>
      <PageTop title="Quick draft" subtitle="Paste a job ad. Get a tailored resume and cover letter in seconds" />
      <div className="card card-pad stack" data-testid="quick-input">
        <div className="field"><label htmlFor="qtext">The job ad</label>
          <textarea id="qtext" className="textarea" style={{ minHeight: 200 }} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the whole job ad here" aria-describedby="qhint"
            onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && text.trim().length >= 80 && !busy) { e.preventDefault(); generate(); } /* 19 Jakob: ⌘/Ctrl+Enter sends, as in every other app */ }} />
          <div id="qhint" className="muted small" style={{ marginTop: 6 }}>{text.trim().length < 80 ? `${text.trim().length === 0 ? "Paste the ad" : `${80 - text.trim().length} more characters`} to begin` : "Press ⌘ Enter (Ctrl Enter) to make it"}</div></div>
        <div className="grid-2" style={{ gap: 12 }}>
          <div className="field"><label htmlFor="qtitle">Job title (optional, if the paste does not start with it)</label><input id="qtitle" className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. IT Support Officer" /></div>
          <div className="field"><label htmlFor="qemp">Employer (optional)</label><input id="qemp" className="input" value={employer} onChange={(e) => setEmployer(e.target.value)} /></div>
        </div>
        <div className="field"><label htmlFor="qurl">Link to the ad (optional)</label><input id="qurl" className="input" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></div>
        <Button size="lg" busy={busy} disabled={text.trim().length < 80} onClick={() => generate()}>{busy ? `Working… ${secs.toFixed(1)} s` : "Make my resume and cover letter"}</Button>
        <p className="muted small" style={{ margin: 0 }}>Uses only facts in your verified profile. It reads the ad with rules (no waiting for a language model), checks the work-rights rules, then writes both documents as Word and PDF, laid out so applicant tracking systems can read them.</p>
      </div>
      {err && <div style={{ marginTop: 14 }}><Callout tone="bad">{err}</Callout></div>}

      {res && (
        <div ref={resRef} className="stack stage" style={{ marginTop: 14, scrollMarginTop: 70 }} data-testid="quick-result">
          <div className="row-flex wrap" style={{ gap: 8 }}>
            <Pill tone={res.within_budget ? "green" : "orange"}>Done in {(res.elapsed_ms / 1000).toFixed(1)} s</Pill>
            <Pill tone={res.gate.status === "PASS" ? "green" : res.gate.status === "FAIL" ? "red" : "orange"}>{res.gate.status === "PASS" ? "Eligible" : res.gate.status === "FAIL" ? "Not eligible" : "Check first"}</Pill>
            <Pill>Match score {Math.round(res.score)}</Pill>
          </div>
          {blocked && <Callout tone="bad">{blocked}</Callout>}
          {res.gate.fail_reasons.map((r) => <Callout key={r} tone="bad">{r}</Callout>)}
          {res.gate.flags.map((f) => <Callout key={f} tone="warn">{f}</Callout>)}
          {res.warnings.map((w) => <Callout key={w} tone="info">{w}</Callout>)}

          {res.application && res.files.length > 0 && <div><h2 className="section-title" style={{ margin: "0 4px 10px" }}>Your documents</h2>
            <DocumentsCard appId={res.application.id} files={res.files} employer={res.job.employer ?? undefined} />
            <p className="muted small" style={{ margin: "8px 4px 0" }}>Saved as a draft in your <Link className="link" href={`/tracker/${res.application.id}`}>Tracker</Link>, where you can fill the application form.</p></div>}

          {ats && (
            <div className="card card-pad stack" data-testid="ats-card">
              <div className="row-flex" style={{ gap: 16, alignItems: "center" }}>
                <ScoreRing score={ats.score} size={84} />
                <div className="grow"><h2 className="section-title" style={{ margin: 0 }}>ATS match: {ats.score} / 100</h2>
                  <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>{ats.coverage_pct}% of the ad&apos;s keywords appear in your resume. It predicts how an applicant tracking system will rank you; it cannot promise a rank.</p></div>
              </div>
              {!ats.target.reached && (
                <div className="callout info" data-testid="ats-target"><div>
                  {ats.target.add_if_true.length > 0
                    ? <><b>To reach {ats.target.goal}+</b> your resume needs the ad&apos;s keywords. The most valuable ones your profile does not list: {ats.target.add_if_true.map((a) => `${a.keyword} (→ ${a.score_if_added})`).join(", ")}. Confirm only the ones you truly have.{!ats.target.reachable_with_true_skills && ` Even with every listed skill the ceiling is ${ats.target.potential_score}.`}</>
                    : <>Your resume already holds every skill this ad names that your profile can support (ceiling {ats.target.potential_score}). The rest of the gap is general wording the ad uses; add a skill only if it is true.</>}
                </div></div>
              )}
              {ats.format && <div className="kv"><span>Readable by an ATS</span><span>{ats.format.parsable ? "Yes" : "Check"} · {ats.format.pages} page{ats.format.pages === 1 ? "" : "s"} · headings: {ats.format.headings_found.join(", ")}</span></div>}
              {ats.format?.issues.map((i) => <Callout key={i} tone="warn">{i}</Callout>)}
              <div><div className="muted small" style={{ marginBottom: 6 }}>In your resume ({ats.matched.length})</div><div className="chips">{(showAllKw ? ats.matched : ats.matched.slice(0, 8)).map((k) => <span className="chip good" key={k}>{k}</span>)}{ats.matched.length > 8 && <button className="link small" onClick={() => setShowAllKw(!showAllKw)}>{showAllKw ? "Show fewer" : `Show all ${ats.matched.length}`}</button>}</div></div>
              {ats.missing_skills.length > 0 && <div>
                <div className="muted small" style={{ marginBottom: 6 }}>The ad asks for these and your profile does not list them. Add one only if it is true:</div>
                <div className="chips">{(showAllMissing ? ats.missing_skills : ats.missing_skills.slice(0, 7)).map((k) => <button className="chip bad" key={k} onClick={() => setAdding(adding === k ? null : k)} aria-expanded={adding === k}>{k}</button>)}{ats.missing_skills.length > 7 && <button className="link small" onClick={() => setShowAllMissing(!showAllMissing)}>{showAllMissing ? "Show fewer" : `Show all ${ats.missing_skills.length}`}</button>}</div>
                <div className="row-flex wrap" style={{ gap: 8, marginTop: 10 }}><span style={{ fontSize: 14.5 }}>I truly have <b>all {ats.missing_skills.length}</b> of these, at level:</span>
                  {LEVELS.map(([v, l]) => <Button key={v} size="sm" variant="gray" onClick={() => confirmSkills(ats.missing_skills, v)}>{l}</Button>)}</div>
                {adding && <div className="row-flex wrap" style={{ gap: 8, marginTop: 10 }}><span style={{ fontSize: 14.5 }}>I really have <b>{adding}</b> at this level:</span>
                  {LEVELS.map(([v, l]) => <Button key={v} size="sm" variant="tint" onClick={() => confirmSkill(adding, v)}>{l}</Button>)}</div>}
              </div>}
              {ats.missing.filter((k) => !ats.missing_skills.includes(k)).length > 0 && <p className="muted small" style={{ margin: 0 }}>Other words in the ad your resume does not use: {ats.missing.filter((k) => !ats.missing_skills.includes(k)).join(", ")}.</p>}
              <p className="muted small" style={{ margin: 0 }}>{ats.note}</p>
            </div>
          )}
        </div>
      )}
    </>
  );
}
