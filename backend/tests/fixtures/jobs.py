"""Five sample ads (raw text) and the structured extraction an LLM should produce for each."""
from datetime import date

from app.matching.schema import JobExtract, Qualification, Salary

RAW = {
    "it_support_pt": """IT Support Officer (Part-time, 20 hrs/week) - Werribee, Melbourne VIC
Support staff with PCs, printers, Microsoft 365 and Active Directory accounts. Experience with SharePoint a plus.
Study in IT welcome; no minimum experience. Driver's licence required. Applications close 30 Nov 2026.""",
    "service_desk_ft": """Service Desk Analyst - Full-time permanent - Melbourne CBD
1-2 years in a service desk role. ITIL, Microsoft 365, Active Directory, ServiceNow. Bachelor's degree in IT desirable.""",
    "aps_role": """IT Support Officer APS4 - Department of Home Affairs, Canberra ACT
Must be an Australian citizen. Baseline security clearance required. Full-time ongoing. Microsoft 365, Active Directory.""",
    "grad_program": """2027 Technology Graduate Program - Sydney NSW
Must be in final year of a degree. Unrestricted work rights required. Python or Java, SQL helpful. No experience needed.""",
    "junior_dev": """Junior Software Developer - Full-time - Docklands, Melbourne
Python, SQL, React, TypeScript, Git. 1+ year experience. Bachelor's degree in IT or related (mandatory). AWS a plus.""",
}

EXTRACT = {
    "it_support_pt": JobExtract(
        title="IT Support Officer", employer="Westside Logistics", required_skills=["Microsoft 365", "Active Directory"],
        nice_to_have_skills=["SharePoint"], employment_type="part_time", hours_per_week_max=20,
        location="Werribee, Melbourne VIC", requires_drivers_licence=True, deadline=date(2026, 11, 30)),
    "service_desk_ft": JobExtract(
        title="Service Desk Analyst", employer="Harbour Tech", required_skills=["ITIL", "Microsoft 365", "Active Directory", "ServiceNow"],
        min_years_experience=1, qualifications=[Qualification(text="Bachelor's degree in IT", mandatory=False)],
        employment_type="full_time", location="Melbourne CBD"),
    "aps_role": JobExtract(
        title="IT Support Officer APS4", employer="Department of Home Affairs", required_skills=["Microsoft 365", "Active Directory"],
        work_rights_requirements=["citizenship", "security_clearance"], clearance_level="Baseline",
        employment_type="full_time", location="Canberra ACT"),
    "grad_program": JobExtract(
        title="2027 Technology Graduate Program", employer="Kestrel Bank", required_skills=["Python"], nice_to_have_skills=["SQL", "Java"],
        qualifications=[Qualification(text="Currently studying a degree", mandatory=True)],
        work_rights_requirements=["unrestricted_work_rights"], employment_type="graduate_program", location="Sydney NSW"),
    "junior_dev": JobExtract(
        title="Junior Software Developer", employer="Docklands Digital", required_skills=["Python", "SQL", "React", "TypeScript", "Git"],
        nice_to_have_skills=["AWS"], min_years_experience=1,
        qualifications=[Qualification(text="Bachelor's degree in IT or related", mandatory=True)],
        employment_type="full_time", location="Docklands, Melbourne", salary=Salary(min=65000, max=75000, period="year")),
}
