from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

EmploymentType = Literal[
    "full_time", "part_time", "casual", "contract", "temporary", "internship", "graduate_program", "unknown"
]
WorkRight = Literal["citizenship", "permanent_residency", "security_clearance", "unrestricted_work_rights"]


class Qualification(BaseModel):
    text: str
    mandatory: bool = False


class Salary(BaseModel):
    min: float | None = None
    max: float | None = None
    period: Literal["hour", "year", "unknown"] = "unknown"


class JobExtract(BaseModel):
    """Structured facts pulled from a job description. The LLM fills this; it never produces a score."""

    title: str
    employer: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    min_years_experience: float | None = None
    qualifications: list[Qualification] = Field(default_factory=list)
    work_rights_requirements: list[WorkRight] = Field(default_factory=list)
    clearance_level: str | None = None
    employment_type: EmploymentType = "unknown"
    fixed_term: bool = False
    hours_per_week_min: float | None = None
    hours_per_week_max: float | None = None
    location: str | None = None
    remote: bool = False
    requires_drivers_licence: bool = False
    salary: Salary = Field(default_factory=Salary)
    unpaid: bool = False
    applicant_pays: bool = False  # the applicant must pay a fee / tuition / placement cost to get the role
    placement_program: bool = False  # an internship/placement run by a matching agency
    deadline: date | None = None
