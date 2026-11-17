"use client";
import { useEffect, useState } from "react";
import { IconLink, IconShield } from "@/components/icons";
import { Button, Callout, Pill, useToast } from "@/components/ui";
import { api, invalidate, useApi } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import type { ApplicationRow, AssistInfo, Blank, EmailInfo } from "@/lib/types";

/** Gmail DRAFT only. The app has no code that sends mail: you press Send in Gmail. */
export function EmailDraftCard({ app, onChanged }: { app: ApplicationRow; onChanged: () => void }) {
  const toast = useToast();
  const { data: e, reload } = useApi<EmailInfo>(`/applications/${app.id}/email`);
  const [to, setTo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!e) return null;
  const address = to ?? e.candidates[0]?.address ?? "";
  async function make() {
    setBusy(true); setErr("");
    try { await api(`/applications/${app.id}/email-draft`, { method: "POST", json: { to: address.trim() } }); invalidate(); reload(); onChanged(); toast("Draft created in Gmail. Open Gmail to review and send it."); }
    catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="card card-pad stack" data-testid="email-card">
      <h2 className="section-title" style={{ margin: 0 }}>Email application</h2>
      {e.draft ? (
        <Callout tone="good">A Gmail draft to <b>{e.draft.to}</b> was created {timeAgo(e.draft.at)}. It has not been sent. Review it in Gmail, press Send yourself, then record the sent email here as proof.</Callout>
      ) : (<>
        <p className="muted" style={{ margin: 0, fontSize: 14.5 }}>For ads that accept applications by email. This creates a <b>draft</b> in your Gmail with both PDFs attached. It is never sent for you.</p>
        {e.candidates.length === 0 && <Callout tone="info">No application email address was found in the ad. Type the address from the ad below.</Callout>}
        <div className="field"><label htmlFor="to">To</label><input id="to" className="input" type="email" value={address} onChange={(x) => setTo(x.target.value)} placeholder="jobs@employer.com.au" list="cands" />
          <datalist id="cands">{e.candidates.map((c) => <option key={c.address} value={c.address} />)}</datalist></div>
        <div className="kv"><span>Subject</span><span>{e.subject}</span></div>
        <div className="kv"><span>Attachments</span><span>{e.attachments.length ? e.attachments.join(", ") : "none yet"}</span></div>
        {e.reason && <Callout tone="info">{e.reason}</Callout>}
        {!e.configured && <Callout tone="warn">Gmail is not connected yet. Run <code>scripts/set_gmail.sh</code> once in Terminal (it saves a Gmail app password in your Keychain). Until then, use the button below to open a Gmail compose window yourself.</Callout>}
        {err && <Callout tone="bad">{err}</Callout>}
        <div className="detail-actions">
          <Button busy={busy} disabled={!e.can_draft || !e.configured || !/^\S+@\S+\.\S+$/.test(address)} onClick={make}>Create Gmail draft</Button>
          <a className="btn btn-gray" href={e.compose_url} target="_blank" rel="noopener noreferrer"><IconLink />Open in Gmail compose</a>
        </div>
        {!e.configured && <p className="muted small" style={{ margin: 0 }}>The compose window cannot attach files: attach the PDFs from Documents.</p>}
      </>)}
    </div>
  );
}

const STATE_LABEL: Record<string, string> = { starting: "Starting", opening: "Opening the job page", navigating: "Finding the application", filling: "Filling in the form", waiting: "Waiting for you",
  ready: "Your turn: review and submit", handed_over: "Yours now: automation has stopped", completed: "Submission confirmed", blocked: "Stopped", closed: "Window closed", error: "Error" };
const STAGE_HINT: Record<string, string> = { USER_INTERVENTION_REQUIRED: "Needs your input", APPLICATION_READY_FOR_APPROVAL: "Ready for your approval" };
const ACTIVE = ["starting", "opening", "navigating", "filling", "waiting", "ready", "handed_over"];

/** Opens the employer's page, finds the way to the application (Apply button, new tab, modal, other site), fills each step from verified facts and stops before Submit. */
export function AssistCard({ app, onChanged }: { app: ApplicationRow; onChanged: () => void }) {
  const toast = useToast();
  const { data: a, reload, setData } = useApi<AssistInfo>(`/applications/${app.id}/assist`, { refreshMs: 2000 });
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const st = a?.status;
  useEffect(() => { if (st?.screenshot || st?.confirmation) onChanged(); }, [st?.screenshot, st?.confirmation?.url]); // eslint-disable-line react-hooks/exhaustive-deps -- screenshots become documents once
  if (!a) return null;
  const link = url ?? a.suggested_url ?? "";
  const active = !!st && ACTIVE.includes(st.state);
  async function start() {
    setBusy(true); setErr("");
    try { const r = await api<{ status: AssistInfo["status"] }>(`/applications/${app.id}/assist`, { method: "POST", json: { url: link.trim() } }); invalidate(); setData({ ...a!, status: r.status, running: true }); onChanged(); }
    catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }
  async function stop() { try { await api(`/applications/${app.id}/assist/stop`, { method: "POST" }); reload(); } catch (x) { toast((x as Error).message, "err"); } }
  async function recordApplied() {
    const c = st?.confirmation; if (!c) return;
    try { await api(`/applications/${app.id}/status`, { method: "POST", json: { status: "applied", evidence: `Confirmation page seen on ${c.at}: “${c.text}” (${c.url}). Screenshot saved as confirmation.png.` } }); invalidate(); onChanged(); toast("Recorded as applied, with the confirmation as proof"); }
    catch (x) { toast((x as Error).message, "err"); }
  }
  const blanks = (st?.left_blank ?? []).map((b) => (typeof b === "string" ? { label: b } as Blank : b));
  const required = blanks.filter((b) => b.required);
  return (
    <div className="card card-pad stack" data-testid="assist-card">
      <h2 className="section-title" style={{ margin: 0 }}>Apply: fill the application for me</h2>
      <p className="muted" style={{ margin: 0, fontSize: 14.5 }}>Opens the employer&apos;s page in a visible window, finds how to apply (Apply button, new tab, pop-up or another site), then fills each step from your verified profile and attaches your documents. It never presses Submit, never solves a CAPTCHA, and never signs in or creates an account for you.</p>
      {!active ? (<>
        <div className="field"><label htmlFor="aurl">Job or application page link</label><input id="aurl" className="input" inputMode="url" value={link} onChange={(x) => setUrl(x.target.value)} placeholder="https://careers.example.com/jobs/…" /></div>
        {a.reason && !a.running && <Callout tone="info">{a.reason}</Callout>}
        {err && <Callout tone="bad">{err}</Callout>}
        <Button busy={busy} disabled={!a.can_start || !/^https?:\/\//i.test(link.trim())} onClick={start}>{st && st.state !== "closed" ? "Run again" : "Open and fill the application"}</Button>
      </>) : null}
      {st && (
        <div className="stack" style={{ gap: 12 }} aria-live="polite" data-testid="assist-status">
          <div className="row-flex wrap" style={{ gap: 6 }}>
            <Pill tone={st.state === "ready" || st.state === "handed_over" || st.state === "completed" ? "green" : st.state === "blocked" || st.state === "error" ? "red" : st.state === "waiting" ? "orange" : "blue"}>{STATE_LABEL[st.state] ?? st.state}</Pill>
            {st.stage && STAGE_HINT[st.stage] && <Pill tone="orange">{STAGE_HINT[st.stage]}</Pill>}
            {st.step && <Pill>Step {st.step.n} of {st.step.of}</Pill>}
            {st.gate?.status === "REVIEW" && <Pill tone="orange">Work-rights questions to read</Pill>}{st.captcha && <Pill tone="orange">CAPTCHA: solve it yourself</Pill>}
          </div>
          <p style={{ margin: 0, fontSize: 15 }}>{st.message}</p>
          {st.notes?.map((n, i) => <Callout key={i} tone="warn">{n}</Callout>)}
          {st.state === "blocked" && st.gate?.status === "FAIL" && <Callout tone="bad">{st.gate.reasons[0]}</Callout>}
          {st.candidates && st.candidates.length > 1 && st.state === "waiting" && <Callout tone="warn">Several Apply buttons look possible: {st.candidates.map((c) => c.text).join(" / ")}. Click the right one in the window.</Callout>}
          {st.confirmation && <Callout tone="good"><b>The employer&apos;s page confirmed the submission.</b> “{st.confirmation.text}” <div style={{ marginTop: 8 }}><Button size="sm" onClick={recordApplied}>Record as applied, with this proof</Button></div></Callout>}
          {(st.needs_review?.length ?? 0) > 0 && <div><div className="muted small" style={{ marginBottom: 6 }}>Read before you submit</div>{st.needs_review!.map((r, i) => (
            <div className="kv-block" key={i}><div className="kv-label">{r.label}</div><div style={{ fontSize: 14.5 }}>{r.value}</div></div>))}</div>}
          {st.filled.length > 0 && <div><div className="muted small" style={{ marginBottom: 6 }}>Filled ({st.filled.length})</div><div className="chips">{st.filled.map((f, i) => <span className="chip" key={i}>{f.label}</span>)}</div></div>}
          {blanks.length > 0 && <div><div className="muted small" style={{ marginBottom: 6 }}>Needs you ({blanks.length}{required.length ? `, ${required.length} required` : ""})</div>
            {blanks.map((b, i) => <div className="kv-block" key={i}><div className="kv-label">{b.label}{b.required ? " *" : ""}</div>{b.reason && <div className="muted small">{b.reason}</div>}</div>)}</div>}
          {(st.uploads?.length ?? 0) > 0 && <div><div className="muted small" style={{ marginBottom: 6 }}>Uploads</div>{st.uploads!.map((u, i) => <div className="kv" key={i}><span>{u.file}</span><span>{u.ok ? "Attached" : "Not confirmed"}</span></div>)}</div>}
          {st.screenshot && <a href={`/api/v1/applications/${app.id}/documents/filled_form.png`} target="_blank" rel="noopener noreferrer" className="link">View the filled form screenshot (saved as evidence)</a>}
          {(st.timeline?.length ?? 0) > 0 && <details><summary className="link" style={{ cursor: "pointer" }}>What happened, step by step</summary><ul className="plain-list">{st.timeline!.map((t, i) => <li key={i}>{t.to.replaceAll("_", " ").toLowerCase()} <span className="muted small">({t.evidence[0]})</span></li>)}</ul></details>}
          {active && <Button variant="gray" onClick={stop}>Close the window</Button>}
          {(st.state === "ready" || st.state === "handed_over") && <div className="callout info"><IconShield /><div>Nothing has been submitted. Review the answers and documents in the window, then press Submit yourself. I will notice the confirmation page and save it as proof.</div></div>}
        </div>
      )}
    </div>
  );
}

interface AutofillCode { code: string; expires_in_minutes: number; extension_path: string }

/** Fills the application in YOUR OWN Chrome through a small extension. Nothing is automated: you open the page, press Fill, solve any security check and press Submit. */
export function AutofillCard({ app }: { app: ApplicationRow }) {
  const toast = useToast();
  const [c, setC] = useState<AutofillCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function make() {
    setBusy(true); setErr("");
    try { setC(await api<AutofillCode>(`/applications/${app.id}/autofill-token`, { method: "POST" })); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  }
  async function copy() { try { await navigator.clipboard.writeText(c!.code); toast("Code copied"); } catch { toast("Copy it by hand", "err"); } }
  return (
    <div className="card card-pad stack" data-testid="autofill-card">
      <h2 className="section-title" style={{ margin: 0 }}>Fill it in your own browser</h2>
      <p className="muted" style={{ margin: 0, fontSize: 14.5 }}>Use this when a form shows a security check (like Cloudflare) in the assistant&apos;s window. A small Chrome extension fills the form in <b>your normal Chrome</b>: you open the employer&apos;s page, press <b>Fill</b>, solve any security check yourself, review, and press Submit yourself. Nothing is automated, and it never clicks a button.</p>
      {!c ? (<>
        {err && <Callout tone="bad">{err}</Callout>}
        <Button variant="tint" busy={busy} onClick={make}>Make a 30-minute autofill code</Button>
      </>) : (<>
        <div className="field"><label htmlFor="afc">Your code (valid {c.expires_in_minutes} minutes, only for {app.employer})</label><input id="afc" className="input" readOnly value={c.code} onFocus={(e) => e.currentTarget.select()} /></div>
        <Button variant="gray" onClick={copy}>Copy code</Button>
        <ol className="plain-list" style={{ paddingLeft: 18 }}>
          <li><b>Once only:</b> in Chrome open <code>chrome://extensions</code>, turn on <b>Developer mode</b>, press <b>Load unpacked</b> and choose the folder <code>{c.extension_path}</code>.</li>
          <li>Open the employer&apos;s application page in Chrome.</li>
          <li>Click the <b>Job Hunter Autofill</b> icon, paste the code, press <b>Fill this page</b>.</li>
          <li>Orange fields are yours to answer. Solve any security check, review everything, then press Submit.</li>
        </ol>
      </>)}
    </div>
  );
}
