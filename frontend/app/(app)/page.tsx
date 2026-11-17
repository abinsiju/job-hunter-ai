"use client";
import Link from "next/link";
import { IconBolt, IconCheck, IconClock, IconJobs, IconSearch, IconStudio, IconTracker } from "@/components/icons";
import { JobRow, PageTop, SectionHead } from "@/components/shared";
import { useShell } from "@/components/shell";
import { Button, Empty, ErrorNote, ListSkeleton, Row, Skeleton } from "@/components/ui";
import { useApi } from "@/lib/api";
import { useNow } from "@/lib/hooks";
import { STATUS_COLOR, STATUS_LABEL, greeting, plural, timeAgo } from "@/lib/format";
import type { Overview } from "@/lib/types";

export default function Today() {
  const { data: o, error, loading, reload } = useApi<Overview>("/overview", { refreshMs: 30000 });
  const { openFind } = useShell();
  const now = useNow();
  const date = now ? now.toLocaleDateString("en-AU", { weekday: "long", day: "numeric", month: "long" }) : " ";
  const waiting = o ? o.attention.length : 0;
  const interviews = o?.pipeline.find((p) => p.status === "interview")?.count ?? 0;
  const total = o ? o.pipeline.reduce((s, p) => s + p.count, 0) : 0;

  return (
    <>
      <PageTop title={o?.owner.first_name ? `${greeting(now)}, ${o.owner.first_name}` : greeting(now)} subtitle={date}
        actions={<Button onClick={openFind}><IconSearch />Find jobs</Button>} />
      <ErrorNote error={error} retry={reload} />

      <div className="stats" style={{ marginTop: 8 }}>
        {loading && !o ? [0, 1, 2, 3].map((i) => <div className="stat" key={i}><Skeleton h={14} w="55%" /><div style={{ height: 14 }} /><Skeleton h={32} w="40%" /></div>) : o && <>
          <Link href="/jobs" className="stat"><div className="stat-label"><IconJobs style={{ width: 16, height: 16 }} />Worth a look</div><div className="stat-value num">{o.listings.pass + o.listings.review}</div><div className="stat-note">{plural(o.listings.pass, "clear match", "clear matches")}</div></Link>
          <Link href="/tracker" className="stat"><div className="stat-label"><IconClock style={{ width: 16, height: 16 }} />Waiting on you</div><div className="stat-value num">{waiting}</div><div className="stat-note">{waiting ? "Review or record" : "All caught up"}</div></Link>
          <Link href="/tracker" className="stat"><div className="stat-label"><IconCheck style={{ width: 16, height: 16 }} />Applied</div><div className="stat-value num">{o.applied_total}</div><div className="stat-note">Across every employer</div></Link>
          <Link href="/tracker" className="stat"><div className="stat-label"><IconBolt style={{ width: 16, height: 16 }} />Interviews</div><div className="stat-value num">{interviews}</div><div className="stat-note">{interviews ? "Good work" : "None yet"}</div></Link>
        </>}
      </div>

      {o && o.attention.length > 0 && (<>
        <SectionHead title="Needs your attention" href="/tracker" label="Open tracker" />
        <div className="list">
          {o.attention.map((a) => (
            <Row key={`${a.kind}-${a.application_id}`} href={`/tracker/${a.application_id}`}
              lead={<div style={{ width: 40, height: 40, borderRadius: 12, display: "grid", placeItems: "center", flex: "none", background: a.kind === "review_draft" ? "var(--orange-tint)" : "var(--blue-tint)", color: a.kind === "review_draft" ? "var(--orange)" : "var(--blue)" }}>{a.kind === "review_draft" ? <IconStudio style={{ width: 21, height: 21 }} /> : <IconTracker style={{ width: 21, height: 21 }} />}</div>}
              title={a.title} sub={a.detail} />
          ))}
        </div>
      </>)}

      <div className="grid-2">
        <div>
          <SectionHead title="Top matches" href="/jobs" />
          {loading && !o ? <ListSkeleton rows={4} /> : o && o.top_matches.length > 0 ? (
            <div className="list">{o.top_matches.map((l) => <JobRow key={l.id} l={l} />)}</div>
          ) : (
            <div className="card"><Empty icon={<IconSearch />} title="No jobs yet" action={<Button onClick={openFind}>Find jobs</Button>}>Search Adzuna or read an employer feed. Every job is scored against your verified profile and checked against your rules.</Empty></div>
          )}
        </div>
        <div>
          <SectionHead title="Your pipeline" href="/tracker" label="Tracker" />
          <div className="card card-pad">
            {o && total > 0 ? (<>
              <div className="funnel" role="img" aria-label="Applications by stage">
                {o.pipeline.filter((p) => p.count).map((p) => <i key={p.status} style={{ flex: p.count, background: STATUS_COLOR[p.status] }} title={`${STATUS_LABEL[p.status]}: ${p.count}`} />)}
              </div>
              <div className="legend">{o.pipeline.filter((p) => p.count).map((p) => <span key={p.status}><span className="dot" style={{ background: STATUS_COLOR[p.status] }} /><b>{p.count}</b> {STATUS_LABEL[p.status]}</span>)}</div>
            </>) : <div className="muted">{loading ? <Skeleton h={12} /> : "Nothing tracked yet. Drafts and applications appear here."}</div>}
          </div>
          <SectionHead title="Recent activity" />
          {o && o.activity.length > 0 ? (
            <div className="list">{o.activity.slice(0, 5).map((e, i) => (
              <Row key={i} href={`/tracker/${e.application_id}`} title={`${STATUS_LABEL[e.status]}: ${e.employer}`} sub={`${e.title ?? "Role not recorded"} · ${timeAgo(e.at)}`} />
            ))}</div>
          ) : <div className="card card-pad muted">Activity from new drafts and applications shows up here.</div>}
        </div>
      </div>
    </>
  );
}
