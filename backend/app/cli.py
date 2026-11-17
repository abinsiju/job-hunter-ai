"""CLI.
  cd backend && uv run python -m app.cli draft [AD_FILE|-] [--score-only] [--source S] [--url URL] [--today YYYY-MM-DD] [--out DIR]
  cd backend && uv run python -m app.cli track list [--status S] [--employer TEXT]
  cd backend && uv run python -m app.cli track show ID
  cd backend && uv run python -m app.cli track update ID STATUS [--evidence TEXT] [--note TEXT]
  cd backend && uv run python -m app.cli blocklist add "Employer" [--reason TEXT] | list | remove "Employer"
  cd backend && uv run python -m app.cli discover search --what "it support" --where Melbourne [--pages 1] [--limit 15]
  cd backend && uv run python -m app.cli discover careers | list [--all] | show ID | draft ID | rescore [--limit N]
Paste an ad via a file, or pipe/paste to stdin (end with Ctrl-D). Drafts only: nothing is ever submitted by this program;
a person applies, then records the confirmation with `track update ID applied --evidence ...`."""
import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from app.ai.factory import get_provider
from app.db.session import get_session
from app.documents.validate import GenerationRejected
from app.pipeline import draft
from app.tracker.service import ALLOWED, TrackerError, update_status
from app.db.models import Application, APPLICATION_STATUSES
from sqlalchemy import select


def _print(r) -> None:
    m = r.match
    print(f"\n{r.job.title}" + (f" - {r.job.employer}" if r.job.employer else "") + (f" ({r.job.location})" if r.job.location else ""))
    print(f"SCORE {m.score}/100   GATE {m.gate.status}\n")
    for name, c in m.breakdown.items():
        print(f"  {name:<22}{c.points:>5g}/{c.max_points}")
        for label, items in (("matched", c.matched), ("missing", c.missing), ("note", c.notes)):
            for i in items:
                print(f"      {label}: {i}")
    for x in m.gate.fail_reasons:
        print(f"\n  FAIL: {x}")
    for x in m.gate.flags:
        print(f"  FLAG: {x}")
    for x in r.warnings:
        print(f"  WARNING: {x}")
    if r.skipped_reason:
        print(f"\nNo documents generated. {r.skipped_reason}")
    if r.files:
        print("\nDrafts written (NOT submitted; review before use):")
        for p in r.files.values():
            print(f"  {p}")
    if r.application:
        print(f"\nTracked as #{r.application.id} (status {r.application.status}). Next: review the documents, then "
              f"`track update {r.application.id} approved`.")
    if r.duplicates and r.duplicates.same_employer:
        for a in r.duplicates.same_employer:
            print(f"  WARNING: same employer already tracked: #{a.id} {a.employer} / {a.title or 'role not recorded'} ({a.status}). Different role, so not blocked.")


def _d(dt) -> str:
    return dt.strftime("%d %b %Y") if dt else "-"


def _row(a: Application) -> str:
    return (f"{a.id:>3}  {a.status:<9} {a.employer[:26]:<26} {(a.title or '(role not recorded)')[:34]:<34} "
            f"{(a.location or '-')[:16]:<16} {_d(a.applied_at):<11} {'' if a.match_score is None else f'{a.match_score:g}'}")


def track(a) -> int:
    with get_session() as s:
        if a.track_cmd == "list":
            q = select(Application).order_by(Application.id)
            if a.status:
                q = q.where(Application.status == a.status)
            rows = [x for x in s.scalars(q) if not a.employer or a.employer.lower() in x.employer.lower()]
            print(f"{'ID':>3}  {'STATUS':<9} {'EMPLOYER':<26} {'TITLE':<34} {'LOCATION':<16} {'APPLIED':<11} SCORE")
            for x in rows:
                print(_row(x))
            counts = {k: sum(1 for x in rows if x.status == k) for k in APPLICATION_STATUSES}
            print("\n" + "  ".join(f"{k}: {v}" for k, v in counts.items() if v) + f"   (total {len(rows)})")
            return 0
        if a.track_cmd == "show":
            x = s.get(Application, a.id)
            if not x:
                print(f"No application #{a.id}", file=sys.stderr)
                return 2
            print(f"#{x.id} {x.employer} / {x.title or '(role not recorded)'} / {x.location or '-'}   status: {x.status}")
            print(f"source: {x.source}  url: {x.source_url or '-'}  score: {x.match_score}  gate: {x.gate_status}")
            print(f"evidence: {x.evidence or '-'}\nnotes: {x.notes or '-'}\ndocuments: {x.doc_paths or '-'}")
            for h in x.history or []:
                print(f"  {h['at'][:16]}  {h['status']:<9} {h.get('note') or ''}")
            return 0
        try:
            x = update_status(s, a.id, a.new_status, evidence=a.evidence, note=a.note)
        except TrackerError as e:
            print(f"Refused: {e}", file=sys.stderr)
            return 2
        print(f"#{x.id} {x.employer} is now {x.status}.")
        print(f"Allowed next: {', '.join(sorted(ALLOWED[x.status])) or 'none (final)'}")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobhunter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft", help="score a pasted job ad and draft a resume + cover letter")
    d.add_argument("ad", nargs="?", default="-", help="file with the job ad text, or - for stdin")
    d.add_argument("--score-only", action="store_true")
    d.add_argument("--source", default="manual", help="where the ad came from (adzuna, aps, career-page, manual)")
    d.add_argument("--url", default=None, help="listing URL (used for duplicate detection)")
    d.add_argument("--today", type=date.fromisoformat, default=None)
    d.add_argument("--out", type=Path, default=Path("../data/generated"))
    t = sub.add_parser("track", help="application tracker")
    ts = t.add_subparsers(dest="track_cmd", required=True)
    tl = ts.add_parser("list")
    tl.add_argument("--status", choices=APPLICATION_STATUSES)
    tl.add_argument("--employer")
    tsh = ts.add_parser("show")
    tsh.add_argument("id", type=int)
    tu = ts.add_parser("update")
    tu.add_argument("id", type=int)
    tu.add_argument("new_status", choices=APPLICATION_STATUSES)
    tu.add_argument("--evidence")
    tu.add_argument("--note")
    bl = sub.add_parser("blocklist", help="employers to exclude forever")
    bls = bl.add_subparsers(dest="bl_cmd", required=True)
    bla = bls.add_parser("add")
    bla.add_argument("employer")
    bla.add_argument("--reason", default="")
    bls.add_parser("list")
    blr = bls.add_parser("remove")
    blr.add_argument("employer")
    dv = sub.add_parser("discover", help="find and score jobs (never applies)")
    ds = dv.add_subparsers(dest="dcmd", required=True)
    dse = ds.add_parser("search", help="Adzuna search (needs ADZUNA_APP_ID / ADZUNA_APP_KEY)")
    dse.add_argument("--what", required=True)
    dse.add_argument("--where", default="Melbourne")
    dse.add_argument("--pages", type=int, default=1)
    dse.add_argument("--max-days-old", type=int, default=14)
    dse.add_argument("--full-time", action="store_true")
    dse.add_argument("--part-time", action="store_true")
    dse.add_argument("--limit", type=int, default=15, help="max listings to score this run (each costs one LLM call)")
    dc = ds.add_parser("careers", help="employer career feeds (First Focus via Workable) + the manual watch-list")
    dc.add_argument("--city", default=None)
    dc.add_argument("--title-contains", nargs="*", default=["support", "service desk", "helpdesk", "help desk", "technician", "analyst", "administrator"])
    dc.add_argument("--limit", type=int, default=15)
    dl = ds.add_parser("list")
    dl.add_argument("--all", action="store_true", help="include gate FAIL")
    dl.add_argument("--limit", type=int, default=25)
    drs = ds.add_parser("rescore", help="re-run extraction + scoring for stored listings")
    drs.add_argument("--limit", type=int, default=20)
    dwb = ds.add_parser("web", help="find employer career pages with a generic web search (agent-reach), read them politely, score them")
    dwb.add_argument("--query", action="append", help="job search words (repeatable)")
    dwb.add_argument("--limit", type=int, default=8)
    dwb.add_argument("--score-limit", type=int, default=15)
    dhn = ds.add_parser("hunt", help="one full round: employer feeds + web search + verify + score, then print what is new")
    dhn.add_argument("--score-limit", type=int, default=20)
    dvf = ds.add_parser("verify", help="find each job's employer-original ad, check it is open, flag differences (polite, robots.txt-respecting)")
    dvf.add_argument("id", type=int, nargs="?", help="one listing (default: every stored, non-dismissed, non-FAIL listing)")
    dvf.add_argument("--url", help="the employer's own link for that listing")
    dvf.add_argument("--all", action="store_true", help="include gate FAIL listings")
    dvf.add_argument("--limit", type=int)
    dsh = ds.add_parser("show")
    dsh.add_argument("id", type=int)
    ddr = ds.add_parser("draft", help="draft documents for a listing with a full ad")
    ddr.add_argument("id", type=int)
    ddr.add_argument("--out", type=Path, default=Path("../data/generated"))
    a = ap.parse_args(argv)
    if a.cmd == "track":
        return track(a)
    if a.cmd == "blocklist":
        from app.blocklist import add_blocked, list_blocked, remove_blocked
        with get_session() as s:
            if a.bl_cmd == "add":
                print(f"Blocked: {a.employer}" if add_blocked(s, a.employer, a.reason) else f"Already blocked: {a.employer}")
            elif a.bl_cmd == "remove":
                print(f"Unblocked: {a.employer}" if remove_blocked(s, a.employer) else f"Not on the list: {a.employer}")
            else:
                items = list_blocked(s)
                print("\n".join(f"  {e}" + (f"  ({r})" if r else "") for e, r in items.items()) or "Blocklist is empty.")
        return 0
    if a.cmd == "discover":
        from app.discover_cli import discover
        return discover(a)
    text = sys.stdin.read() if a.ad == "-" else Path(a.ad).read_text()
    if len(text.strip()) < 40:
        print("Job ad is empty or too short.", file=sys.stderr)
        return 2
    try:
        with get_session() as s:
            r = asyncio.run(draft(text, s, get_provider(), a.today or date.today(), a.out, docs=not a.score_only, source=a.source, url=a.url))
    except GenerationRejected as e:
        print(f"\nDocuments NOT generated: the assembled text failed the fact checks.\n  {e}", file=sys.stderr)
        return 3
    _print(r)
    return 1 if r.skipped_reason else 0


if __name__ == "__main__":
    sys.exit(main())
