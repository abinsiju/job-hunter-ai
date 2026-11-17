"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { IconBolt, IconBuilding, IconSearch } from "./icons";
import { Button, Callout, Segmented, Sheet, Switch } from "./ui";
import { useJob, useApi } from "@/lib/api";
import { plural } from "@/lib/format";
import type { DiscoverResult, SystemStatus } from "@/lib/types";

export function FindJobsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [mode, setMode] = useState<"adzuna" | "careers">("adzuna");
  const [what, setWhat] = useState("it support");
  const [where, setWhere] = useState("Melbourne");
  const [pages, setPages] = useState(1);
  const [partTime, setPartTime] = useState(false);
  const [fullTime, setFullTime] = useState(false);
  const { job, running, run, reset } = useJob<DiscoverResult>();
  const { data: sys } = useApi<SystemStatus>(open ? "/system" : null);
  const configured = sys?.discovery.adzuna_configured ?? true;

  const go = () => run(mode === "adzuna" ? "discover_adzuna" : "discover_careers",
    mode === "adzuna" ? { what, where, pages, part_time: partTime, full_time: fullTime, limit: 15 } : { limit: 15 }).catch(() => {});
  const close = () => { if (!running) { reset(); onClose(); } };
  const r = job?.result;

  return (
    <Sheet open={open} onClose={close} title="Find jobs">
      {!job && !running && (
        <div className="stack">
          <Segmented value={mode} onChange={setMode} options={[{ value: "adzuna", label: "Search Adzuna" }, { value: "careers", label: "Employer feeds" }]} />
          {mode === "adzuna" ? (
            <>
              {!configured && <Callout tone="warn">Adzuna keys are not set yet. Add <span className="mono">ADZUNA_APP_ID</span> and <span className="mono">ADZUNA_APP_KEY</span> to <span className="mono">.env</span>, then restart the API.</Callout>}
              <div className="field"><label htmlFor="what">What</label><input id="what" className="input" value={what} onChange={(e) => setWhat(e.target.value)} placeholder="it support, service desk…" /></div>
              <div className="field"><label htmlFor="where">Where</label><input id="where" className="input" value={where} onChange={(e) => setWhere(e.target.value)} placeholder="Melbourne" /></div>
              <div className="card card-pad" style={{ boxShadow: "none" }}>
                <Switch checked={partTime} onChange={(v) => { setPartTime(v); if (v) setFullTime(false); }} label="Part-time only" />
                <Switch checked={fullTime} onChange={(v) => { setFullTime(v); if (v) setPartTime(false); }} label="Full-time only" />
                <div className="toggle-row"><span>Pages of results</span><Segmented value={String(pages)} onChange={(v) => setPages(Number(v))} options={[{ value: "1", label: "1" }, { value: "2", label: "2" }, { value: "3", label: "3" }]} /></div>
              </div>
              <p className="muted small" style={{ margin: 0 }}>Adzuna shares a short preview of each ad. Previews are scored but marked “Check first” until you read the full ad.</p>
            </>
          ) : (
            <p className="muted" style={{ margin: 0 }}>Reads the public job feed that First Focus publishes on its own careers page (full ads, Australia only). Other employers on your watch-list are opened in the browser.</p>
          )}
          <Button size="lg" block onClick={go} disabled={mode === "adzuna" && (!what.trim() || !configured)}>{mode === "adzuna" ? <><IconSearch />Search and score</> : <><IconBuilding />Fetch and score</>}</Button>
        </div>
      )}
      {(running || (job && job.status !== "done" && job.status !== "error")) && (
        <div className="empty" style={{ padding: "30px 10px" }}>
          <div className="spinner" style={{ width: 34, height: 34, margin: "0 auto 14px" }} />
          <h3>{job?.message ?? "Starting"}…</h3>
          <p style={{ margin: 0 }}>Each new listing takes about six seconds to read and score on your Mac. You can close this and keep working.</p>
        </div>
      )}
      {job?.status === "error" && (
        <div className="stack"><Callout tone="bad">{job.error}</Callout><Button variant="gray" block onClick={reset}>Back</Button></div>
      )}
      {job?.status === "done" && r && (
        <div className="stack">
          <Callout tone="good"><b>Done.</b> Found {plural(r.fetched ?? 0, "listing")}: {r.new ?? 0} new, {r.known ?? 0} already seen. Scored {r.scored}.{r.waiting_to_score > 0 && ` ${r.waiting_to_score} more are waiting; run it again to score them.`}</Callout>
          <Button size="lg" block onClick={() => { onClose(); reset(); router.push("/jobs"); router.refresh(); }}><IconBolt />See the results</Button>
          <Link className="link" style={{ textAlign: "center", display: "block" }} href="#" onClick={(e) => { e.preventDefault(); reset(); }}>Search again</Link>
        </div>
      )}
    </Sheet>
  );
}
