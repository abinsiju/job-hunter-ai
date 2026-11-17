"""The application journey. Observe the page, decide which stage it is, take ONE verified step, observe again.

Hard rules (each covered by tests):
  * The final Submit is never clicked. The person presses it; we then look for the confirmation page and record it as evidence.
  * Job boards and private addresses are never opened (policy.py). Pages that move us elsewhere stop the run.
  * CAPTCHAs, sign-in, account creation and email verification are handed to the person; we wait and carry on afterwards.
  * Page text is untrusted data. It is never an instruction: nothing on a page is sent to a model or obeyed.
  * A state changes only on evidence seen in the browser; a click alone proves nothing."""
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Callable

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from app.apply.answers import SemanticMatcher
from app.apply.audit import Audit
from app.apply.engine import BANNER_JS, CUSTOM_JS, FLAG_JS, GUARD_OFF, GUARD_ON, SCAN_JS, Decision, UnsafeAction, decide, qa_apply
from app.apply.formgate import check_form
from app.apply.page_intel import (COOKIE_RE, BAD_APPLY_RE, FINAL_RE, METHOD_BAD_RE, METHOD_OK_RE, NEXT_RE, SNAPSHOT_JS, STRONG_APPLY_RE, classify, find_nav, pick_apply_action,
                                  pick_method)
from app.apply.platforms import NotAllowed
from app.apply.policy import check_url
from app.apply.resolver import NEVER_REASON, resolve_control
from app.apply.states import ENTRY, FORM, GATES, END, IllegalTransition, Machine, S
from app.core.config import settings
from app.apply.uploads import CONTAINER_TEXT_JS, judge_upload, validate_file

DECLINE_RE = re.compile(r"^(decline( all)?|reject( all)?|deny( all)?|refuse|no,? thanks|only (necessary|essential)( cookies)?|necessary( cookies)? only|essential only|use necessary cookies only)$", re.I)
MAX_ITER = 120
MAX_ROUNDS = 8  # re-scans of one step while answers reveal new questions
LOAD_WAIT_LIMIT = 60  # x 0.5 s
WAIT_LABEL = {S.LOGIN_REQUIRED: "Sign in to the employer's site yourself. I will carry on when you are in.",
              S.ACCOUNT_CREATION_REQUIRED: "This site wants an account. Create it yourself (I never create accounts). I will carry on afterwards.",
              S.EMAIL_VERIFICATION_REQUIRED: "Verify your email yourself, then I will carry on.",
              S.CAPTCHA_OR_SECURITY_CHALLENGE: "A security check needs you. Solve it yourself and I will carry on.",
              S.SESSION_EXPIRED: "Your session expired. Sign in again and I will carry on where I stopped.",
              S.APPLICATION_METHOD_SELECTION: "Choose how you want to apply. I only pick options that need no account or third-party sign-in.",
              S.USER_INTERVENTION_REQUIRED: "I need you here."}


class Stop(Exception):
    def __init__(self, legacy: str, message: str, **extra):
        super().__init__(message)
        self.legacy, self.message, self.extra = legacy, message, extra


class PageChanged(Exception):
    pass


def _legacy(state: S, waiting: bool = False) -> str:
    if state in END:
        return "ready"
    if state in (S.SUBMISSION_CONFIRMED, S.APPLICATION_COMPLETED):
        return "completed"
    if state == S.APPLICATION_ERROR:
        return "error"
    if waiting:
        return "waiting"
    if state in FORM:
        return "filling"
    return "navigating"


def _sig(snap: dict) -> str:
    return f"{snap['url'].split('#')[0]}|{snap.get('step')}|{snap['fieldCount']}|{snap['passwordFields']}|{len(snap.get('dialogs', []))}|{hash(snap.get('text', '')[:600])}"


def _nav_sig(snap: dict) -> str:
    return f"{snap['url'].split('#')[0]}|{snap.get('step')}"


class Run:
    def __init__(self, plan: dict, out_dir: Path, headless: bool, keep_open: bool, on_status, on_ready, embed=None):
        self.plan, self.out_dir, self.headless, self.keep_open = plan, Path(out_dir), headless, keep_open
        self.on_status, self.on_ready = on_status, on_ready
        self.audit = Audit(self.out_dir / "assist_audit.jsonl")
        self.machine = Machine(on_event=lambda e: self.audit.log("state", frm=e.frm, to=e.to, evidence=e.evidence, url=e.url))
        self.status = {"state": "starting", "stage": S.JOB_PAGE_OPENED.value, "message": "Starting", "platform": plan.get("platform"), "filled": [], "left_blank": [],
                       "captcha": False, "login_required": False, "screenshot": None, "submit_clicked": False, "stopped_before_submit": False, "timeline": [],
                       "needs_review": [], "uploads": [], "step": None, "confirmation": None, "notes": [], "pages": [], "candidates": []}
        self.handled: set[str] = set()
        self.clicks: Counter = Counter()
        self.applying = False
        self.context = None
        self.page = None
        self.deadline = time.monotonic() + settings.assist_max_minutes * 60
        self.shots = 0
        self.step_required_blank = 0
        self.cc_set = False
        self.semantic = SemanticMatcher(embed, plan.get("qa", []))

    # ───────────── status ─────────────
    def emit(self, legacy: str, message: str, **extra) -> None:
        self.status.update(state=legacy, message=message, stage=self.machine.state.value, timeline=self.machine.timeline(30), **extra)
        if self.on_status:
            self.on_status(dict(self.status))

    def shot(self, name: str, full: bool = False) -> str | None:
        try:
            self.shots += 1
            f = self.out_dir / (name if name.endswith(".png") else f"shot_{self.shots:02d}_{name}.png")
            self.page.screenshot(path=str(f), full_page=full)
            return f.name
        except PWError:
            return None

    def _upsert(self, key: str, item: dict) -> None:
        lst = self.status[key]
        for i, x in enumerate(lst):
            if x.get("label") == item.get("label") and x.get("step") == item.get("step"):
                lst[i] = item
                return
        lst.append(item)

    # ───────────── observing ─────────────
    def current_page(self):
        if self.page and not self.page.is_closed():
            return self.page
        for p in reversed(self.context.pages):
            if not p.is_closed():
                return p
        raise Stop("closed", "The browser window was closed.")

    def ready(self, page) -> None:
        """Wait for the page to be meaningfully ready (not a fixed sleep): loaded, network quiet, loading indicators gone."""
        for state, ms in (("domcontentloaded", 15000), ("networkidle", 4000)):
            try:
                page.wait_for_load_state(state, timeout=ms)
            except PWTimeout:
                pass
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if not page.evaluate("document.querySelectorAll('[aria-busy=true], .spinner, .loading, .skeleton, [class*=skeleton]').length"):
                return
            page.wait_for_timeout(250)

    def snapshot(self):
        page = self.page
        snap, scope = page.evaluate(SNAPSHOT_JS), page
        if snap["fieldCount"] < 3 and snap.get("iframes"):  # an application embedded in a frame
            best = None
            for fr in page.frames[1:]:
                try:
                    if fr.is_detached():
                        continue
                    check_url(fr.url)
                    if re.search(r"captcha|recaptcha|analytics|tagmanager|chat|youtube", fr.url, re.I):
                        continue
                    s = fr.evaluate(SNAPSHOT_JS)
                except (NotAllowed, PWError):
                    continue
                if s["fieldCount"] > (best[1]["fieldCount"] if best else 2):
                    best = (fr, s)
            if best:
                scope, snap = best
                snap["frame"] = best[0].url
        return scope, snap

    def check_page_allowed(self, page) -> None:
        try:
            check_url(page.url)
        except NotAllowed as e:
            raise Stop("blocked", f"The page moved to a site that is not allowed, so I stopped. {e}") from None

    def check_deadline(self) -> None:
        if time.monotonic() > self.deadline:
            raise Stop("error", "This is taking too long, so I stopped. Everything done so far is saved in the audit file.")

    # ───────────── state ─────────────
    def transition(self, cls, snap: dict) -> None:
        try:
            self.machine.move(cls.state, cls.evidence, snap["url"])
        except IllegalTransition as e:
            self.audit.log("odd_transition", detail=str(e))
            self.machine.move(S.UNKNOWN_PAGE_STATE, [f"the page ({cls.state.value}) does not follow from {self.machine.state.value}"], snap["url"])
        if cls.injection and not any("instructions" in n for n in self.status["notes"]):
            self.status["notes"].append("This page contains text that looks like instructions to me. Web pages are untrusted, so I ignored it.")
            self.audit.log("prompt_injection_ignored", url=snap["url"])
        if snap.get("captcha"):
            self.status["captcha"] = True

    # ───────────── acting ─────────────
    def click(self, scope, b: dict, kind: str) -> None:
        text = b["text"]
        ok = {"next": bool(NEXT_RE.match(text)), "method": bool(METHOD_OK_RE.search(text) and not METHOD_BAD_RE.search(text)),
              "opener": bool(STRONG_APPLY_RE.search(text) or NEXT_RE.match(text) or METHOD_OK_RE.search(text))}[kind]
        if not ok or (b["type"] == "submit" and b["formHasFields"] and kind != "next") or (kind != "next" and b["inForm"] and b["formHasFields"] and not b["inDialog"]):
            raise UnsafeAction(f"refused to click {text!r}")
        if kind != "next" and BAD_APPLY_RE.search(text) and not METHOD_OK_RE.search(text):
            raise UnsafeAction(f"refused to click {text!r}")
        loc = scope.locator(f'[data-jh-btn="{b["i"]}"]').first
        now = loc.evaluate("el => (el.innerText || el.value || el.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim()")
        if now[:50] != text[:50]:
            raise UnsafeAction(f"the control changed from {text!r} to {now!r}")
        self.audit.log("click", how=kind, text=text[:80], url=self.page.url)
        loc.click(timeout=6000)

    def after_click(self, before_pages: set, before_url: str, before_sig: str) -> list[str]:
        """What did the click actually do? Only things SEEN count: a new tab, a different address, or a changed page."""
        end = time.monotonic() + 8
        while time.monotonic() < end:
            new = [p for p in self.context.pages if p not in before_pages and not p.is_closed()]
            if new:
                np = new[-1]
                try:
                    np.wait_for_load_state("domcontentloaded", timeout=15000)
                except PWTimeout:
                    pass
                self.page = np
                self.check_page_allowed(np)
                self.machine.move(S.NEW_TAB_OPENED, [f"a new tab opened: {np.url[:100]}"], np.url)
                self.audit.log("new_tab", url=np.url, opener=before_url)
                return ["new tab"]
            if self.page.url.split("#")[0] != before_url.split("#")[0]:
                self.check_page_allowed(self.page)
                a, b = (before_url.split("/")[2:3] or [""])[0], (self.page.url.split("/")[2:3] or [""])[0]
                if a != b:
                    self.machine.move(S.EXTERNAL_APPLICATION_REDIRECT, [f"moved from {a} to {b}"], self.page.url)
                return ["navigated"]
            try:
                if _sig(self.page.evaluate(SNAPSHOT_JS)) != before_sig:
                    return ["page changed"]
            except PWError:
                return ["page changed"]
            self.page.wait_for_timeout(250)
        self.audit.log("no_visible_change", url=before_url)
        return []

    def wait_person(self, state: S, snap: dict) -> None:
        """Hand over to the person and wait until the page is no longer in this state (they signed in, solved the check, chose an option)."""
        msg = WAIT_LABEL.get(state, "I need you here.")
        self.shot(state.value.lower())
        self.status["login_required"] = self.status["login_required"] or state in (S.LOGIN_REQUIRED, S.ACCOUNT_CREATION_REQUIRED, S.SESSION_EXPIRED)
        self.emit("waiting", msg)  # the page shot stays in the audit folder: it is not "the filled form"
        self.audit.log("waiting_for_person", state=state.value, url=snap["url"])
        end = time.monotonic() + settings.assist_wait_seconds
        while time.monotonic() < end:
            self.page.wait_for_timeout(1000)
            self.page = self.current_page()
            try:
                _, s = self.snapshot()
            except PWError:
                continue
            self.check_page_allowed(self.page)
            if classify(s, {"applying": self.applying}).state != state:
                self.handled.clear()  # a fresh page or a reloaded form: look at it again (nothing already typed is overwritten)
                return
        raise Stop("blocked", f"I waited for you but nothing changed. {msg}", login_required=self.status["login_required"])

    # ───────────── filling ─────────────
    def need(self, scope, c: dict, reason: str, cat: str, step) -> None:
        label = c["label"] or c["name"] or c["id"] or c["type"]
        if c.get("occ", 1) > 1:
            label = f"{label} (#{c['occ']})"
        item = {"label": label[:160], "required": bool(c["required"]), "type": c["type"], "reason": reason, "category": cat, "step": step}
        self._upsert("left_blank", item)
        if c["required"]:
            self.step_required_blank += 1
        target = f'[data-jh="{c["idx"]}-0"]' if c["type"] == "radio" else f'[data-jh="{c["idx"]}"]'
        try:
            scope.locator(target).first.evaluate(FLAG_JS)
        except PWError:
            pass

    def process(self, scope, page, c: dict, n_files: int, step) -> None:
        r = resolve_control(c, self.plan, n_files, {"cc_set": self.cc_set}, self.semantic)
        label, cat, pol, dec = r.label, r.cat, r.pol, r.dec
        sel = f'[data-jh="{c["idx"]}"]'
        if r.skip:
            return
        if r.need:
            return self.need(scope, c, r.need, cat, step)
        if r.semantic_id:
            self.audit.log("semantic_match", label=label[:80], answer_id=r.semantic_id)
        if r.keep_existing:
            self.audit.log("kept_existing_value", label=label[:80])  # never overwrite what the person (or the site) already put there
            return
        try:
            if dec.kind == "text":
                loc = scope.locator(sel).first
                if c.get("combo"):
                    loc.fill("")
                    loc.press_sequentially(dec.value[:40], delay=40)
                    opt = page.locator("[role=option]:visible").filter(has_text=re.compile(re.escape(dec.value.split(",")[0]), re.I)).first
                    opt.wait_for(timeout=4000)
                    opt.click()
                    if dec.value.split(",")[0].lower() not in loc.input_value().lower():
                        return self.need(scope, c, "autocomplete: the suggestion did not stick, please choose it", cat, step)
                else:
                    loc.fill(dec.value)
                    got, want = loc.input_value().strip(), dec.value.strip()
                    if c["type"] == "tel" or re.search(r"phone|mobile", label, re.I):  # phone boxes reformat what you type: compare the digits
                        same = bool(re.sub(r"\D", "", want)) and re.sub(r"\D", "", got).endswith(re.sub(r"\D", "", want)[-9:])
                    else:
                        same = got == want
                    if not same:
                        return self.need(scope, c, "the field did not keep the value", cat, step)
            elif dec.kind == "select":
                loc = scope.locator(sel).first
                loc.select_option(label=dec.value)
                if loc.evaluate("el => el.options[el.selectedIndex].text").strip() != dec.value.strip():
                    return self.need(scope, c, "the dropdown did not keep the choice", cat, step)
            elif dec.kind == "radio":
                loc = scope.locator(f'[data-jh="{dec.option_ref}"]').first
                try:
                    loc.check(timeout=3000)
                except PWError:
                    loc.check(force=True)
                if not loc.is_checked():
                    return self.need(scope, c, "the choice did not stay selected", cat, step)
            elif dec.kind == "file":
                return self.upload(scope, page, c, dec, step, cat)
        except PWTimeout:
            return self.need(scope, c, "this control did not respond", cat, step)
        except PWError:
            if page.url != self.step_url:
                raise PageChanged() from None
            return self.need(scope, c, "this control did not accept the value", cat, step)
        item = {"label": label[:160], "kind": dec.kind, "source": dec.source, "category": cat, "verified": True, "step": step}
        if dec.kind in ("radio", "select"):
            item["value"] = dec.value
        self._upsert("filled", item)
        if self.cc_flag(dec):
            self.cc_set = True
        if r.review:
            self._upsert("needs_review", {"label": label[:160], "value": dec.value[:140] + ("…" if len(dec.value) > 140 else ""), "category": cat, "source": dec.source,
                                          "reason": "read this before you submit", "step": step})

    @staticmethod
    def cc_flag(dec: Decision) -> bool:
        return dec.kind == "select" and "country code" in str(dec.source)

    def upload(self, scope, page, c: dict, dec: Decision, step, cat: str) -> None:
        label = c["label"] or c["name"] or "Upload"
        paths = dec.value if isinstance(dec.value, list) else [dec.value]
        err = next((f"{Path(p).name}: {e}" for p in paths if (e := validate_file(p))), None)
        rec = {"label": label[:120], "file": ", ".join(Path(p).name for p in paths), "ok": False, "detail": err or "", "step": step}
        if err:
            self._upsert("uploads", rec)
            return self.need(scope, c, f"upload not done: {err}", cat, step)
        loc = scope.locator(f'[data-jh="{c["idx"]}"]').first
        loc.set_input_files(paths)
        page.wait_for_timeout(800)
        info = loc.evaluate(CONTAINER_TEXT_JS)
        judged = [judge_upload(info, p) for p in paths]
        ok, why = all(j[0] for j in judged), "; ".join(j[1] for j in judged if not j[0]) or judged[0][1]
        rec.update(ok=ok, detail=why)
        self._upsert("uploads", rec)
        self.audit.log("upload", label=label[:80], file=rec["file"], ok=ok, detail=why)
        if ok:
            self._upsert("filled", {"label": label[:160], "kind": "file", "source": dec.source, "category": "DOCUMENT", "verified": True, "step": step})
        else:
            self.need(scope, c, f"upload not confirmed: {why}. Attach it yourself", cat, step)

    def fill_step(self, scope, page, snap: dict) -> None:
        """Fill what the verified profile supports, re-scanning until the step stops changing (answers can reveal new questions)."""
        step = snap["step"]["n"] if snap.get("step") else None
        self.step_url = page.url
        step_key = f"{page.url.split('#')[0]}|{step}"
        self.step_required_blank = 0
        self.cc_set = False
        try:
            scope.evaluate(GUARD_ON)  # nothing can be submitted while we work
        except PWError:
            pass
        for rnd in range(MAX_ROUNDS):
            controls = scope.evaluate(SCAN_JS)
            n_files = sum(c["type"] == "file" for c in controls)
            seen, fresh = Counter(), []
            for c in controls:
                lab = c["label"] or c["name"] or c["type"]
                seen[(lab, c["type"])] += 1
                c["occ"] = seen[(lab, c["type"])]
                sig = f"{step_key}|{lab}|{c['type']}|{seen[(lab, c['type'])]}"
                if sig not in self.handled:
                    fresh.append((sig, c))
            if not fresh:
                break
            fresh.sort(key=lambda sc: not re.search(r"country code|dial|calling code|phone country|prefix", (sc[1]["label"] or "").lower()))  # the country-code box is set before the phone number
            if rnd > 0:
                self.machine.move(S.CONDITIONAL_FIELDS_PENDING, [f"{len(fresh)} new field(s) appeared after an answer"], page.url)
                self.audit.log("conditional_fields", count=len(fresh), step=step)
            for sig, c in fresh:
                if page.url != self.step_url:
                    raise PageChanged()
                self.handled.add(sig)
                self.process(scope, page, c, n_files, step)
        for cu in scope.evaluate(CUSTOM_JS):
            sig = f"{step_key}|custom|{cu['label']}"
            if sig in self.handled:
                continue
            self.handled.add(sig)
            self._upsert("left_blank", {"label": cu["label"] or "Custom dropdown", "required": False, "type": "custom-dropdown", "category": "OTHER", "step": step,
                                        "reason": "a custom dropdown I cannot operate reliably: please choose it yourself"})
            try:
                scope.locator(f'[data-jh-custom="{cu["i"]}"]').first.evaluate(FLAG_JS)
            except PWError:
                pass
        self.status["step"] = snap.get("step")
        self.cur_step = step

    def form_gate(self, scope, page) -> None:
        """The eligibility gate, on the LIVE form, before anything is typed: a stated citizenship/PR/clearance requirement stops the run."""
        try:
            body = page.inner_text("body", timeout=5000)
            labels = [c.get("label", "") for c in scope.evaluate(SCAN_JS)]
        except PWError:
            return
        gate = check_form(body, labels)
        self.status["gate"] = {"status": gate.status, "reasons": gate.reasons, "questions": gate.questions}
        if gate.status == "FAIL":
            raise Stop("blocked", "I stopped before filling anything: " + gate.reasons[0], gate=self.status["gate"])

    # ───────────── hand-over and finish ─────────────
    def hand_over(self, scope, page, snap: dict, *, ready: bool, reason: str = "") -> str:
        """Stop automating this step. `ready` = the form is complete and waiting for the person's approval and Submit."""
        left = [b for b in self.status["left_blank"]]
        req = [b for b in left if b.get("required")]
        if ready:
            self.machine.move(S.REVIEW_REQUIRED, ["the last step is filled and a final button is the only thing left"], page.url)
            self.machine.move(S.APPLICATION_READY_FOR_APPROVAL, [f"{len(self.status['filled'])} field(s) filled, {len(left)} left for you"], page.url)
        else:
            self.machine.move(S.USER_INTERVENTION_REQUIRED, [reason or "required answers are missing"], page.url)
        msg = (f"Job Hunter filled {len(self.status['filled'])} field(s). " + ("Orange fields need YOUR answer. " if left else "") +
               ("Solve the CAPTCHA yourself. " if self.status["captcha"] else "") +
               ("Review everything, then press Submit yourself." if ready else (reason or "Complete the highlighted answers, then press Next yourself. I will carry on from the next page.")))
        try:
            page.evaluate("() => document.getElementById('jh-banner') && document.getElementById('jh-banner').remove()")
            scope.evaluate(BANNER_JS, "Job Hunter: " + msg)
            if ready:
                scope.evaluate(GUARD_OFF)  # hand the page back: the PERSON can now submit
        except PWError:
            pass
        shot = self.shot("filled_form.png", full=True)
        self.status.update(stopped_before_submit=ready)
        idle = self.keep_open and settings.assist_leave_after_fill and not self.plan.get("watch_seconds")
        if idle:
            msg = msg.replace("Review everything, then press Submit yourself.", "") + "I have stopped touching the page: it is yours. Solve any security check yourself, review everything, then press Submit."
        self.emit("handed_over" if idle else "ready", msg, screenshot=shot or self.status["screenshot"], missing_required=len(req))
        self.audit.log("handed_over", ready=ready, filled=len(self.status["filled"]), left_blank=len(left), required_missing=len(req), idle=bool(idle))
        if self.on_ready:
            self.on_ready(page)
        if idle:  # nothing more is sent to the page: no polling, no snapshots, no clicks. We only notice when you close the window.
            while self.context.browser and self.context.browser.is_connected():
                time.sleep(1)
            raise Stop("closed", "The browser window was closed.")
        return self.watch(page, snap)

    def watch(self, page, snap: dict) -> str:
        """After handing over: wait for the person. Notice a confirmation page (record it), or a move to another step (carry on)."""
        seconds = float(self.plan.get("watch_seconds") or 0)
        end = time.monotonic() + seconds
        base = _nav_sig(snap)
        while self.keep_open or time.monotonic() < end:
            try:
                if self.context.browser and not self.context.browser.is_connected():
                    raise Stop("closed", "The browser was closed.")
                self.page = self.current_page()
                page.wait_for_timeout(1000)
                _, s = self.snapshot()
            except PWError:
                continue
            cls = classify(s, {"applying": True})
            if cls.state == S.SUBMISSION_CONFIRMED:
                self.confirm(s)
                return "done"
            if cls.state in (S.CAPTCHA_OR_SECURITY_CHALLENGE, S.SESSION_EXPIRED) or (cls.state in FORM | GATES and _nav_sig(s) != base and cls.state not in END):
                return "changed"
        return "done"

    def confirm(self, snap: dict) -> None:
        shot = self.shot("confirmation.png", full=True)
        self.machine.move(S.SUBMISSION_CONFIRMED, ["confirmation wording and no form on the page"], snap["url"])
        m = re.search(r"[^.\n]{0,80}(submitted|received|thank you)[^.\n]{0,100}", snap["text"], re.I)
        self.status["confirmation"] = {"url": snap["url"], "text": (m.group(0).strip() if m else snap["text"][:160]), "screenshot": shot,
                                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.audit.log("submission_confirmed", url=snap["url"])
        self.machine.move(S.APPLICATION_COMPLETED, ["confirmation recorded as evidence"], snap["url"])
        self.emit("completed", "Submission confirmed by the employer's page. The confirmation was saved as evidence.", screenshot=self.status["screenshot"])

    # ───────────── the journey ─────────────
    def step_entry(self, scope, snap: dict, state: S) -> None:
        if state == S.PAGE_LOADING or state == S.APPLICATION_FORM_LOADING:
            self.loads += 1
            if self.loads > LOAD_WAIT_LIMIT:
                raise Stop("error", "The page is taking too long to load, so I stopped.")
            self.page.wait_for_timeout(500)
            return
        self.loads = 0
        if state == S.APPLICATION_INTRODUCTION:
            cand = [b for b in snap["buttons"] if (NEXT_RE.match(b["text"]) or STRONG_APPLY_RE.search(b["text"])) and not b["inChrome"] and not b["disabled"]]
            if cand:
                return self.do_click(scope, cand[0], "opener")
        choice = pick_apply_action(snap)
        self.status["candidates"] = [{"text": b["text"], "context": b["context"][:100]} for b in choice.candidates]
        if choice.kind == "click":
            self.emit("navigating", f"Application button found ({choice.button['text']!r}). Opening the application.")
            return self.do_click(scope, choice.button, "opener")
        if choice.kind == "ambiguous":
            self.machine.move(S.USER_INTERVENTION_REQUIRED, [choice.why], snap["url"])
            self.emit("waiting", "There are several Apply buttons and I cannot tell which one is for this job. Click the right one yourself and I will carry on.")
            return self.wait_until_changed(snap)
        if not self.scrolled:  # lazy content? scroll once and look again
            self.scrolled = True
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(800)
            self.page.evaluate("window.scrollTo(0, 0)")
            return
        for b in snap["buttons"]:  # an external application link
            if b["href"] and re.search(r"apply|application", b["text"], re.I) and not BAD_APPLY_RE.search(b["text"]):
                try:
                    check_url(b["href"])
                except NotAllowed:
                    continue
                return self.do_click(scope, b, "opener")
        self.machine.move(S.USER_INTERVENTION_REQUIRED, [choice.why], snap["url"])
        self.emit("waiting", "I could not find where to apply on this page, and I will not guess an address. Open the application yourself and I will carry on.")
        return self.wait_until_changed(snap)

    def decline_cookies(self) -> bool:
        """A cookie banner can cover the Apply button. We only ever DECLINE (or choose 'necessary only'), never accept."""
        try:
            btn = self.page.locator("[role=dialog], [class*=cookie i], [id*=cookie i], [class*=consent i]").get_by_role("button", name=DECLINE_RE).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=3000)
                self.audit.log("cookie_banner_declined", url=self.page.url)
                self.page.wait_for_timeout(500)
                return True
        except PWError:
            pass
        return False

    def do_click(self, scope, b: dict, kind: str) -> None:
        key = (b["text"], b["href"], kind)
        self.clicks[key] += 1
        if self.clicks[key] > 2:
            self.machine.move(S.USER_INTERVENTION_REQUIRED, [f"clicked {b['text']!r} twice with no visible change"], self.page.url)
            self.emit("waiting", f"I pressed {b['text']!r} but nothing changed, so I stopped instead of pressing it again. Please continue by hand.")
            return self.wait_until_changed(self.snapshot()[1])
        if kind == "opener":
            self.applying = True
        before_snap = self.page.evaluate(SNAPSHOT_JS)
        if any(COOKIE_RE.search(d["text"]) for d in before_snap.get("dialogs", [])):
            self.decline_cookies()
        before = (set(self.context.pages), self.page.url, _sig(self.page.evaluate(SNAPSHOT_JS)))
        try:
            scope.evaluate(GUARD_OFF)
        except PWError:
            pass
        try:
            self.click(scope, b, kind)
        except PWTimeout:  # something covers the control (usually a cookie banner): decline it and try once more
            self.decline_cookies()
            try:
                self.click(scope, b, kind)
            except PWTimeout:
                self.machine.move(S.USER_INTERVENTION_REQUIRED, [f"{b['text']!r} could not be pressed: something covers it"], self.page.url)
                self.emit("waiting", f"I could not press {b['text']!r} because something is covering it. Press it yourself and I will carry on.")
                return self.wait_until_changed(before_snap)
        self.after_click(*before)

    def wait_until_changed(self, snap: dict) -> None:
        end, base = time.monotonic() + settings.assist_wait_seconds, _sig(snap)
        while time.monotonic() < end:
            self.page.wait_for_timeout(1000)
            self.page = self.current_page()
            try:
                if _sig(self.snapshot()[1]) != base:
                    return
            except PWError:
                continue
        raise Stop("blocked", "I could not find an application form on this page and nothing changed while I waited, so I stopped. Open the application page yourself and start again.")

    def step_form(self, scope, snap: dict) -> str:
        """Fill the current step, then either move to the next step (when it is safe) or hand over. Returns 'done' or 'continue'."""
        page = self.page
        if not self.gated:
            self.form_gate(scope, page)
            self.gated = True
            self.shot("form_detected")
        self.emit("filling", "Filling in what your verified profile supports")
        self.fill_step(scope, page, snap)
        _, after = self.snapshot()
        nav = find_nav(after)
        required_blank = any(b.get("required") and b.get("step") == self.cur_step and b["type"] != "custom-dropdown" for b in self.status["left_blank"])
        uploads_bad = any(not u["ok"] for u in self.status["uploads"] if u.get("step") == self.cur_step)
        if nav.next and not required_blank and not uploads_bad:
            self.audit.log("advance", from_step=self.status["step"])
            self.do_click(scope, nav.next, "next")
            try:
                self.page.evaluate(GUARD_ON)
            except PWError:
                pass
            _, now = self.snapshot()
            if _sig(now) == _sig(after) or now["invalid"]:  # the form did not move: it is telling us something
                for msg in now["invalid"][:6]:
                    self._upsert("left_blank", {"label": msg[:160], "required": True, "type": "validation", "reason": "the form reports a problem here", "category": "OTHER",
                                                "step": self.status["step"]})
                return self.finish_step(scope, page, now, ready=False, reason="The form did not accept the step. Check the highlighted problem, then press Next yourself.")
            return "continue"
        if nav.next and (required_blank or uploads_bad):
            return self.finish_step(scope, page, after, ready=False, reason="This step needs you before I can move on: complete the highlighted answers, then press Next yourself. I will carry on from the next page.")
        return self.finish_step(scope, page, after, ready=True)

    def finish_step(self, scope, page, snap: dict, *, ready: bool, reason: str = "") -> str:
        r = self.hand_over(scope, page, snap, ready=ready, reason=reason)
        return "done" if r == "done" else "continue"

    def go(self) -> dict:
        plan = self.plan
        try:
            check_url(plan["url"])
        except NotAllowed as e:
            self.emit("blocked", str(e))
            return self.status
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.audit.log("start", url=plan["url"], employer=plan.get("employer"), role=plan.get("role"))
        self.loads, self.scrolled, self.gated, self.step_url, self.cur_step = 0, False, False, "", None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                try:
                    self.context = browser.new_context(viewport={"width": 1280, "height": 900})
                    self.context.set_default_timeout(10000)  # one stuck control must not stall a run for 30 s
                    try:
                        self.context.tracing.start(screenshots=True, snapshots=True)
                    except PWError:
                        pass
                    self.page = self.context.new_page()
                    self.emit("opening", "Opening the job page")
                    self.page.goto(plan["url"], wait_until="domcontentloaded", timeout=45000)
                    self.journey()
                except Stop as s:
                    self.emit(s.legacy, s.message, **s.extra)
                except PWError as e:
                    if browser.is_connected():
                        self.machine.move(S.APPLICATION_ERROR, [str(e).splitlines()[0][:120]])
                        self.emit("error", f"The browser could not complete: {str(e).splitlines()[0][:160]}")
                    else:
                        self.emit("closed", "The browser window was closed.")
                finally:
                    try:
                        self.context.tracing.stop(path=str(self.out_dir / "trace.zip"))
                        os.chmod(self.out_dir / "trace.zip", 0o600)  # a trace holds page contents, incl. your details: keep it private
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        browser.close()
                    except PWError:
                        pass
        except PWError as e:
            self.emit("error", f"The browser could not start: {str(e).splitlines()[0][:160]}")
        self.audit.log("end", state=self.status["state"], stage=self.status["stage"])
        return self.status

    def journey(self) -> None:
        last, stall = None, 0
        for _ in range(MAX_ITER):
            self.check_deadline()
            self.page = self.current_page()
            self.ready(self.page)
            self.check_page_allowed(self.page)
            if self.page.url not in self.status["pages"]:
                self.status["pages"].append(self.page.url)
            scope, snap = self.snapshot()
            cls = classify(snap, {"applying": self.applying})
            self.transition(cls, snap)
            st = self.machine.state
            sig = (st, _sig(snap))
            stall = stall + 1 if sig == last and st not in (S.PAGE_LOADING, S.APPLICATION_FORM_LOADING) else 0
            last = sig
            if stall >= 5:
                self.machine.move(S.USER_INTERVENTION_REQUIRED, ["the page stopped changing and I cannot move it forward"], snap["url"])
                self.emit("waiting", "I could not make progress on this page, so I stopped. Please continue by hand.")
                return self.wait_until_changed(snap) if self.keep_open else None
            if st == S.JOB_PAGE_OPENED:
                self.machine.move(S.JOB_DETAILS_VISIBLE if cls.state not in (S.JOB_PAGE_OPENED,) else S.PAGE_LOADING, ["first look at the page"], snap["url"])
                st = self.machine.state
            if st in (S.SUBMISSION_CONFIRMED,):
                self.confirm(snap)
                return
            if st in (S.CAPTCHA_OR_SECURITY_CHALLENGE, S.LOGIN_REQUIRED, S.ACCOUNT_CREATION_REQUIRED, S.EMAIL_VERIFICATION_REQUIRED, S.SESSION_EXPIRED):
                self.wait_person(st, snap)
                continue
            if st == S.USER_INTERVENTION_REQUIRED:
                self.status["login_required"] = False
                if cls.reason == "duplicate_application":
                    self.emit("blocked", "The employer's page says you have already applied for this job. I stopped so nothing is sent twice.")
                    return
                self.wait_person(st, snap)
                continue
            if st == S.UNKNOWN_PAGE_STATE:
                self.emit("waiting", "I do not recognise this page. Nothing was clicked. If it is the application, continue by hand.")
                self.wait_until_changed(snap)
                continue
            if st == S.APPLICATION_METHOD_SELECTION:
                choice = pick_method(snap)
                if choice.kind == "click":
                    self.emit("navigating", f"Choosing {choice.button['text']!r}: it needs no account or third-party sign-in")
                    self.do_click(scope, choice.button, "method")
                else:
                    self.wait_person(st, snap)
                continue
            if (st in ENTRY or st in GATES) and st != S.APPLICATION_MODAL_OPEN:
                if st in (S.NEW_TAB_OPENED, S.EXTERNAL_APPLICATION_REDIRECT):
                    self.machine.move(S.APPLICATION_FORM_LOADING, ["inspecting the page we arrived at"], snap["url"]) if cls.state in FORM else None
                    if cls.state in (S.NEW_TAB_OPENED, S.EXTERNAL_APPLICATION_REDIRECT):
                        continue
                self.step_entry(scope, snap, st if st in (S.PAGE_LOADING, S.APPLICATION_FORM_LOADING, S.APPLICATION_INTRODUCTION) else cls.state)
                continue
            if st in FORM or st == S.APPLICATION_MODAL_OPEN or st in END:
                if st in (S.APPLICATION_FORM_LOADING,):
                    self.step_entry(scope, snap, st)
                    continue
                try:
                    res = self.step_form(scope, snap)
                except PageChanged:
                    self.audit.log("page_changed_during_fill", url=self.page.url)
                    self.emit("filling", "The page changed while I was filling, so I paused and am looking at it again.")
                    self.handled.clear()
                    continue
                if res == "done":
                    return
                continue
        raise Stop("error", "I went through too many steps without finishing, so I stopped.")


def default_embed():
    """Embeddings from the LOCAL Ollama (nothing leaves this Mac). None if it is not running: then only the exact rules apply."""
    import asyncio

    try:
        from app.ai.factory import get_provider
        ai = get_provider()
    except Exception:  # noqa: BLE001
        return None

    def embed(texts: list[str]) -> list[list[float]]:
        return [asyncio.run(ai.embed(t)) for t in texts]
    return embed


def run_journey(plan: dict, out_dir: Path, *, headless: bool = False, keep_open: bool = True, on_status: Callable[[dict], None] | None = None,
                on_ready: Callable | None = None, embed: Callable | None = None) -> dict:
    return Run(plan, out_dir, headless, keep_open, on_status, on_ready, embed if embed is not None else default_embed()).go()
