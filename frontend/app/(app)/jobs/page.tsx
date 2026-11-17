"use client";
import { useEffect, useMemo, useState } from "react";
import { IconMore, IconRefresh, IconSearch } from "@/components/icons";
import { JobRow, PageTop } from "@/components/shared";
import { useShell } from "@/components/shell";
import { Button, Callout, Empty, ErrorNote, ListSkeleton, SearchBox, Segmented, Sheet, useToast } from "@/components/ui";
import { api, invalidate, useApi, useJob } from "@/lib/api";
import { plural } from "@/lib/format";
import { recall, remember } from "@/lib/remember";
import type { ListingsResponse } from "@/lib/types";

type Tab = "open" | "pass" | "review" | "fail" | "closed" | "dismissed";
const PAGE = 40;

export default function Jobs() {
  const [tab, setTab] = useState<Tab>("open");
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [sort, setSort] = useState<"rank" | "newest">("rank");
  const filterKey = `${tab}|${dq}|${sort}`;
  const [paging, setPaging] = useState({ key: "", n: PAGE });
  const limit = paging.key === filterKey ? paging.n : PAGE;  // paging resets whenever a filter changes
  const [menu, setMenu] = useState(false);
  const [restored, setRestored] = useState(false);
  const { openFind } = useShell();
  // 20 frequency of use: Jobs opens on the tab and sort you used last (loaded after the first paint)
  useEffect(() => {
    const t = setTimeout(() => { const m = recall<{ tab?: Tab; sort?: "rank" | "newest" } | null>("jobs", null); if (m?.tab) setTab(m.tab); if (m?.sort) setSort(m.sort); setRestored(true); }, 0);
    return () => clearTimeout(t);
  }, []);
  useEffect(() => { if (restored) remember("jobs", { tab, sort }); }, [restored, tab, sort]);
  const toast = useToast();
  useEffect(() => { const t = setTimeout(() => setDq(q), 250); return () => clearTimeout(t); }, [q]);
  const path = useMemo(() => `/listings?gate=${tab}&sort=${sort}&limit=${limit}${dq ? `&q=${encodeURIComponent(dq)}` : ""}`, [tab, sort, limit, dq]);
  const { data, error, loading, reload } = useApi<ListingsResponse>(path);
  const rescore = useJob();
  const c = data?.counts;

  async function dismiss(id: number, restore = false) {
    try {
      await api(`/listings/${id}/${restore ? "restore" : "dismiss"}`, { method: "POST" });
      invalidate("/listings"); invalidate("/overview"); reload();
      toast(restore ? "Moved back to your list" : "Hidden. You can find it under Hidden.");
    } catch (e) { toast((e as Error).message, "err"); }
  }

  return (
    <>
      <PageTop title="Jobs" subtitle={c ? `${plural(c.pass + c.review, "job")} worth a look · ${c.pass} clear` : "Scored against your profile and rules"}
        actions={<><Button variant="gray" size="sm" onClick={() => setMenu(true)} aria-label="More"><IconMore /></Button><Button onClick={openFind}><IconSearch />Find jobs</Button></>} />
      <div className="filters">
        <SearchBox value={q} onChange={setQ} placeholder="Search title, employer or place" />
        <div className="row-flex wrap" style={{ justifyContent: "space-between" }}>
          <Segmented value={tab} onChange={setTab} options={[
            { value: "open", label: "Worth a look", count: c ? c.pass + c.review : undefined }, { value: "pass", label: "Clear", count: c?.pass },
            { value: "review", label: "Check first", count: c?.review }, { value: "fail", label: "Not eligible", count: c?.fail }, { value: "closed", label: "Closed", count: c?.closed }, { value: "dismissed", label: "Hidden", count: c?.dismissed }]} />
          <Segmented value={sort} onChange={setSort} options={[{ value: "rank", label: "Best match" }, { value: "newest", label: "Newest" }]} />
        </div>
      </div>
      <ErrorNote error={error} retry={reload} />
      {c && c.untriaged > 0 && <div style={{ marginBottom: 14 }}><Callout tone="info">{plural(c.untriaged, "listing")} found but not scored yet. Open Find jobs and run it again to score more.</Callout></div>}
      {loading && !data ? <ListSkeleton rows={6} /> : data && data.items.length > 0 ? (<>
        <div className="list">
          {data.items.map((l) => tab === "dismissed"
            ? <div key={l.id} className="row" style={{ cursor: "default" }}><div className="grow" style={{ minWidth: 0 }}><div className="row-title">{l.title}</div><div className="row-sub">{l.employer} · {l.location}</div></div><Button variant="tint" size="sm" onClick={() => dismiss(l.id, true)}>Restore</Button></div>
            : <JobRow key={l.id} l={l} onDismiss={tab === "fail" ? undefined : () => dismiss(l.id)} />)}
        </div>
        {data.total > data.items.length && <div style={{ textAlign: "center", marginTop: 16 }}><Button variant="gray" onClick={() => setPaging({ key: filterKey, n: limit + PAGE })}>Show {Math.min(PAGE, data.total - data.items.length)} more</Button></div>}
        <p className="muted small" style={{ textAlign: "center", marginTop: 16 }}>{plural(data.total, "job")}{data.total > data.items.length ? ` · showing ${data.items.length}` : ""}</p>
      </>) : data ? (
        <div className="card"><Empty icon={<IconSearch />} title={dq ? "No matches" : tab === "fail" ? "Nothing excluded" : tab === "dismissed" ? "Nothing hidden" : "No jobs here yet"}
          action={tab === "open" && !dq ? <Button onClick={openFind}>Find jobs</Button> : undefined}>
          {dq ? `Nothing matches “${dq}”. Try a shorter search.` : tab === "fail" ? "Jobs that break your hard rules (citizenship, clearance, fees) are listed here, never in your main list." : tab === "open" ? "Search for jobs to see them scored against your profile." : "Nothing in this view."}</Empty></div>
      ) : null}

      <Sheet open={menu} onClose={() => { if (!rescore.running) setMenu(false); }} title="More">
        <div className="stack">
          <p className="muted" style={{ margin: 0 }}>Re-score applies new rules or profile changes to every stored listing. It takes about six seconds per job.</p>
          {rescore.job?.status === "done" && <Callout tone="good">Re-scored {(rescore.job.result as { scored: number } | null)?.scored} listings.</Callout>}
          {rescore.job?.status === "error" && <Callout tone="bad">{rescore.job.error}</Callout>}
          <Button variant="tint" block busy={rescore.running} onClick={() => rescore.run("rescore", { limit: 30 }).then(() => { reload(); }).catch((e) => toast(e.message, "err"))}><IconRefresh />{rescore.running ? rescore.job?.message ?? "Working" : "Re-score all listings"}</Button>
        </div>
      </Sheet>
    </>
  );
}
