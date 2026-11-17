"""The application journey as an explicit state machine. A state change needs OBSERVED EVIDENCE (what we saw in the browser),
never just "a button was clicked". Illegal jumps are refused, and every change is recorded for the audit trail."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class S(str, Enum):
    JOB_PAGE_OPENED = "JOB_PAGE_OPENED"
    PAGE_LOADING = "PAGE_LOADING"
    JOB_DETAILS_VISIBLE = "JOB_DETAILS_VISIBLE"
    APPLICATION_ACTION_REQUIRED = "APPLICATION_ACTION_REQUIRED"
    APPLICATION_METHOD_SELECTION = "APPLICATION_METHOD_SELECTION"
    APPLICATION_MODAL_OPEN = "APPLICATION_MODAL_OPEN"
    EXTERNAL_APPLICATION_REDIRECT = "EXTERNAL_APPLICATION_REDIRECT"
    NEW_TAB_OPENED = "NEW_TAB_OPENED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    ACCOUNT_CREATION_REQUIRED = "ACCOUNT_CREATION_REQUIRED"
    EMAIL_VERIFICATION_REQUIRED = "EMAIL_VERIFICATION_REQUIRED"
    APPLICATION_INTRODUCTION = "APPLICATION_INTRODUCTION"
    APPLICATION_FORM_LOADING = "APPLICATION_FORM_LOADING"
    APPLICATION_FORM_DETECTED = "APPLICATION_FORM_DETECTED"
    APPLICATION_STEP_ACTIVE = "APPLICATION_STEP_ACTIVE"
    CONDITIONAL_FIELDS_PENDING = "CONDITIONAL_FIELDS_PENDING"
    DOCUMENT_UPLOAD_REQUIRED = "DOCUMENT_UPLOAD_REQUIRED"
    ELIGIBILITY_QUESTIONS = "ELIGIBILITY_QUESTIONS"
    SCREENING_QUESTIONS = "SCREENING_QUESTIONS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPLICATION_READY_FOR_APPROVAL = "APPLICATION_READY_FOR_APPROVAL"
    SUBMISSION_CONFIRMED = "SUBMISSION_CONFIRMED"
    APPLICATION_COMPLETED = "APPLICATION_COMPLETED"
    APPLICATION_ERROR = "APPLICATION_ERROR"
    CAPTCHA_OR_SECURITY_CHALLENGE = "CAPTCHA_OR_SECURITY_CHALLENGE"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    UNKNOWN_PAGE_STATE = "UNKNOWN_PAGE_STATE"
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"


ENTRY = [S.JOB_PAGE_OPENED, S.PAGE_LOADING, S.JOB_DETAILS_VISIBLE, S.APPLICATION_ACTION_REQUIRED]
GATES = {S.APPLICATION_METHOD_SELECTION, S.APPLICATION_MODAL_OPEN, S.EXTERNAL_APPLICATION_REDIRECT, S.NEW_TAB_OPENED, S.LOGIN_REQUIRED,
         S.ACCOUNT_CREATION_REQUIRED, S.EMAIL_VERIFICATION_REQUIRED, S.APPLICATION_INTRODUCTION}
FORM = {S.APPLICATION_FORM_LOADING, S.APPLICATION_FORM_DETECTED, S.APPLICATION_STEP_ACTIVE, S.CONDITIONAL_FIELDS_PENDING, S.DOCUMENT_UPLOAD_REQUIRED,
        S.ELIGIBILITY_QUESTIONS, S.SCREENING_QUESTIONS}
END = {S.REVIEW_REQUIRED, S.APPLICATION_READY_FOR_APPROVAL}
STOPS = {S.APPLICATION_ERROR, S.CAPTCHA_OR_SECURITY_CHALLENGE, S.SESSION_EXPIRED, S.UNKNOWN_PAGE_STATE, S.USER_INTERVENTION_REQUIRED}
WAITING_FOR_PERSON = {S.LOGIN_REQUIRED, S.ACCOUNT_CREATION_REQUIRED, S.EMAIL_VERIFICATION_REQUIRED, S.CAPTCHA_OR_SECURITY_CHALLENGE, S.SESSION_EXPIRED,
                      S.USER_INTERVENTION_REQUIRED, S.APPLICATION_METHOD_SELECTION}


class IllegalTransition(Exception):
    pass


def allowed(frm: S, to: S) -> bool:
    if frm == to:
        return True
    if to in STOPS or to in WAITING_FOR_PERSON:
        return frm != S.APPLICATION_COMPLETED
    if frm == S.APPLICATION_COMPLETED:
        return False
    if to == S.APPLICATION_COMPLETED:
        return frm == S.SUBMISSION_CONFIRMED
    if to == S.SUBMISSION_CONFIRMED:
        return frm in FORM | END | STOPS | GATES  # the PERSON presses Submit, at any point they choose
    if frm in ENTRY:
        return to in GATES or to in FORM or to in END or (to in ENTRY and ENTRY.index(to) >= ENTRY.index(frm)) or to == S.JOB_DETAILS_VISIBLE
    if frm in GATES:
        return to in GATES or to in FORM or to in END or to in ENTRY  # an external redirect lands on another job/intro page
    if frm in FORM:
        return to in FORM or to in END or to in GATES
    if frm in END:
        return to in END or to in FORM
    if frm == S.SUBMISSION_CONFIRMED:
        return False
    return True  # out of a stop/wait state: resume anywhere the evidence supports


@dataclass
class Event:
    at: str
    frm: str
    to: str
    evidence: list[str]
    url: str = ""


@dataclass
class Machine:
    state: S = S.JOB_PAGE_OPENED
    history: list[Event] = field(default_factory=list)
    on_event: object = None

    def move(self, to: S, evidence: list[str], url: str = "") -> bool:
        """Change state. Needs at least one observed fact. Returns True if the state actually changed."""
        if not evidence:
            raise IllegalTransition(f"{self.state.value} -> {to.value}: no evidence was observed")
        if not allowed(self.state, to):
            raise IllegalTransition(f"{self.state.value} -> {to.value} is not a legal step")
        if to == self.state:
            return False
        ev = Event(datetime.now(timezone.utc).isoformat(timespec="seconds"), self.state.value, to.value, evidence[:4], url)
        self.history.append(ev)
        self.state = to
        if self.on_event:
            self.on_event(ev)
        return True

    def timeline(self, n: int = 40) -> list[dict]:
        return [e.__dict__ for e in self.history[-n:]]
