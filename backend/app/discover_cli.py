"""discover search | careers | list | show | draft. Discovery only finds and scores: it never applies to anything."""
import asyncio
import json
from datetime import date
from pathlib import Path

from app.ai.factory import get_provider
from app.core.config import settings
from app.db.models import Listing
from app.db.session import get_session
from app.discovery.adzuna import AdzunaSource
from app.discovery.base import SourceError
from app.discovery.triage import ranked, triage_pending, upsert_listings
from app.discovery.workable import WorkableSource

PAGES = json.loads((Path(__file__).parent / "discovery" / "career_pages.json").read_text())["pages"]


def _line(l: Listing) -> str:
    sc = "-" if l.score is None else f"{l.score:g}"
    src = l.source.split(":")[0] + ("*" if not l.description_complete else "") + (f" +{l.similar} same" if getattr(l, "similar", 0) else "")
    return f"{l.id:>4}  {l.gate_status or 'new':<6} {sc:>5}  {l.title[:38]:<38} {(l.employer or '-')[:20]:<20} {(l.location or '-')[:18]:<18} {src}"


def _print_ranked(rows: list[Listing]) -> None:
    print(f"{'ID':>4}  {'GATE':<6} {'SCORE':>5}  {'TITLE':<38} {'EMPLOYER':<20} {'LOCATION':<18} SRC")
    for l in rows:
        print(_line(l))
    if any(not l.description_complete for l in rows):
        print("\n* snippet only: the full ad was not available, so these can never show as PASS. Open the link, copy the full ad, "
              "then run `draft AD.txt --source adzuna --url URL`.")


async def _collect(a) -> list:
    if a.dcmd == "search":
        src = AdzunaSource(settings.adzuna_app_id, settings.adzuna_app_key)
        return await src.search(what=a.what, where=a.where, pages=a.pages, max_days_old=a.max_days_old, full_time=a.full_time, part_time=a.part_time)
    out = []
    for p in (p for p in PAGES if p["mode"] == "feed"):
        out += await WorkableSource(p["workable_slug"], p["name"]).search(country="Australia", city=a.city, title_contains=a.title_contains)
    return out


def _verify(s, a) -> int:
    from collections import Counter

    from app.verify.fetch import PoliteFetcher
    from app.verify.service import verify_all, verify_and_rescore

    async def run():
        f, ai = PoliteFetcher(), get_provider()
        try:
            if a.id:
                l = s.get(Listing, a.id)
                if not l:
                    print(f"No listing #{a.id}", file=__import__("sys").stderr)
                    return None
                return [(l, await verify_and_rescore(s, ai, f, l, date.today(), employer_url=a.url))]
            return await verify_all(s, ai, f, date.today(), only_open=not a.all, limit=a.limit, unverified_only=getattr(a, "unverified", False))
        finally:
            await f.aclose()
    done = asyncio.run(run())
    if done is None:
        return 2
    print(f"{'ID':>4}  {'CONFIDENCE':<17} {'OPEN':<7} {'ROUTE':<16} {'GATE':<6} TITLE / EMPLOYER")
    for l, r in done:
        print(f"{l.id:>4}  {r.confidence:<17} {r.open_status:<7} {r.application_route:<16} {l.gate_status or '-':<6} {l.title[:34]} / {(l.employer or '-')[:22]}")
        for d in r.discrepancies:
            print(f"        DIFF [{d['severity']}] {d['label']}: listing says {d['third_party']!r}; employer says {d['employer']!r}")
    c = Counter(r.confidence for _, r in done)
    print(f"\nChecked {len(done)}: VERIFIED {c['VERIFIED']}, THIRD-PARTY FULL {c['THIRD_PARTY_FULL']}, SNIPPET ONLY {c['SNIPPET_ONLY']}; "
          f"closed {sum(r.open_status == 'closed' for _, r in done)}; with differences {sum(bool(r.discrepancies) for _, r in done)}.")
    return 0


DEFAULT_QUERIES = ["IT support service desk Melbourne careers", "helpdesk technician Melbourne careers", "desktop support analyst Melbourne careers",
                   "graduate IT support Melbourne careers", "Amazon IT support technician Melbourne amazon.jobs", "Atlassian IT support Melbourne careers",
                   "Microsoft IT support Melbourne careers", "Google IT support Melbourne careers", "TikTok IT support Australia careers"]


async def _web(queries, limit):
    from app.discovery.websearch import read_hits, search_urls
    from app.verify.fetch import PoliteFetcher
    hits, notes = [], []
    for q in queries:
        try:
            hits += search_urls(q, limit)
        except (RuntimeError, ValueError) as e:
            notes.append(f"{q}: {e}")
    f = PoliteFetcher()
    try:
        raws, n2 = await read_hits(f, list({h["url"]: h for h in hits}.values()), date.today())
    finally:
        await f.aclose()
    return raws, notes + n2


def _round(s, a, web: bool, queries) -> int:
    """Fetch -> store -> score. Prints only what is new."""
    before = {l.id for l in s.query(Listing.id)}
    raws, notes = asyncio.run(_web(queries or DEFAULT_QUERIES, getattr(a, "limit", 8))) if web else ([], [])
    if getattr(a, "dcmd", "") == "hunt":
        for p in (p for p in PAGES if p["mode"] == "feed"):
            raws += asyncio.run(WorkableSource(p["workable_slug"], p["name"]).search(country="Australia"))
    new, known = upsert_listings(s, raws)
    asyncio.run(triage_pending(s, get_provider(), date.today(), limit=a.score_limit))
    fresh = [l for l in ranked(s, limit=200) if l.id not in before]
    print(f"Read {len(raws)} job ads: {new} new, {known} already known. Skipped {len(notes)} page(s).")
    for n in notes[:12]:
        print(f"  skipped: {n[:150]}")
    if fresh:
        print()
        _print_ranked(fresh)
    return 0


def discover(a) -> int:
    if a.dcmd in ("search", "careers"):
        try:
            raws = asyncio.run(_collect(a))
        except SourceError as e:
            print(f"Discovery failed: {e}", file=__import__("sys").stderr)
            return 2
        with get_session() as s:
            new, known = upsert_listings(s, raws)
            print(f"Fetched {len(raws)} listings: {new} new, {known} already known.")
            done = asyncio.run(triage_pending(s, get_provider(), date.today(), limit=a.limit))
            print(f"Scored {len(done)} listing(s) (limit {a.limit}; run again to score more).\n")
            _print_ranked(ranked(s, limit=25))
        if a.dcmd == "careers":
            print("\nOther career pages to open in a browser (no documented feed; nothing is fetched from them):")
            for p in PAGES:
                if p["mode"] == "manual":
                    print(f"  {p['name']:<22} {p['kind']:<9} {p['url']}")
            print("  (KMTech blocks automated access; visit it yourself.)")
        return 0
    with get_session() as s:
        if a.dcmd == "rescore":  # re-run extraction + scoring for every stored listing (after extraction/scoring logic changes)
            n = s.query(Listing).update({Listing.triaged_at: None})
            s.commit()
            done = asyncio.run(triage_pending(s, get_provider(), date.today(), limit=a.limit))
            print(f"Reset {n} listing(s); rescored {len(done)} (limit {a.limit}).\n")
            _print_ranked(ranked(s, limit=25))
            return 0
        if a.dcmd in ("web", "hunt"):
            code = _round(s, a, True, a.query if a.dcmd == "web" else None)
            if a.dcmd == "hunt":
                a.id, a.url, a.all, a.limit, a.unverified = None, None, False, None, True
                _verify(s, a)
            return code
        if a.dcmd == "verify":
            return _verify(s, a)
        if a.dcmd == "list":
            rows = ranked(s, include_fail=a.all, limit=a.limit)
            _print_ranked(rows) if rows else print("No scored listings yet. Run `discover search` or `discover careers`.")
            return 0
        l = s.get(Listing, a.id)
        if not l:
            print(f"No listing #{a.id}", file=__import__("sys").stderr)
            return 2
        if a.dcmd == "show":
            print(f"#{l.id} {l.title} / {l.employer or '-'} / {l.location or '-'}   [{l.source}]  posted {l.posted_at or '-'}")
            print(f"link: {l.url}\ngate: {l.gate_status}  score: {l.score}  full text: {l.description_complete}")
            for r in l.fail_reasons or []:
                print(f"  FAIL: {r}")
            for f in l.flags or []:
                print(f"  FLAG: {f}")
            for k, c in (l.breakdown or {}).items():
                print(f"  {k:<22}{c['points']:>5g}/{c['max_points']}")
            return 0
        if not l.description_complete:  # draft
            print(f"#{l.id} only has a snippet of the ad. Open {l.url}, copy the full text to a file, then run:\n"
                  f"  draft AD.txt --source {l.source} --url {l.url}", file=__import__("sys").stderr)
            return 2
        from app.pipeline import draft
        from app.cli import _print
        from app.discovery.base import RawListing
        raw = RawListing(l.source, l.source_id, l.url, l.title, l.employer, l.location, l.description, True, l.posted_at, l.employment_hint)
        r = asyncio.run(draft(raw.ad_text(), s, get_provider(), date.today(), a.out, source=l.source, url=l.url))
        _print(r)
        return 1 if r.skipped_reason else 0
