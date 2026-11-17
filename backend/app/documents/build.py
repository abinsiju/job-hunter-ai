import re
from datetime import date

from app.documents.factbook import FactBook, month, month_range
from app.documents.model import CoverLetter, Entry, Resume, Section
from app.documents.statements import build_groups, render_group, summary_text
from app.matching.schema import JobExtract
from app.matching.skills import norm, SimilarityMatcher

MAX_CAPSTONE_BULLETS = 4
MAX_SKILLS = 10  # keeps the list credible: only skills that match what this job asks for
MAX_SKILLS_IN_LETTER = 6


async def select_skills(book: FactBook, job: JobExtract, matcher: SimilarityMatcher) -> list[tuple[str, str]]:
    """Verified skills relevant to THIS job, best match first, at most MAX_SKILLS. A skill is relevant when it matches
    (alias, exact or embedding >= the skill threshold) something the job asks for. Unverified skills never appear."""
    wanted = list(dict.fromkeys(job.required_skills + job.nice_to_have_skills)) or [job.title]
    scored = []
    for n, (name, level) in enumerate(book.verified_skills()):
        sims = [await matcher.similarity(name, w) for w in wanted]
        best = max(sims)
        if best >= matcher.skill_threshold:
            # best match first; ties follow the order the ad lists its requirements, then profile order
            scored.append((-best, sims.index(best), n, name, level))
    scored.sort()
    return [(name, level) for _, _, _, name, level in scored[:MAX_SKILLS]]


async def _rank(matcher: SimilarityMatcher, query: str, items: list[str]) -> list[str]:
    scored = [(await matcher.similarity(query, i), n, i) for n, i in enumerate(items)]
    return [i for _, _, i in sorted(scored, key=lambda t: (-t[0], t[1]))]


async def build_resume(book: FactBook, job: JobExtract, matcher: SimilarityMatcher) -> tuple[Resume, list[str]]:
    """Deterministic resume from verified facts. Only the ORDER of bullets and the summary are tailored."""
    contact, warnings = book.contact_lines()
    query = " ".join([job.title, *job.required_skills, *job.nice_to_have_skills])
    sections: list[Section] = [Section("Summary", text=summary_text(book))]

    skills = await select_skills(book, job, matcher)
    if skills:
        ad_forms = {norm(x): x for x in (job.required_skills + job.nice_to_have_skills)}

        def mirror(n: str) -> str:  # the ad says "Office 365", your verified skill is "M365": show both, so a keyword match works without claiming anything new
            f = ad_forms.get(norm(n))
            return f"{n} ({f})" if f and f.lower() != n.lower() and norm(f) == norm(n) else n
        main = [mirror(n) for n, lvl in skills if lvl in ("strong", "working")]
        basic = [mirror(n) for n, lvl in skills if lvl == "basic"]
        line = ", ".join(main)
        if basic:
            line += (". " if main else "") + f"Familiar with: {', '.join(basic)}"
        sections.append(Section("Skills", text=line))
    else:
        warnings.append("None of the verified skills match this ad's requirements: the Skills section is omitted")

    edu = book.get("education", "uni_bit")
    if edu:
        sections.append(Section("Education", entries=[Entry(
            f"{edu['degree']}, {edu['institution']}", f"Expected completion {month(edu['expected_completion'], True)}")]))

    exp = []
    for key, e in sorted(book.in_category("employment").items(), key=lambda kv: kv[1].get("end") or "", reverse=True):
        heading = e.get("resume_heading") or f"{e['title']} — {e['employer']}, {e.get('location', '')} | {month_range(e.get('start'), e.get('end'))}"
        if e.get("duties_beyond_core_role"):
            exp.append(Entry(heading, "IT support, network and admin duties taken on beyond the core warehouse role",
                             await _rank(matcher, query, e["duties_beyond_core_role"])))
        else:
            exp.append(Entry(heading, "Responsibilities included:", list(e.get("duties", []))))
    if exp:
        sections.append(Section("Experience", entries=exp))

    proj = []
    t = book.get("project", "capstone_network_console")
    if t:
        proj.append(Entry(f"Network Intelligence Console — ExampleTel IoT NaaP capstone, Example University {t['course']} ({t['team']}) | {t['period']}",
                          f"{t['role']}, team of {t['team_size']}. University project",
                          (await _rank(matcher, query, t["contributions"]))[:MAX_CAPSTONE_BULLETS]))
    v = book.get("project", "venuehub")
    if v:
        proj.append(Entry("VenueHub — full-stack venue booking app (Example University Full Stack Development)",
                          f"{v['document_wording'].capitalize()}. {', '.join(v['stack'])}", await _rank(matcher, query, v["contributions"])))
    pp = book.get("project", "recipe_planner_features")
    if pp:
        proj.append(Entry("Recipe Planner — solo project", "Java, SQLite", [pp["description"]]))
    if proj:
        sections.append(Section("Projects", entries=proj))

    tcs = book.get("virtual_experience", "virtual_internship")
    if tcs:
        sections.append(Section("Virtual Experience", entries=[Entry(
            f"{tcs['program']} ({tcs['provider']}) | {month_range(tcs.get('start'), tcs.get('end'))}", None, [tcs["topic"]])]))

    if job.requires_drivers_licence and book.has_licence_and_car():
        sections.append(Section("Additional", text="Current Victorian driver's licence; own car."))
    if job.title and job.title != "Role":  # the role this resume is for (an ATS matches on it); it is a statement about the target, not a claim about you
        contact = [*contact, f"Applying for: {job.title}"]
    return Resume(book.name or "", contact, sections), warnings


async def assemble_cover_letter(book: FactBook, job: JobExtract, matcher: SimilarityMatcher, today: date) -> CoverLetter:
    """Opening and closing are fixed frames; the middle is verified evidence sentences ranked by relevance to the job."""
    contact, _ = book.contact_lines()
    role = re.sub(r"\s*\(.*?\)", "", job.title).strip()
    subject = f"Application for {role}" + (f" \u2014 {job.employer}" if job.employer else "")
    at = f" at {job.employer}" if job.employer else ""
    edu = book.get("education", "uni_bit")
    opening = f"I am writing to apply for the {role} position{at}."
    if edu:
        opening += f" I am currently studying a {edu['degree']} at {edu['institution']}, expected to be completed in {month(edu['expected_completion'], True)}."

    query = " ".join([job.title, *job.required_skills, *job.nice_to_have_skills])
    scored = []
    for g in build_groups(book):
        sims = [(await matcher.similarity(query, text), n, m) for n, m in enumerate(g.sentences) for text in [m[1]]]
        sims.sort(key=lambda t: (-t[0], t[1]))
        scored.append((sims[0][0] if sims else 0.0, g, [m for _, _, m in sims]))
    scored.sort(key=lambda t: -t[0])
    exp = [t for t in scored if t[1].kind == "experience"][:2]
    proj = [t for t in scored if t[1].kind == "project"][:1]
    paras = [opening]
    if exp:
        paras.append(" ".join(render_group(g, picked[:3 if g.id.endswith("acme_packaging") else 2]) for _, g, picked in exp if picked))
    if proj and proj[0][2]:
        paras.append(render_group(proj[0][1], proj[0][2][:2]))
    letter_skills = [n for n, lvl in await select_skills(book, job, matcher) if lvl in ("strong", "working")][:MAX_SKILLS_IN_LETTER]
    if letter_skills:
        paras.append(f"The skills I would bring that match this role include {', '.join(letter_skills)}.")
    closing = "Thank you for considering my application. I would welcome the opportunity to discuss the role further."
    if job.requires_drivers_licence and book.has_licence_and_car():
        closing = "I hold a current Victorian driver's licence and have my own car. " + closing
    paras.append(closing)
    return CoverLetter(book.name or "", contact, f"{today.day} {month(today.isoformat(), True)}", subject, "Dear Hiring Manager,",
                       [p for p in paras if p])
