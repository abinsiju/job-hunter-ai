import json
import re
from datetime import date
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.db.models import ProfileFact
from app.db.seed import seed_facts
from app.documents.build import assemble_cover_letter, build_resume
from app.documents.factbook import FactBook
from app.documents.statements import render_group
from app.documents.validate import GenerationRejected, validate_generated
from app.matching.extract import extract_job
from app.matching.schema import JobExtract
from app.matching.skills import SimilarityMatcher
from app.pipeline import draft
from tests.conftest import FakeAI, PipeAI
from tests.fixtures.jobs import EXTRACT, RAW

SEED = Path(__file__).resolve().parents[2] / "data" / "profile_seed.json"
pytestmark = pytest.mark.skipif(not SEED.exists(), reason="data/profile_seed.json not present")
TODAY = date(2026, 11, 9)


@pytest.fixture(scope="module")
def engine():
    e = create_engine(settings.database_url.rsplit("/", 1)[0] + "/jobhunter_test")
    Base.metadata.drop_all(e)
    Base.metadata.create_all(e)
    yield e
    Base.metadata.drop_all(e)


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        s.execute(text("truncate profile_facts, applications restart identity"))
        s.commit()
        seed_facts(s, json.loads(SEED.read_text()))
        yield s


def texts(resume):
    return [s.text for s in resume.sections if s.text] + [x for s in resume.sections for e in s.entries for x in [e.heading, e.sub or "", *e.bullets]]


async def make(db, job=None):
    job = job or EXTRACT["it_support_pt"]
    m = SimilarityMatcher(FakeAI())
    book = FactBook.from_session(db)
    resume, warnings = await build_resume(book, job, m)
    letter = await assemble_cover_letter(book, job, m, TODAY)
    return book, resume, letter, warnings


def sec(resume, title):
    return next((s for s in resume.sections if s.title == title), None)


async def test_resume_structure_and_exact_jigjas_heading(db):
    _, r, _, _ = await make(db)
    assert [s.title for s in r.sections] == ["Summary", "Skills", "Education", "Experience", "Projects", "Virtual Experience", "Additional"]
    heads = [e.heading for e in sec(r, "Experience").entries]
    assert heads[1] == "Warehouse Operator — Acme Packaging, Workville VIC | Feb 2024 – Jan 2025"
    assert heads[0].startswith("Technical Consultant — Globex Australia") and "Aug 2026 – Sep 2026" in heads[0]
    assert "Warehouse Operations" not in " ".join(texts(r))


async def test_telstra_capstone_is_a_project_never_experience(db):
    _, r, letter, _ = await make(db)
    assert "ExampleTel" not in " ".join(x for e in sec(r, "Experience").entries for x in [e.heading, e.sub or "", *e.bullets])
    proj = sec(r, "Projects").entries[0]
    assert "ExampleTel" in proj.heading and "University project" in proj.sub
    assert not re.search(r"(worked|work) (for|at) ExampleTel|ExampleTel employee", " ".join(letter.paragraphs), re.I)


async def test_unverified_facts_never_appear(db):
    db.query(ProfileFact).filter_by(key="venuehub").update({"status": "needs_confirmation", "last_confirmed": None})
    db.commit()
    _, r, letter, _ = await make(db)
    assert "VenueHub" not in " ".join(texts(r) + letter.paragraphs)


def skills_line(resume):
    s = sec(resume, "Skills")
    return s.text if s else None


async def test_skills_list_only_what_the_job_asks_for(db):
    _, r, letter, _ = await make(db, EXTRACT["it_support_pt"])  # asks for Microsoft 365, Active Directory, SharePoint
    assert skills_line(r) == "M365 (Microsoft 365), Active Directory, SharePoint"  # mirrors the ad's wording for a skill you have
    assert "Canva" not in skills_line(r) and "Python" not in skills_line(r)  # verified, but not relevant to this ad
    assert any(p == "The skills I would bring that match this role include M365, Active Directory, SharePoint." for p in letter.paragraphs)


async def test_skills_capped_at_ten_best_first(db):
    names = [f.key for f in db.query(ProfileFact).filter_by(category="skill").order_by(ProfileFact.id)][:15]
    job = JobExtract(title="Generalist", required_skills=names)
    _, r, letter, _ = await make(db, job)
    listed = [x.strip() for x in skills_line(r).split(",")]
    assert len(listed) == 10 and listed == [n for n in names if n != "3D printers / laser cutters"][:10]
    assert len([p for p in letter.paragraphs if p.startswith("The skills I would bring")][0].split(",")) == 6  # letter: max 6


async def test_basic_skill_shown_as_familiar_with_only_when_relevant(db):
    job = JobExtract(title="Maker space assistant", required_skills=["3D printers / laser cutters", "Canva"])
    _, r, letter, _ = await make(db, job)
    assert skills_line(r) == "Canva. Familiar with: 3D printers / laser cutters"
    assert "3D printers" not in " ".join(letter.paragraphs)  # basic skills never in the letter's skills sentence
    _, r2, _, _ = await make(db, EXTRACT["it_support_pt"])
    assert "3D" not in skills_line(r2)


async def test_no_skill_section_when_nothing_matches(db):
    job = JobExtract(title="Forklift driver", required_skills=["Forklift licence", "Pallet jack"])
    _, r, letter, warns = await make(db, job)
    assert sec(r, "Skills") is None and any("None of the verified skills match" in w for w in warns)
    assert not any(p.startswith("The skills I would bring") for p in letter.paragraphs)


async def test_unverified_skill_never_listed_even_if_the_job_asks_for_it(db):
    f = db.query(ProfileFact).filter_by(category="skill", key="SharePoint").one()
    f.status, f.last_confirmed = "needs_confirmation", None
    db.commit()
    _, r, letter, _ = await make(db, EXTRACT["it_support_pt"])
    assert skills_line(r) == "M365 (Microsoft 365), Active Directory"
    assert "SharePoint" not in " ".join(letter.paragraphs[-3:]) or "SharePoint" not in skills_line(r)


async def test_work_rights_and_forbidden_claims_never_in_documents(db):
    _, r, letter, _ = await make(db)
    blob = " ".join(texts(r) + letter.paragraphs + [letter.subject])
    assert not re.search(r"visa|work rights|citizen|permanent resident|485|sponsor", blob, re.I)
    assert not re.search(r"examplebrand|contractor|subcontract", blob, re.I)
    assert "Network & Systems Administrator" not in blob and "multi-site" not in blob


async def test_proper_nouns_keep_their_capitals(db):
    _, r, letter, _ = await make(db)
    assert "Brevo WhatsApp templates" in texts(r)
    assert "brevo" not in " ".join(letter.paragraphs).replace("Brevo", "")


async def test_no_puffery_or_invented_claims_in_documents(db):
    _, r, letter, _ = await make(db)
    blob = " ".join(texts(r) + letter.paragraphs).lower()
    for bad in ("proven", "proficien", "extensive", "expert", "passionate", "authentication", "years of experience", "track record"):
        assert bad not in blob, bad


async def test_licence_line_only_when_job_requires_it(db):
    _, r, letter, _ = await make(db, EXTRACT["it_support_pt"])
    assert sec(r, "Additional") and "driver's licence" in letter.paragraphs[-1]
    _, r2, letter2, _ = await make(db, EXTRACT["service_desk_ft"])
    assert sec(r2, "Additional") is None and "driver" not in " ".join(letter2.paragraphs)


async def test_email_in_header_only_when_verified(db):
    f = db.query(ProfileFact).filter_by(category="contact", key="email").one()
    email = f.value["email"]
    phone = db.query(ProfileFact).filter_by(category="contact", key="phone").one().value
    assert f.status == "verified" and "@" in email
    _, r, letter, warns = await make(db)
    assert r.contact[:3] == [phone, "Exampleton, VIC", email] and letter.contact == r.contact[:3] and r.contact[3].startswith("Applying for: ")  # phone | location | email, then the target role
    assert not any("email" in w for w in warns)
    f.status, f.last_confirmed = "needs_confirmation", None  # un-verify: it must disappear again
    db.commit()
    _, r, _, warns = await make(db)
    assert "@" not in " ".join(r.contact) and any("email" in w for w in warns)


async def test_cover_letter_frames_use_job_title_and_employer(db):
    _, _, letter, _ = await make(db)
    assert letter.subject == "Application for IT Support Officer — Westside Logistics"
    assert letter.paragraphs[0].startswith("I am writing to apply for the IT Support Officer position at Westside Logistics.")
    assert "(Part-time" not in letter.paragraphs[0]


def test_render_group_merges_noun_duties_and_chains_verbs():
    from app.documents.statements import Group
    g = Group("x", "experience", "At Acme,", [])
    out = render_group(g, [("NOUN", "a"), ("VERB", "assisted with b"), ("NOUN", "c")])
    assert out == "At Acme, my responsibilities included a; c. I also assisted with b."
    assert render_group(g, [("VERB", "built x"), ("VERB", "wrote y")]) == "At Acme, I built x. I also wrote y."


def test_validator_blocks_visa_forbidden_numbers_and_unverified_skills(db):
    job = EXTRACT["it_support_pt"]
    facts = "Experience: Warehouse Operator at Acme Packaging. Duties: Staff IT support (PCs, printers, M365)."
    ok = validate_generated(db, "I provided staff IT support using M365 at Acme Packaging.", facts, "", job)
    assert ok == []
    bad = validate_generated(db, "I hold a student visa. I worked at ExampleBrand for 7 years with Active Directory.", facts, "", job)
    joined = " ".join(bad)
    assert "visa" in joined and "ExampleBrand" in joined and "number not in facts" in joined and "Active Directory" in joined


async def test_pipeline_end_to_end_writes_valid_files_and_uses_llm_only_for_extraction(db, tmp_path):
    ai = PipeAI(EXTRACT["it_support_pt"])
    res = await draft(RAW["it_support_pt"], db, ai, TODAY, tmp_path)
    assert res.skipped_reason is None and res.match.gate.status == "PASS" and ai.chat_calls == 1
    assert set(res.files) == {"resume.docx", "resume.pdf", "cover_letter.docx", "cover_letter.pdf"}
    for name, p in res.files.items():
        assert p.exists() and p.stat().st_size > 1000, name
        if name.endswith(".pdf"):
            assert p.read_bytes()[:5] == b"%PDF-"
        else:
            assert any("Alex Example" in x.text for x in Document(p).paragraphs)
    meta = json.loads((next(tmp_path.iterdir()) / "match.json").read_text())
    assert meta["status"] == "DRAFT - not submitted" and meta["gate"]["status"] == "PASS"


async def test_gate_fail_generates_no_documents(db, tmp_path):
    res = await draft(RAW["aps_role"], db, PipeAI(EXTRACT["aps_role"]), TODAY, tmp_path)
    assert res.match.gate.status == "FAIL" and res.files == {} and "citizenship" in res.skipped_reason
    assert list(tmp_path.iterdir()) == []


async def test_review_job_still_drafts_with_flags(db, tmp_path):
    res = await draft(RAW["service_desk_ft"], db, PipeAI(EXTRACT["service_desk_ft"]), TODAY, tmp_path)
    assert res.match.gate.status == "REVIEW" and any("check timing" in f for f in res.match.gate.flags) and res.files


async def test_pipeline_rejects_if_assembled_text_breaks_a_rule(db, tmp_path):
    f = db.query(ProfileFact).filter_by(key="acme_packaging").one()
    f.value = {**f.value, "duties_beyond_core_role": ["Held a student visa while doing IT support"]}  # simulate bad data
    db.commit()
    with pytest.raises(GenerationRejected):
        await draft(RAW["it_support_pt"], db, PipeAI(EXTRACT["it_support_pt"]), TODAY, tmp_path)
    assert list(tmp_path.iterdir()) == []
