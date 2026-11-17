"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { IconBack, IconLink, IconShield } from "@/components/icons";
import { AssistCard, AutofillCard, EmailDraftCard } from "@/components/apply-panels";
import { DocumentsCard } from "@/components/shared";
import { Button, Callout, ErrorNote, Pill, ScoreRing, Sheet, Skeleton, StatusPill, useToast } from "@/components/ui";
import { api, invalidate, useApi } from "@/lib/api";
import { STATUS_LABEL, shortDate, timeAgo } from "@/lib/format";
import type { ApplicationRow, Status } from "@/lib/types";

const ACTION: Record<Status, { label: string; variant: "primary" | "gray" | "danger" | "tint"; confirm?: string }> = {
  approved: { label: "Approve the documents", variant: "primary" }, applied: { label: "I've applied", variant: "primary" },
  interview: { label: "I got an interview", variant: "primary" }, offer: { label: "I received an offer", variant: "primary" },
  rejected: { label: "Not selected", variant: "gray", confirm: "Mark this as not selected?" }, withdrawn: { label: "Withdraw", variant: "danger", confirm: "Withdraw this application?" },
  drafted: { label: "Back to draft", variant: "gray" },
};
const GUIDE: Record<Status, string> = {
  drafted: "Read both documents carefully. When you are happy with them, approve them. Nothing is sent anywhere.",
  approved: "Now apply on the employer's own site or email, using the approved documents. Then come back and record it with proof.",
  applied: "Applied and recorded. Update this when you hear back.", interview: "Interview stage. Good luck: record the outcome when you know it.",
  offer: "You have an offer. Withdraw here if you decline it.", rejected: "This application is closed.", withdrawn: "This application is closed.",
};

export default function ApplicationDetail() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const { data: a, error, loading, setData, reload } = useApi<ApplicationRow>(`/applications/${id}`);
  const [evidence, setEvidence] = useState("");
  const [note, setNote] = useState("");
  const [sheet, setSheet] = useState<null | { to: Status }>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function move(to: Status, extra: { evidence?: string; note?: string } = {}) {
    setBusy(true); setErr("");
    try {
      const next = await api<ApplicationRow>(`/applications/${id}/status`, { method: "POST", json: { status: to, ...extra } });
      invalidate(); setData({ ...a!, ...next, same_employer: a?.same_employer, listing_id: a?.listing_id });
      setSheet(null); setEvidence(""); toast(to === "applied" ? "Recorded. Good luck!" : `Marked ${STATUS_LABEL[to].toLowerCase()}`);
      reload();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function addNote() {
    try { await api(`/applications/${id}/notes`, { method: "POST", json: { note } }); setNote(""); invalidate(); reload(); toast("Note added"); }
    catch (e) { toast((e as Error).message, "err"); }
  }

  if (loading && !a) return <div style={{ display: "grid", gap: 14, marginTop: 14 }}><Skeleton h={28} w="55%" /><Skeleton h={200} r={20} /></div>;
  if (error || !a) return <><Link href="/tracker" className="link"><IconBack style={{ width: 14, height: 14, verticalAlign: "-2px" }} /> Tracker</Link><div style={{ marginTop: 16 }}><ErrorNote error={error ?? new Error("Not found")} retry={reload} /></div></>;

  const history = [...(a.history ?? [])].reverse();
  const choose = (to: Status) => (to === "applied" || ACTION[to].confirm ? setSheet({ to }) : move(to));
  const dates: [string, string | null][] = [["Drafted", a.drafted_at], ["Approved", a.approved_at], ["Applied", a.applied_at], ["Interview", a.interview_at], ["Outcome", a.outcome_at]];

  return (
    <>
      <Link href="/tracker" className="link" style={{ display: "inline-flex", alignItems: "center", gap: 4, marginTop: 6 }}><IconBack style={{ width: 15, height: 15 }} />Tracker</Link>
      <div className="row-flex" style={{ gap: 14, margin: "12px 0 4px", alignItems: "center" }}>
        <div className="grow"><h1 className="large-title" style={{ margin: 0 }}>{a.employer}</h1><p className="subtitle" style={{ margin: "2px 0 0" }}>{a.title ?? "Role not recorded"}{a.location ? ` · ${a.location}` : ""}</p></div>
        {a.score != null && <ScoreRing score={a.score} size={64} />}
      </div>
      <div className="row-flex wrap" style={{ gap: 6, marginBottom: 20 }}><StatusPill status={a.status} />{a.before_tracker ? <Pill>Before the tracker</Pill> : <Pill>{a.source_label}</Pill>}</div>

      <div className="grid-2">
        <div className="stack">
          <div className="card card-pad">
            <h2 className="section-title" style={{ margin: "0 0 6px" }}>Next step</h2>
            <p style={{ margin: "0 0 14px", fontSize: 15.5 }}>{a.before_tracker && a.status === "applied" ? "Recorded before the tracker existed. Update it when you hear back." : GUIDE[a.status]}</p>
            <div className="detail-actions">
              {a.allowed_next.map((to) => <Button key={to} variant={ACTION[to].variant} onClick={() => choose(to)}>{ACTION[to].label}</Button>)}
              {a.allowed_next.length === 0 && <span className="muted">No further steps.</span>}
            </div>
            {(a.status === "drafted" || a.status === "approved") && <div className="callout info" style={{ marginTop: 14 }}><IconShield /><div>This app never submits anything. You always apply yourself.</div></div>}
          </div>

          {a.same_employer && a.same_employer.length > 0 && <Callout tone="info">You have other records with {a.employer}: {a.same_employer.map((s, i) => <span key={s.id}>{i ? ", " : ""}<Link className="link" href={`/tracker/${s.id}`}>{s.title ?? "role not recorded"} ({s.status})</Link></span>)}.</Callout>}

          {(a.status === "drafted" || a.status === "approved") && a.documents.length > 0 && <><AssistCard app={a} onChanged={reload} /><AutofillCard app={a} /><EmailDraftCard app={a} onChanged={reload} /></>}

          {a.documents.length > 0 && <div><h2 className="section-title" style={{ margin: "0 4px 10px" }}>Documents</h2><DocumentsCard appId={a.id} files={a.documents} employer={a.employer} /></div>}

          <div className="card card-pad">
            <h2 className="section-title" style={{ margin: "0 0 10px" }}>Notes</h2>
            {a.notes ? <div className="pre" style={{ marginBottom: 14 }}>{a.notes}</div> : <p className="muted" style={{ marginTop: 0 }}>No notes yet.</p>}
            <div className="row-flex" style={{ gap: 8 }}>
              <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note…" aria-label="Add a note" onKeyDown={(e) => { if (e.key === "Enter" && note.trim()) addNote(); }} />
              <Button variant="tint" disabled={!note.trim()} onClick={addNote}>Add</Button>
            </div>
          </div>
        </div>

        <div className="stack">
          <div className="card card-pad">
            <h2 className="section-title" style={{ margin: "0 0 4px" }}>Details</h2>
            {dates.filter(([, v]) => v).map(([k, v]) => <div className="kv" key={k}><span>{k}</span><span>{shortDate(v)}</span></div>)}
            {a.url && <div className="kv"><span>Original ad</span><a className="link" href={a.url} target="_blank" rel="noopener noreferrer"><IconLink style={{ width: 14, height: 14, verticalAlign: "-2px" }} /> Open</a></div>}
            {a.listing_id && <div className="kv"><span>Job analysis</span><Link className="link" href={`/jobs/${a.listing_id}`}>View</Link></div>}
            {a.evidence && <div className="kv-block"><div className="kv-label">Proof of application</div><div className="pre" style={{ fontWeight: 500 }}>{a.before_tracker ? "You reported this application before the tracker existed. No confirmation is on file." : a.evidence}</div></div>}
          </div>
          <div className="card card-pad">
            <h2 className="section-title" style={{ margin: "0 0 14px" }}>History</h2>
            <div className="timeline">
              {history.map((h, i) => (
                <div className="tl-item" key={i}><div className="tl-title">{STATUS_LABEL[h.status]}</div><div className="tl-sub">{a.before_tracker ? "Imported from your earlier list" : `${timeAgo(h.at)}${h.note ? ` · ${h.note}` : ""}`}</div></div>
              ))}
              {history.length === 0 && <div className="muted">No history recorded.</div>}
            </div>
          </div>
        </div>
      </div>

      <Sheet open={sheet?.to === "applied"} onClose={() => setSheet(null)} title="Record your application">
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>Keep proof that you applied: a confirmation email subject, a reference number, or where you saved a screenshot. It is required, so you never lose track of what was sent.</p>
          <div className="field"><label htmlFor="ev">Confirmation or reference</label><textarea id="ev" className="textarea" style={{ minHeight: 110 }} value={evidence} onChange={(e) => setEvidence(e.target.value)} placeholder="e.g. Seek confirmation email, reference 8841" /></div>
          {err && <Callout tone="bad">{err}</Callout>}
          <Button size="lg" block busy={busy} disabled={evidence.trim().length < 5} onClick={() => move("applied", { evidence })}>Record as applied</Button>
        </div>
      </Sheet>
      <Sheet open={!!sheet && sheet.to !== "applied"} onClose={() => setSheet(null)} title={sheet && sheet.to !== "applied" ? ACTION[sheet.to].confirm ?? "Confirm" : ""}>
        <div className="stack">
          <div className="field"><label htmlFor="nt">Reason (optional)</label><input id="nt" className="input" value={note} onChange={(e) => setNote(e.target.value)} /></div>
          {err && <Callout tone="bad">{err}</Callout>}
          <Button variant={sheet && sheet.to !== "applied" ? ACTION[sheet.to].variant : "primary"} size="lg" block busy={busy} onClick={() => sheet && move(sheet.to, { note: note || undefined })}>{sheet ? ACTION[sheet.to].label : ""}</Button>
        </div>
      </Sheet>
    </>
  );
}
