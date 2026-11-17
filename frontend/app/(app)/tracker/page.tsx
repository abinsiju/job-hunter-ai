"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { IconPlus, IconTracker } from "@/components/icons";
import { AppCard, ApplicationRowItem, PageTop } from "@/components/shared";
import { Button, Callout, Empty, ErrorNote, ListSkeleton, SearchBox, Segmented, Sheet, useToast } from "@/components/ui";
import { api, invalidate, useApi } from "@/lib/api";
import { useMediaQuery } from "@/lib/hooks";
import { STATUS_COLOR, plural } from "@/lib/format";
import type { ApplicationRow, ApplicationsResponse, Status } from "@/lib/types";

const COLUMNS: { key: string; label: string; statuses: Status[]; color: string }[] = [
  { key: "drafted", label: "Draft", statuses: ["drafted"], color: STATUS_COLOR.drafted }, { key: "approved", label: "Approved", statuses: ["approved"], color: STATUS_COLOR.approved },
  { key: "applied", label: "Applied", statuses: ["applied"], color: STATUS_COLOR.applied }, { key: "interview", label: "Interview", statuses: ["interview"], color: STATUS_COLOR.interview },
  { key: "offer", label: "Offer", statuses: ["offer"], color: STATUS_COLOR.offer }, { key: "closed", label: "Closed", statuses: ["rejected", "withdrawn"], color: STATUS_COLOR.rejected },
];
type Filter = "all" | "action" | "applied" | "interviews" | "closed";
const FILTERS: Record<Filter, Status[] | null> = { all: null, action: ["drafted", "approved"], applied: ["applied"], interviews: ["interview", "offer"], closed: ["rejected", "withdrawn"] };

export default function Tracker() {
  const { data, error, loading, reload } = useApi<ApplicationsResponse>("/applications");
  const wide = useMediaQuery("(min-width: 900px)");
  const [picked, setPicked] = useState<"board" | "list" | null>(null);
  const view = picked ?? (wide ? "board" : "list");  // board on desktop, list on phones, unless you choose
  const setView = setPicked;
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [logOpen, setLogOpen] = useState(false);

  const items = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (data?.items ?? []).filter((a) => (!ql || `${a.employer} ${a.title ?? ""} ${a.location ?? ""}`.toLowerCase().includes(ql)) && (!FILTERS[filter] || FILTERS[filter]!.includes(a.status)));
  }, [data, q, filter]);
  const counts = data?.counts;
  const action = (counts?.drafted ?? 0) + (counts?.approved ?? 0);

  return (
    <>
      <PageTop title="Tracker" subtitle={data ? `${plural(data.total, "application")} · ${action ? `${action} waiting on you` : "all caught up"}` : "Every application, from draft to offer"}
        actions={<Button onClick={() => setLogOpen(true)}><IconPlus />Log application</Button>} />
      <div className="filters">
        <SearchBox value={q} onChange={setQ} placeholder="Search employer, role or place" />
        <div className="row-flex wrap" style={{ justifyContent: "space-between" }}>
          {view === "list" ? <Segmented value={filter} onChange={setFilter} options={[
            { value: "all", label: "All", count: data?.total }, { value: "action", label: "Needs action", count: action }, { value: "applied", label: "Applied", count: counts?.applied },
            { value: "interviews", label: "Interviews", count: (counts?.interview ?? 0) + (counts?.offer ?? 0) }, { value: "closed", label: "Closed", count: (counts?.rejected ?? 0) + (counts?.withdrawn ?? 0) }]} /> : <span />}
          <Segmented value={view} onChange={setView} options={[{ value: "board", label: "Board" }, { value: "list", label: "List" }]} />
        </div>
      </div>
      <ErrorNote error={error} retry={reload} />
      {loading && !data ? <ListSkeleton rows={6} /> : data && data.total === 0 ? (
        <div className="card"><Empty icon={<IconTracker />} title="Nothing tracked yet" action={<Button onClick={() => setLogOpen(true)}>Log an application</Button>}>Drafts you make appear here automatically. You can also log a job you found yourself.</Empty></div>
      ) : view === "board" ? (
        <div className="board">
          {COLUMNS.map((col) => {
            const rows = items.filter((a) => col.statuses.includes(a.status));
            return (
              <section className="col" key={col.key} aria-label={col.label}>
                <div className="col-head"><span><span className="dot" style={{ background: col.color }} />{col.label}</span><span>{rows.length}</span></div>
                {rows.length ? rows.map((a) => <AppCard key={a.id} a={a} />) : <div className="muted small" style={{ padding: "10px 8px 14px" }}>Nothing here</div>}
              </section>
            );
          })}
        </div>
      ) : items.length ? <div className="list">{items.map((a) => <ApplicationRowItem key={a.id} a={a} />)}</div> : <div className="card"><Empty icon={<IconTracker />} title="No matches">Nothing matches this filter.</Empty></div>}
      <LogSheet open={logOpen} onClose={() => setLogOpen(false)} />
    </>
  );
}

function LogSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const [f, setF] = useState({ employer: "", title: "", location: "", url: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  async function save() {
    setBusy(true); setErr("");
    try {
      const a = await api<ApplicationRow>("/applications", { method: "POST", json: { employer: f.employer, title: f.title, location: f.location || null, url: f.url || null, source: "manual" } });
      invalidate(); toast("Logged as a draft"); onClose(); setF({ employer: "", title: "", location: "", url: "" }); router.push(`/tracker/${a.id}`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <Sheet open={open} onClose={onClose} title="Log an application">
      <div className="stack">
        <p className="muted" style={{ margin: 0 }}>For a job you found yourself. It starts as a draft, and a vacancy you already applied to is blocked.</p>
        <div className="field"><label htmlFor="le">Employer</label><input id="le" className="input" value={f.employer} onChange={set("employer")} /></div>
        <div className="field"><label htmlFor="lt">Role</label><input id="lt" className="input" value={f.title} onChange={set("title")} /></div>
        <div className="field"><label htmlFor="ll">Location (optional)</label><input id="ll" className="input" value={f.location} onChange={set("location")} /></div>
        <div className="field"><label htmlFor="lu">Link (optional)</label><input id="lu" className="input" value={f.url} onChange={set("url")} inputMode="url" /></div>
        {err && <Callout tone="bad">{err}</Callout>}
        <Button size="lg" block busy={busy} disabled={!f.employer.trim() || !f.title.trim()} onClick={save}>Save</Button>
      </div>
    </Sheet>
  );
}
