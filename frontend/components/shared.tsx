"use client";
import Link from "next/link";
import { useState } from "react";
import { IconBuilding, IconChevron, IconDoc, IconDownload, IconEyeOff, IconPin } from "./icons";
import { Bar, Pill, Row, ScoreRing, Sheet, StatusPill, GatePill, cx } from "./ui";
import { CATEGORY_LABEL, EMPLOYMENT_LABEL, STATUS_LABEL, timeAgo } from "@/lib/format";
import type { ApplicationRow, Breakdown, ListingRow } from "@/lib/types";

export function Breakdown({ breakdown, expanded = false }: { breakdown: Breakdown; expanded?: boolean }) {
  const [open, setOpen] = useState(expanded);
  const entries = Object.entries(breakdown).sort((a, b) => b[1].max_points - a[1].max_points);  // heaviest category first
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  if (!entries.length) return null;
  return (
    <div>
      {entries.map(([k, c]) => (
        <div className="cat" key={k}>
          <div className="cat-head"><span>{CATEGORY_LABEL[k] ?? k}</span><span>{c.points % 1 ? c.points.toFixed(1) : c.points} / {c.max_points}</span></div>
          <Bar value={c.points} max={c.max_points} />
          {open && (c.matched.length > 0 || c.missing.length > 0 || c.notes.length > 0) && (
            <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
              {c.matched.length > 0 && <div className="chips">{c.matched.map((m) => <span className="chip good" key={m}>{m.replace(/ ~ .*$/, "")}</span>)}</div>}
              {c.missing.length > 0 && <div className="chips">{c.missing.map((m) => <span className="chip bad" key={m}>{m}</span>)}</div>}
              {c.notes.map((n) => <div className="muted small" key={n}>{cap(n)}</div>)}
            </div>
          )}
        </div>
      ))}
      <button className="link" style={{ marginTop: 6 }} onClick={() => setOpen(!open)}>{open ? "Hide details" : "Show what matched and what is missing"}</button>
    </div>
  );
}

export function JobRow({ l, onDismiss }: { l: ListingRow; onDismiss?: () => void }) {
  const emp = l.employment ? EMPLOYMENT_LABEL[l.employment] : "";
  return (
    <Row
      href={`/jobs/${l.id}`}
      lead={<ScoreRing score={l.score} size={46} />}
      title={l.title}
      sub={<span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>{l.employer && <><IconBuilding style={{ width: 13, height: 13 }} />{l.employer}</>}{l.location && <><IconPin style={{ width: 13, height: 13 }} />{l.location}</>}</span>}
      meta={<>
        <GatePill gate={l.gate} />
        {l.tracked && <Pill tone="indigo">{l.tracked.status === "drafted" ? "Draft saved" : STATUS_LABEL[l.tracked.status]}</Pill>}
        {l.flags.length > 0 && l.gate !== "FAIL" && <Pill tone="orange">{l.flags.length} to check</Pill>}
        {emp && <Pill>{emp}</Pill>}
        {l.salary && <Pill>{l.salary}</Pill>}
        <Pill>{l.source_label}{l.snippet_only ? " · preview" : ""}</Pill>
        {l.confidence === "VERIFIED" && <Pill tone="green">Verified</Pill>}
        {l.open_status === "closed" && <Pill tone="red">Closed</Pill>}
        {l.discrepancy_count > 0 && <Pill tone="orange">{l.discrepancy_count} difference{l.discrepancy_count === 1 ? "" : "s"}</Pill>}
        {l.similar > 0 && <Pill>+{l.similar} same</Pill>}
        {l.posted_at && <span className="muted small" style={{ alignSelf: "center" }}>{timeAgo(l.posted_at)}</span>}
      </>}
      trail={onDismiss ? <button className="icon-btn" aria-label="Not interested" title="Not interested" onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDismiss(); }}><IconEyeOff /></button> : undefined}
    />
  );
}

export function AppCard({ a }: { a: ApplicationRow }) {
  return (
    <Link href={`/tracker/${a.id}`} className="kcard">
      <div className="row-flex" style={{ alignItems: "flex-start" }}>
        <div className="grow" style={{ minWidth: 0 }}>
          <div className="row-title" style={{ whiteSpace: "normal" }}>{a.employer}</div>
          <div className="row-sub" style={{ whiteSpace: "normal" }}>{a.title ?? "Role not recorded"}</div>
        </div>
        {a.score != null && <ScoreRing score={a.score} size={34} />}
      </div>
      <div className="row-meta">
        {a.before_tracker && <Pill>Before tracker</Pill>}
        {a.documents.length > 0 && <Pill icon={<IconDoc />}>Documents</Pill>}
        {!a.before_tracker && (a.applied_at ? <span className="muted small">Applied {timeAgo(a.applied_at)}</span> : <span className="muted small">{timeAgo(a.updated_at)}</span>)}
      </div>
    </Link>
  );
}

export function ApplicationRowItem({ a }: { a: ApplicationRow }) {
  return (
    <Row href={`/tracker/${a.id}`} title={a.employer} sub={a.title ?? "Role not recorded"}
      meta={<><StatusPill status={a.status} />{a.before_tracker && <Pill>Before tracker</Pill>}{a.documents.length > 0 && <Pill icon={<IconDoc />}>Documents</Pill>}</>}
      trail={a.score != null ? <ScoreRing score={a.score} size={38} /> : undefined} />
  );
}

export function DocumentsCard({ appId, files, employer }: { appId: number; files: string[]; employer?: string }) {
  const [preview, setPreview] = useState<string | null>(null);
  const label = (f: string) => (f.startsWith("resume") ? "Resume" : f.startsWith("cover") ? "Cover letter" : f) + (f.endsWith(".pdf") ? " (PDF)" : " (Word)");
  const order = ["resume.pdf", "cover_letter.pdf", "resume.docx", "cover_letter.docx"];
  const sorted = [...files].sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const url = (f: string) => `/api/v1/applications/${appId}/documents/${encodeURIComponent(f)}`;
  return (
    <>
      <div className="list">
        {sorted.map((f) => (
          <div className="row" key={f}>
            <div style={{ width: 40, height: 40, borderRadius: 11, background: f.endsWith(".pdf") ? "var(--red-tint)" : "var(--blue-tint)", color: f.endsWith(".pdf") ? "var(--red)" : "var(--blue)", display: "grid", placeItems: "center", flex: "none" }}><IconDoc style={{ width: 22, height: 22 }} /></div>
            <div className="row-main"><div className="row-title">{label(f)}</div><div className="row-sub">{f.endsWith(".pdf") ? "Opens in a preview" : "Editable in Word or Pages"}</div></div>
            {f.endsWith(".pdf") ? <button className="btn btn-tint btn-sm" onClick={() => setPreview(f)}>Preview</button> : null}
            <a className="icon-btn" href={url(f)} download aria-label={`Download ${label(f)}`}><IconDownload /></a>
          </div>
        ))}
      </div>
      <Sheet open={!!preview} onClose={() => setPreview(null)} title={preview ? `${label(preview)}${employer ? ` · ${employer}` : ""}` : ""} wide>
        {preview && <iframe className="preview" src={`${url(preview)}#view=FitH`} title={label(preview)} />}
      </Sheet>
    </>
  );
}

export function SectionHead({ title, href, label = "See all" }: { title: string; href?: string; label?: string }) {
  return <div className="section-head"><h2 className="section-title">{title}</h2>{href && <Link href={href} className="link">{label} <IconChevron style={{ width: 11, height: 11, verticalAlign: "-1px" }} /></Link>}</div>;
}

export function PageTop({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
      <div><h1 className="large-title">{title}</h1>{subtitle && <p className="subtitle" style={{ marginBottom: 14 }}>{subtitle}</p>}</div>
      {actions && <div className={cx("row-flex", "wrap")} style={{ paddingBottom: 8 }}>{actions}</div>}
    </div>
  );
}
