import json

import pytest

from app.matching.extract import detect_work_rights, extract_job


class Canned:
    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), 0

    async def chat(self, system, user, *, json_mode=False, temperature=None, json_schema=None):
        self.calls += 1
        assert temperature == 0 and json_schema and "work_rights_requirements" in json_schema["properties"]  # structured outputs, deterministic
        return self.outputs.pop(0)


def j(**kw):
    return json.dumps({"title": "T", **kw})


async def test_junk_work_rights_values_from_model_are_dropped():
    # real outputs seen from qwen3:4b
    ai = Canned(j(work_rights_requirements=["Driver's licence", "unknown"]))
    job = await extract_job(ai, "IT Support. Driver's licence required.")
    assert job.work_rights_requirements == [] and job.requires_drivers_licence


async def test_model_claim_without_text_evidence_is_ignored():
    # seen live: qwen3:4b invented "unrestricted_work_rights" for an ad that never mentions it
    job = await extract_job(Canned(j(work_rights_requirements=["unrestricted_work_rights", "citizenship"])), "Service Desk Analyst, ITIL, M365.")
    assert job.work_rights_requirements == []


async def test_synonyms_normalised_and_confirmed_by_text():
    job = await extract_job(Canned(j(work_rights_requirements=["Australian citizen", "PR"])), "Australian citizen or permanent resident only")
    assert set(job.work_rights_requirements) == {"citizenship", "permanent_residency"}


async def test_regex_backstop_catches_what_model_missed():
    ai = Canned(j(work_rights_requirements=[]))
    job = await extract_job(ai, "Must be an Australian citizen. Baseline security clearance required.")
    assert set(job.work_rights_requirements) == {"citizenship", "security_clearance"}


async def test_invalid_json_retries_then_fails_loudly():
    ai = Canned("not json", j(employment_type="banana"))
    with pytest.raises(ValueError):
        await extract_job(ai, "x")
    assert ai.calls == 2


def test_detect_work_rights_negatives():
    assert detect_work_rights("Help desk role, 2 years experience, public relations skills") == set()
    assert detect_work_rights("Applicants must hold Australian citizenship or permanent residency") == {"citizenship", "permanent_residency"}
    assert detect_work_rights("Requires NV1 clearance") == {"security_clearance"}


async def test_employment_type_and_years_fallbacks_only_fill_gaps():
    job = await extract_job(Canned(j(employment_type="unknown")), "Junior Developer - Full-time - Docklands. 1+ year experience required.")
    assert job.employment_type == "full_time" and job.min_years_experience == 1
    job = await extract_job(Canned(j(employment_type="part_time", min_years_experience=3)), "Full-time role. 1 year experience.")
    assert job.employment_type == "part_time" and job.min_years_experience == 3  # model values are kept
    job = await extract_job(Canned(j()), "2027 Technology Graduate Program. No experience needed.")
    assert job.employment_type == "graduate_program" and job.min_years_experience is None
    job = await extract_job(Canned(j()), "Service desk. 1-2 years in a service desk role.")
    assert job.min_years_experience is None  # not stated as "experience": do not guess


async def test_unpaid_detected_from_text_even_if_model_misses_it():
    job = await extract_job(Canned(j()), "Marketing intern. This is an unpaid position for experience only.")
    assert job.unpaid


from app.matching.extract import SCHEMA, clean_skills


def test_every_field_is_required_in_the_schema_sent_to_the_model():
    # regression: with only `title` required, qwen3:4b skipped required_skills entirely on long real ads (0 of 8 extracted)
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"]) and "required_skills" in SCHEMA["required"]
    assert "Include every one that is named" in SCHEMA["properties"]["required_skills"]["description"]


def test_clean_skills_strips_qualifiers_and_drops_soft_skills_and_sentences():
    got = clean_skills(["Strong grasp of Windows Server", "Experience with virtualisation (Hyper-V, VMware)", "Deep infrastructure knowledge",
                        "communication skills", "complex problem-solving", "ticket triage", "Microsoft 365", "microsoft 365", "Azure AD",
                        "Incident, request, and problem resolution", "Hands-on experience with Active Directory",
                        "a very long phrase that is clearly a whole sentence and not a skill"])
    assert got == ["Windows Server", "virtualisation (Hyper-V, VMware)", "Microsoft 365", "Azure AD", "Active Directory"]  # "Deep infrastructure knowledge" is vague: dropped
    assert clean_skills([]) == [] and clean_skills(["", "  ", "."]) == []


async def test_extract_applies_the_skill_cleaner():
    job = await extract_job(Canned(j(required_skills=["Strong grasp of Azure AD", "communication skills", "Exchange"])), "ad text")
    assert job.required_skills == ["Azure AD", "Exchange"]


from app.matching.extract import clean_qualifications
from app.matching.schema import Qualification as Q

# real strings the model put into `qualifications` (all marked mandatory) for First Focus ads on 2026-11-10
REAL_NOT_QUALIFICATIONS = [
    "Deep infrastructure knowledge—from desktop to firewall, LAN/WAN included",
    "Strong grasp of Windows Server, Azure AD, GPO, DNS, DHCP, and messaging platforms",
    "Experience with virtualisation (Hyper-V, VMware) and backup tech (Veeam, Datto, StorageCraft)",
    "Excellent communication skills—clear, confident, and client-friendly",
    "A knack for complex problem-solving and ticket triage", "A genuine desire to help, not just fix",
    "Proven experience in a Helpdesk or Service Desk environment", "Working knowledge of ITIL concepts and best-practice",
    "The ability to work well under pressure", "Deep knowledge of AD (mostly Azure but still a little bit of on-prem too)",
]


def test_skill_and_experience_statements_are_not_qualifications_and_never_mandatory():
    out = clean_qualifications([Q(text=t, mandatory=True) for t in REAL_NOT_QUALIFICATIONS])
    assert out == []


def test_real_credentials_survive_and_mandatory_needs_a_cue_in_the_text():
    got = clean_qualifications([Q(text="Bachelor of IT", mandatory=True), Q(text="Must hold a Cisco CCNA certification", mandatory=True),
                                Q(text="CompTIA A+ desirable", mandatory=True), Q(text="Working With Children Check is essential", mandatory=False),
                                Q(text="Current driver's licence", mandatory=True)])
    assert [(q.text, q.mandatory) for q in got] == [("Bachelor of IT", False), ("Must hold a Cisco CCNA certification", True),
                                                    ("CompTIA A+ desirable", False), ("Working With Children Check is essential", False)]


async def test_extract_never_lets_skill_statements_reach_the_gate_as_mandatory_qualifications():
    quals = [{"text": t, "mandatory": True} for t in REAL_NOT_QUALIFICATIONS]
    job = await extract_job(Canned(j(qualifications=quals, required_skills=["Windows Server", "client-friendly", "backup tech (V", "Microsoft AD"])), "ad")
    assert job.qualifications == [] and job.required_skills == ["Windows Server", "Microsoft AD"]


def test_microsoft_ad_is_the_same_skill_as_active_directory():
    from app.matching.skills import norm
    assert norm("Microsoft AD") == norm("Active Directory") == norm("Microsoft Active Directory")
    assert norm("Azure AD") != norm("Active Directory")  # a different (cloud identity) product
