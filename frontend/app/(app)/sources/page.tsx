"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { IconLink } from "@/components/icons";
import { PageTop } from "@/components/shared";
import { Button, Callout, ErrorNote, Pill, Skeleton, useToast } from "@/components/ui";
import { api, invalidate, useApi, useJob } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { normaliseUrl, parseCount, parseMoney, tidy } from "@/lib/forgive";
import { recall, remember } from "@/lib/remember";
import type { ImportResult, SourceCard, SourceSearchResult, SourcesResponse } from "@/lib/types";

const STATUS: Record<string, { label: string; tone: string }> = {
  API_CONNECTED: { label: "API connected", tone: "green" }, FEED_CONNECTED: { label: "Authorised feed", tone: "green" }, PUBLIC_PAGES: { label: "Public employer pages", tone: "green" },
  MANUAL_IMPORT: { label: "Manual import", tone: "blue" }, SEARCH_LINK_ONLY: { label: "Search link only", tone: "orange" }, APPROVAL_PENDING: { label: "Approval pending", tone: "indigo" },
  DISABLED: { label: "Off", tone: "" }, ERROR: { label: "Error", tone: "red" },
};
const METHOD: Record<string, string> = { api: "API", authorised_feed: "Authorised feed", public_pages: "Public pages", browser_search: "Browser search", manual_import: "Manual import" };
const PERMISSION: Record<string, string> = { permitted: "Permitted", approval_required: "Needs the platform's approval", no_permission: "No permission for automation", user_supplied_only: "Only what you provide" };
const ROLES = ["IT Support Officer", "Help Desk Analyst", "Service Desk Analyst", "Desktop Support", "Junior IT Technician", "Technical Support Engineer", "ICT Support", "MSP Support", "Junior Network Technician", "Junior Systems Administrator"];
const TYPES: [string, string][] = [["full_time", "Full-time"], ["part_time", "Part-time"], ["casual", "Casual"], ["contract", "Contract"]];

function SourceBox({ s, onToggle }: { s: SourceCard; onToggle: () => void }) {
  const st = STATUS[s.status] ?? { label: s.status, tone: "" };
  return (
    <div className="card card-pad stack" data-testid={`source-${s.source}`} style={{ gap: 10 }}>
      <div className="row-flex wrap" style={{ gap: 8, justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0, fontSize: 18 }}>{s.label}</h3>
        <Pill tone={st.tone}>{st.label}</Pill>
      </div>
      <div className="chips">{s.methods.map((m) => <span className="chip" key={m}>{METHOD[m] ?? m}</span>)}</div>
      <p className="muted" style={{ margin: 0, fontSize: 14 }}><b>{s.real_data_connection ? "Real data connection." : "Not a data connection."}</b> {s.permission_note}</p>
      <div className="kv"><span>Permission</span><span>{PERMISSION[s.permission] ?? s.permission}</span></div>
      <div className="kv"><span>Jobs discovered</span><span>{s.jobs_discovered}</span></div>
      <div className="kv"><span>Credentials</span><span>{s.auth === "none_needed" ? "None needed" : s.auth === "configured" ? "Stored (not used)" : "Not set"}</span></div>
      {s.real_data_connection && <div className="kv"><span>Last successful fetch</span><span>{s.last_success_at ? timeAgo(s.last_success_at) : "Never"}</span></div>}
      {s.search_link && <div className="kv"><span>Search link</span><span>Available{s.last_link_at ? ` · last made ${timeAgo(s.last_link_at)}` : ""}</span></div>}
      {s.manual_import && <div className="kv"><span>Last import</span><span>{s.last_import_at ? timeAgo(s.last_import_at) : "None yet"}</span></div>}
      {s.last_error && <Callout tone="bad">{s.last_error} {s.last_error_at ? `(${timeAgo(s.last_error_at)})` : ""}</Callout>}
      {s.notes.map((n, i) => <p className="muted small" style={{ margin: 0 }} key={i}>{n}</p>)}
      <div className="row-flex" style={{ gap: 12 }}>
        {s.docs_url && <a className="link small" href={s.docs_url} target="_blank" rel="noopener noreferrer">Official documentation</a>}
        <button className="link small" onClick={onToggle}>{s.enabled ? "Turn off" : "Turn on"}</button>
      </div>
    </div>
  );
}

export default function Sources() {
  const toast = useToast();
  const { data, error, reload } = useApi<SourcesResponse>("/sources", { refreshMs: 20000 });
  const search = useJob<SourceSearchResult>();
  const [q, setQ] = useState({ keywords: "IT Support Officer", city: "Melbourne", suburb: "", distance_km: "", salary_min: "", salary_max: "", salary_period: "year", level: "", date_posted_days: "7", employer: "", industry: "" });
  const [types, setTypes] = useState<string[]>([]);
  const [chosen, setChosen] = useState<string[]>([]);
  const [imp, setImp] = useState({ source: "seek", url: "", title: "", employer: "", location: "", text: "", application_url: "" });
  const [impRes, setImpRes] = useState<ImportResult | null>(null);
  const [impErr, setImpErr] = useState("");
  const [busy, setBusy] = useState(false);
  // 15 Hick: five choices up front; the rest wait behind "More filters". 16 Miller: six role shortcuts, the others on request.
  const [more, setMore] = useState(false);
  const [allRoles, setAllRoles] = useState(false);
  const resRef = useRef<HTMLDivElement | null>(null);
  const [restored, setRestored] = useState(false);
  // 20 frequency of use: the search is left the way you last used it (loaded after the first paint so the page and server agree)
  useEffect(() => {
    const t = setTimeout(() => {
      const m = recall<{ city?: string; days?: string; types?: string[]; chosen?: string[]; more?: boolean } | null>("sources", null);
      if (m) { setQ((x) => ({ ...x, city: m.city ?? x.city, date_posted_days: m.days ?? x.date_posted_days })); setTypes(m.types ?? []); setChosen(m.chosen ?? []); setMore(!!m.more); }
      setRestored(true);  // only now may the page start saving, so the defaults can never overwrite what you chose last time
    }, 0);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => { if (restored) remember("sources", { city: q.city, days: q.date_posted_days, types, chosen, more }); }, [restored, q.city, q.date_posted_days, types, chosen, more]);

  function criteria() {
    const num = (v: string) => (v.trim() === "" ? null : parseMoney(v)); // 18 Postel: "80k", "$80,000" and "80 000" all work
    const count = (v: string) => (v.trim() === "" ? null : parseCount(v));
    return { keywords: tidy(q.keywords), city: q.city, suburb: tidy(q.suburb), distance_km: count(q.distance_km), salary_min: num(q.salary_min), salary_max: num(q.salary_max), salary_period: q.salary_period,
      employment_types: types, level: q.level, date_posted_days: num(q.date_posted_days), employer: tidy(q.employer), industry: q.industry, sources: chosen };
  }
  async function runSearch() {
    const j = await search.run("source_search", criteria()).catch((e: Error) => { toast(e.message, "err"); return null; });
    if (j?.status === "error") toast(j.error ?? "Search failed", "err");
    reload(); invalidate("/listings");
  }
  async function doImport() {
    setBusy(true); setImpErr(""); setImpRes(null);
    try {
      const r = await api<ImportResult>(`/sources/${imp.source}/import`, { method: "POST", json: { text: imp.text, url: normaliseUrl(imp.url) || null, title: tidy(imp.title), employer: tidy(imp.employer), location: tidy(imp.location), application_url: normaliseUrl(imp.application_url) || null } });
      setImpRes(r); setImp({ ...imp, url: "", title: "", text: "", application_url: "" }); invalidate(); reload();
    } catch (e) { setImpErr((e as Error).message); } finally { setBusy(false); }
  }
  async function toggle(s: SourceCard) {
    try { await api(`/sources/${s.source}/enabled`, { method: "PUT", json: { enabled: !s.enabled } }); reload(); } catch (e) { toast((e as Error).message, "err"); }
  }
  const res = search.job?.status === "done" ? search.job.result : null;
  useEffect(() => { if (res) resRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }); }, [res]);
  const set = (k: keyof typeof q) => (e: { target: { value: string } }) => setQ({ ...q, [k]: e.target.value });

  return (
    <>
      <PageTop title="Sources" subtitle="Where your jobs come from, and what each source really does" />
      <div style={{ marginBottom: 14 }}>
        <Callout tone="info"><b>Only some sources give this app data.</b> Employer feeds, employer career pages and Adzuna return listings by themselves. SEEK, Indeed and Jora only give you a search link and manual import: their terms do not allow automated reading, so this app never scrapes them, never drives them with a bot, and never automates Indeed Apply.</Callout>
      </div>

      <div className="card card-pad stack" data-testid="search-card">
        <h2 className="section-title" style={{ margin: 0 }}>Search all sources</h2>
        <div className="chips">{(allRoles ? ROLES : ROLES.slice(0, 6)).map((r) => <button key={r} className="chip" onClick={() => setQ({ ...q, keywords: r })} aria-pressed={q.keywords === r}>{r}</button>)}<button className="link small" onClick={() => setAllRoles(!allRoles)}>{allRoles ? "Fewer roles" : `More roles (${ROLES.length - 6})`}</button></div>
        <div className="grid-2" style={{ gap: 12 }}>
          <div className="field"><label htmlFor="kw">Keywords or job title</label><input id="kw" className="input" value={q.keywords} onChange={set("keywords")} /></div>
          <div className="field"><label htmlFor="city">City</label>
            <select id="city" className="input" value={q.city} onChange={set("city")}><option>Melbourne</option><option>Sydney</option><option>Canberra</option><option>Brisbane</option><option>Adelaide</option><option>Perth</option><option value="">Anywhere in Australia</option></select></div>
          <div className="field"><label htmlFor="days">Posted within</label>
            <select id="days" className="input" value={q.date_posted_days} onChange={set("date_posted_days")}><option value="1">24 hours</option><option value="3">3 days</option><option value="7">7 days</option><option value="14">14 days</option><option value="31">31 days</option><option value="">Any time</option></select></div>
        </div>
        <button className="link" onClick={() => setMore(!more)} aria-expanded={more} aria-controls="more-filters">{more ? "Fewer filters" : "More filters (suburb, distance, salary, level, employer)"}</button>
        {more && <div id="more-filters" className="grid-2 stage" style={{ gap: 12 }}>
          <div className="field"><label htmlFor="sub">Suburb (optional)</label><input id="sub" className="input" value={q.suburb} onChange={set("suburb")} /></div>
          <div className="field"><label htmlFor="dist">Distance (km, optional)</label><input id="dist" className="input" inputMode="numeric" value={q.distance_km} onChange={set("distance_km")} /></div>
          <div className="field"><label htmlFor="smin">Salary from</label><input id="smin" className="input" inputMode="numeric" value={q.salary_min} onChange={set("salary_min")} /></div>
          <div className="field"><label htmlFor="smax">Salary to</label><input id="smax" className="input" inputMode="numeric" value={q.salary_max} onChange={set("salary_max")} /></div>
          <div className="field"><label htmlFor="per">Salary is per</label><select id="per" className="input" value={q.salary_period} onChange={set("salary_period")}><option value="year">year</option><option value="hour">hour</option></select></div>
          <div className="field"><label htmlFor="lvl">Level</label><select id="lvl" className="input" value={q.level} onChange={set("level")}><option value="">Any</option><option value="entry">Entry level</option><option value="junior">Junior</option><option value="graduate">Graduate</option></select></div>
          <div className="field"><label htmlFor="emp">Employer (optional)</label><input id="emp" className="input" value={q.employer} onChange={set("employer")} /></div>
        </div>}
        <div><div className="muted small" style={{ marginBottom: 6 }}>Employment type</div>
          <div className="chips">{TYPES.map(([v, l]) => <button key={v} className="chip" aria-pressed={types.includes(v)} onClick={() => setTypes(types.includes(v) ? types.filter((x) => x !== v) : [...types, v])}>{l}</button>)}</div></div>
        <div><div className="muted small" style={{ marginBottom: 6 }}>Sources (none selected = all)</div>
          <div className="chips">{(data?.sources ?? []).filter((s) => s.enabled).map((s) => <button key={s.source} className="chip" aria-pressed={chosen.includes(s.source)} onClick={() => setChosen(chosen.includes(s.source) ? chosen.filter((x) => x !== s.source) : [...chosen, s.source])}>{s.label}</button>)}</div></div>
        <Button size="lg" busy={search.running} onClick={runSearch}>{search.running ? search.job?.message ?? "Searching" : "Search"}</Button>
        <p className="muted small" style={{ margin: 0 }}>Work-rights rules are applied after you import a job, never sent to a job board.</p>
      </div>

      {res && (
        <div ref={resRef} className="stack stage" style={{ marginTop: 14, scrollMarginTop: 70 }} data-testid="search-results">
          <h2 className="section-title" style={{ margin: "0 4px" }}>Results by source</h2>
          {res.sources.map((r) => (
            <div className="card card-pad stack" key={r.source} data-testid={`result-${r.source}`} style={{ gap: 8 }}>
              <div className="row-flex wrap" style={{ gap: 8, justifyContent: "space-between" }}>
                <b>{r.label}</b>
                {!r.ok ? <Pill tone="red">Failed</Pill> : r.fetched_data ? <Pill tone="green">{r.jobs_found} jobs returned</Pill> : <Pill tone="orange">Search link only: nothing fetched</Pill>}
              </div>
              {r.ok && r.fetched_data && <p className="muted" style={{ margin: 0, fontSize: 14 }}>{r.stored.created} new · {r.stored.merged} already known from another source · {r.stored.known} seen before{r.stored.skipped_invalid ? ` · ${r.stored.skipped_invalid} skipped (bad link)` : ""}</p>}
              {r.error && <Callout tone="bad">{r.error}</Callout>}
              {r.link && <>
                <a className="btn btn-tint" href={r.link} target="_blank" rel="noopener noreferrer"><IconLink />Open this search on {r.label}</a>
                <p className="muted small" style={{ margin: 0 }}>Opens in your own browser. Filters carried into the link: {r.filters_applied.join(", ") || "none"}.{r.filters_not_applied.length ? ` Not carried: ${r.filters_not_applied.join(", ")}.` : ""} Copy a listing you like and import it below.</p>
              </>}
            </div>
          ))}
          {res.scored != null && <Callout tone="info">Scored {res.scored} new job(s){res.waiting_to_score ? `, ${res.waiting_to_score} still waiting` : ""}. <Link className="link" href="/jobs">Open Jobs</Link>.</Callout>}
        </div>
      )}

      <h2 className="section-title">Source status</h2>
      <ErrorNote error={error} retry={reload} />
      {!data ? <Skeleton h={180} r={20} /> : <div className="grid-2" style={{ alignItems: "start" }}>{data.sources.map((s) => <SourceBox key={s.source} s={s} onToggle={() => toggle(s)} />)}</div>}

      <h2 className="section-title">Import a job you found</h2>
      <div className="card card-pad stack" data-testid="import-card">
        <p className="muted" style={{ margin: 0, fontSize: 14.5 }}>Found a job on SEEK, Indeed or Jora in your own browser? Copy its text and paste it here. It is scored, checked against your work-rights rules, and de-duplicated against jobs you already have. Pasted text is treated as untrusted data.</p>
        <div className="grid-2" style={{ gap: 12 }}>
          <div className="field"><label htmlFor="isrc">Source</label><select id="isrc" className="input" value={imp.source} onChange={(e) => setImp({ ...imp, source: e.target.value })}><option value="seek">SEEK</option><option value="indeed">Indeed</option><option value="jora">Jora</option></select></div>
          <div className="field"><label htmlFor="iurl">Link to the listing (optional)</label><input id="iurl" className="input" inputMode="url" value={imp.url} onChange={(e) => setImp({ ...imp, url: e.target.value })} placeholder="https://…" /></div>
          <div className="field"><label htmlFor="ititle">Job title</label><input id="ititle" className="input" value={imp.title} onChange={(e) => setImp({ ...imp, title: e.target.value })} /></div>
          <div className="field"><label htmlFor="iemp">Employer</label><input id="iemp" className="input" value={imp.employer} onChange={(e) => setImp({ ...imp, employer: e.target.value })} /></div>
          <div className="field"><label htmlFor="iloc">Location</label><input id="iloc" className="input" value={imp.location} onChange={(e) => setImp({ ...imp, location: e.target.value })} /></div>
          <div className="field"><label htmlFor="iapp">Employer&apos;s application link (optional)</label><input id="iapp" className="input" inputMode="url" value={imp.application_url} onChange={(e) => setImp({ ...imp, application_url: e.target.value })} /></div>
        </div>
        <div className="field"><label htmlFor="itext">The job ad text</label><textarea id="itext" className="textarea" style={{ minHeight: 160 }} value={imp.text} onChange={(e) => setImp({ ...imp, text: e.target.value })} placeholder="Paste the whole ad here" /></div>
        {impErr && <Callout tone="bad">{impErr}</Callout>}
        {impRes && <Callout tone="good">{impRes.message} <Link className="link" href="/jobs">Open Jobs</Link>.</Callout>}
        <Button busy={busy} disabled={imp.text.trim().length < 40} onClick={doImport}>Import this job</Button>
      </div>
    </>
  );
}
