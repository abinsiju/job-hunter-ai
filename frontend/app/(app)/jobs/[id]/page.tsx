"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { IconBack, IconBan, IconBuilding, IconClock, IconDoc, IconEyeOff, IconLink, IconPin } from "@/components/icons";
import { Breakdown } from "@/components/shared";
import { VerificationCard, WorkRightsLine } from "@/components/verification";
import { Button, Callout, ErrorNote, GatePill, Pill, ScoreRing, Sheet, Skeleton, StatusPill, useToast } from "@/components/ui";
import { api, invalidate, useApi, useJob } from "@/lib/api";
import { EMPLOYMENT_LABEL, shortDate, timeAgo } from "@/lib/format";
import type { DraftResult, ListingDetail } from "@/lib/types";

const VERDICT = { PASS: "Matches your profile and your rules", REVIEW: "Worth a look, with a few things to check", FAIL: "Not eligible for you" } as const;

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { data: l, error, loading, reload } = useApi<ListingDetail>(`/listings/${id}`);
  const draft = useJob<DraftResult>();
  const [showAd, setShowAd] = useState(false);
  const [confirmBlock, setConfirmBlock] = useState(false);

  async function makeDraft() {
    const j = await draft.run("draft", { listing_id: Number(id) }).catch((e: Error) => { toast(e.message, "err"); return null; });
    if (!j) return;
    if (j.status === "error") return toast(j.error ?? "Could not create the draft", "err");
    const r = j.result;
    if (r?.skipped_reason) return toast(r.skipped_reason, "err");
    if (!r?.application) return;
    // One step: documents are ready, now open the employer's form and fill it (it stops before Submit).
    try {
      await api(`/applications/${r.application.id}/assist`, { method: "POST", json: {} });
      toast("Form opened in a new window. Check it, then press Submit yourself.");
    } catch (e) {
      toast(`Documents are ready. ${(e as Error).message}`, "err");
    }
    router.push(`/tracker/${r.application.id}`);
  }
  async function dismiss() {
    try { await api(`/listings/${id}/dismiss`, { method: "POST" }); invalidate("/listings"); invalidate("/overview"); toast("Hidden from your list"); router.push("/jobs"); }
    catch (e) { toast((e as Error).message, "err"); }
  }
  async function block() {
    try { await api(`/listings/${id}/block-employer`, { method: "POST" }); setConfirmBlock(false); invalidate(); toast("Employer blocked. Re-score to apply it everywhere."); reload(); }
    catch (e) { toast((e as Error).message, "err"); }
  }

  if (loading && !l) return <div style={{ display: "grid", gap: 14, marginTop: 14 }}><Skeleton h={28} w="60%" /><Skeleton h={14} w="35%" /><Skeleton h={180} r={20} /></div>;
  if (error || !l) return <><Link href="/jobs" className="link"><IconBack style={{ width: 14, height: 14, verticalAlign: "-2px" }} /> Jobs</Link><div style={{ marginTop: 16 }}><ErrorNote error={error ?? new Error("Not found")} retry={reload} /></div></>;

  const ex = l.extract;
  const emp = l.employment ? EMPLOYMENT_LABEL[l.employment] : "";
  const eligible = l.gate !== "FAIL";

  return (
    <>
      <Link href="/jobs" className="link" style={{ display: "inline-flex", alignItems: "center", gap: 4, marginTop: 6 }}><IconBack style={{ width: 15, height: 15 }} />Jobs</Link>
      <h1 className="large-title" style={{ marginTop: 12 }}>{l.title}</h1>
      <p className="subtitle" style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", marginBottom: 18 }}>
        {l.employer && <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><IconBuilding style={{ width: 16, height: 16 }} />{l.employer}</span>}
        {l.location && <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><IconPin style={{ width: 16, height: 16 }} />{l.location}</span>}
        {l.posted_at && <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}><IconClock style={{ width: 16, height: 16 }} />Posted {timeAgo(l.posted_at)}</span>}
      </p>

      <div className="grid-2">
        <div className="stack">
          <div className="card card-pad">
            <div className="row-flex" style={{ gap: 18, alignItems: "center" }}>
              <ScoreRing score={l.score} size={92} />
              <div className="grow">
                <div className="row-flex wrap" style={{ gap: 6 }}><GatePill gate={l.gate} />{emp && <Pill>{emp}</Pill>}{l.salary && <Pill>{l.salary}</Pill>}<Pill>{l.source_label}{l.snippet_only ? " · preview" : ""}</Pill>{l.verification.open_status === "closed" && <Pill tone="red">Closed</Pill>}</div>
                <div style={{ marginTop: 8 }}><WorkRightsLine v={l.verification} failReasons={l.fail_reasons} /></div>
                <div style={{ fontSize: 17, fontWeight: 600, letterSpacing: "-0.02em", marginTop: 8 }}>{VERDICT[l.gate ?? "REVIEW"]}</div>
                {l.score != null && <div className="muted" style={{ fontSize: 14.5 }}>{l.score % 1 ? l.score.toFixed(1) : l.score} out of 100</div>}
              </div>
            </div>
            {l.explanation.length > 0 && <ul className="plain-list">{l.explanation.map((x, i) => <li key={i}>{x.charAt(0).toUpperCase() + x.slice(1)}</li>)}</ul>}
          </div>

          <div className="callouts">
            {l.fail_reasons.map((r) => <Callout key={r} tone="bad"><b>Not eligible.</b> {r}.</Callout>)}
            {l.flags.map((f) => <Callout key={f} tone="warn">{f.charAt(0).toUpperCase() + f.slice(1)}{/[.!?]$/.test(f) ? "" : "."}</Callout>)}
            {l.tracked && <Callout tone="info">You already have this vacancy in your tracker (<StatusPill status={l.tracked.status} />). <Link className="link" href={`/tracker/${l.tracked.id}`}>Open it</Link>.</Callout>}
            {l.same_employer.length > 0 && <Callout tone="info">You have also dealt with {l.employer} before: {l.same_employer.map((s) => <Link key={s.id} className="link" href={`/tracker/${s.id}`}>{s.title ?? "another role"} ({s.status})</Link>).reduce<React.ReactNode[]>((a, x, i) => (i ? [...a, ", ", x] : [x]), [])}.</Callout>}
            {l.employer_blocked && <Callout tone="bad">{l.employer} is on your blocklist.</Callout>}
            {l.draft_hint && eligible && <Callout tone="info">{l.draft_hint}</Callout>}
          </div>

          <VerificationCard id={l.id} v={l.verification} snippetOnly={l.snippet_only} onChanged={reload} />

          <div className="card card-pad">
            <h2 className="section-title" style={{ margin: "0 0 4px" }}>How it scored</h2>
            <Breakdown breakdown={l.breakdown} />
          </div>
        </div>

        <div className="stack">
          <div className="card card-pad">
            <div className="detail-actions" style={{ flexDirection: "column" }}>
              {eligible && l.can_draft && <Button size="lg" block busy={draft.running} onClick={makeDraft}><IconDoc />{draft.running ? draft.job?.message ?? "Working" : "Apply: fill the form for me"}</Button>}
              {eligible && !l.can_draft && <Link className="btn btn-primary btn-lg btn-block" href={`/studio?listing=${l.id}`}><IconDoc />Draft from the full ad</Link>}
              {l.url && <a className="btn btn-tint btn-block" href={l.url} target="_blank" rel="noopener noreferrer"><IconLink />Open the original ad</a>}
              <div className="row-flex" style={{ gap: 8 }}>
                <Button variant="gray" block onClick={dismiss}><IconEyeOff />Not interested</Button>
                {l.employer && !l.employer_blocked && <Button variant="danger" block onClick={() => setConfirmBlock(true)}><IconBan />Block employer</Button>}
              </div>
            </div>
            {draft.job?.status === "running" && <p className="muted small" style={{ margin: "12px 0 0" }}>About 30 seconds. It opens the employer&apos;s form with your details and documents, then stops. You press Submit.</p>}
          </div>

          <div className="card card-pad">
            <h2 className="section-title" style={{ margin: "0 0 10px" }}>What the ad asks for</h2>
            {(ex.required_skills?.length ?? 0) > 0 && <div style={{ marginBottom: 12 }}><div className="muted small" style={{ marginBottom: 6 }}>Skills</div><div className="chips">{ex.required_skills!.map((s) => <span className="chip" key={s}>{s}</span>)}</div></div>}
            {(ex.nice_to_have_skills?.length ?? 0) > 0 && <div style={{ marginBottom: 12 }}><div className="muted small" style={{ marginBottom: 6 }}>Nice to have</div><div className="chips">{ex.nice_to_have_skills!.map((s) => <span className="chip" key={s}>{s}</span>)}</div></div>}
            <div>
              {ex.min_years_experience != null && <div className="kv"><span>Experience</span><span>{ex.min_years_experience}+ years</span></div>}
              {(ex.qualifications?.length ?? 0) > 0 && <div className="kv"><span>Qualifications</span><span>{ex.qualifications!.map((q) => q.text).join(", ")}</span></div>}
              {(ex.hours_per_week_max ?? ex.hours_per_week_min) != null && <div className="kv"><span>Hours</span><span>{ex.hours_per_week_max ?? ex.hours_per_week_min} per week</span></div>}
              {ex.deadline && <div className="kv"><span>Closes</span><span>{shortDate(ex.deadline)}</span></div>}
              {ex.requires_drivers_licence && <div className="kv"><span>Driver&apos;s licence</span><span>Required</span></div>}
              <div className="kv"><span>Ad text</span><span>{l.snippet_only ? "Preview only" : "Full ad"}</span></div>
            </div>
          </div>

          <div className="card card-pad">
            <button className="row-flex" style={{ width: "100%", justifyContent: "space-between" }} onClick={() => setShowAd(!showAd)} aria-expanded={showAd}>
              <span className="section-title" style={{ margin: 0 }}>{l.snippet_only ? "Preview from the source" : "Full ad"}</span><span className="link">{showAd ? "Hide" : "Show"}</span>
            </button>
            {showAd && <div className="pre" style={{ marginTop: 12 }}>{l.description || "No text."}</div>}
          </div>
        </div>
      </div>

      <Sheet open={confirmBlock} onClose={() => setConfirmBlock(false)} title={`Block ${l.employer}?`}>
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>Every job from this employer will be excluded from now on, including future searches. You can undo this in Settings.</p>
          <Button variant="danger" block onClick={block}>Block {l.employer}</Button>
          <Button variant="gray" block onClick={() => setConfirmBlock(false)}>Cancel</Button>
        </div>
      </Sheet>
    </>
  );
}
