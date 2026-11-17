"""Page intelligence: take a snapshot of what is on the page (the browser's job) and decide what stage of the application journey it is (pure functions,
fully testable without a browser). Nothing here clicks anything."""
import re
from dataclasses import dataclass, field

from app.apply.states import S

SNAPSHOT_JS = r"""() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  const name = (el) => norm(el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '').slice(0, 120);
  const dialogs = Array.from(document.querySelectorAll('[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open],.modal.show,.modal.open')).filter(vis);
  const inDialog = (el) => dialogs.some((d) => d.contains(el));
  document.querySelectorAll('[data-jh-btn]').forEach((e) => e.removeAttribute('data-jh-btn'));
  const buttons = []; let bi = 0;
  const heading = (document.querySelector('main h1, h1') || {}).textContent;
  for (const el of document.querySelectorAll('a[href], button, [role=button], input[type=submit], input[type=button]')) {
    if (!vis(el)) continue;
    const text = name(el); if (!text) continue;
    const list = el.closest('li, article, tr, [class*=card], [class*=result], [class*=listing]');
    const siblings = list && list.parentElement ? Array.from(list.parentElement.children).filter((c) => c.tagName === list.tagName).length : 0;
    const form = el.closest('form');
    el.setAttribute('data-jh-btn', String(bi));
    buttons.push({ i: bi++, text: text, tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(), href: el.tagName === 'A' ? el.href : '',
      target: el.getAttribute('target') || '', disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true', inForm: !!form, formHasFields: !!form && form.querySelectorAll('input:not([type=hidden]):not([type=submit]),select,textarea').length > 0,
      inDialog: inDialog(el), inMain: !!el.closest('main, [role=main], article, [class*=job-detail], [class*=jobdetail], [class*=posting]'),
      inChrome: !!el.closest('header, footer, nav, [role=navigation], [role=banner], [role=contentinfo]'), inList: siblings >= 3,
      context: norm((list || el.parentElement || el).innerText).slice(0, 160) });
  }
  const fieldSel = 'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]), textarea, select';
  const fields = Array.from(document.querySelectorAll(fieldSel)).filter((e) => e.type === 'file' ? (vis(e) || (e.parentElement && vis(e.parentElement))) : vis(e));
  const text = norm(document.body ? document.body.innerText : '');
  const stepM = text.match(/\b(?:step|page)\s+(\d+)\s*(?:of|\/)\s*(\d+)/i);
  const current = document.querySelector('[aria-current=step]');
  const stepsEls = document.querySelectorAll('[aria-current=step], [role=progressbar][aria-valuenow]');
  return {
    url: location.href, title: document.title, heading: norm(heading || ''), text: text.slice(0, 8000), textLength: text.length,
    buttons: buttons.slice(0, 120), dialogs: dialogs.map((d) => ({ text: norm(d.innerText).slice(0, 400), fields: d.querySelectorAll(fieldSel).length, buttons: Array.from(d.querySelectorAll('button, a[href], [role=button]')).filter(vis).map((b) => name(b)).filter(Boolean).slice(0, 12) })),
    iframes: Array.from(document.querySelectorAll('iframe')).filter(vis).map((f) => ({ src: f.src || '', title: f.title || '', w: Math.round(f.getBoundingClientRect().width), h: Math.round(f.getBoundingClientRect().height) })),
    loading: Array.from(document.querySelectorAll('[aria-busy=true], [role=progressbar]:not([aria-valuenow]), .spinner, .loading, .loader, .skeleton, [class*=skeleton], [class*=spinner]')).filter(vis).length,
    fieldCount: fields.filter((e) => e.type !== 'password').length, passwordFields: fields.filter((e) => e.type === 'password').length, fileFields: fields.filter((e) => e.type === 'file').length,
    step: stepM ? { n: +stepM[1], of: +stepM[2] } : (current ? { n: Array.from(current.parentElement.children).indexOf(current) + 1, of: current.parentElement.children.length } : null),
    captcha: document.querySelectorAll('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], iframe[title*="captcha" i], .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]').length,
    invalid: Array.from(document.querySelectorAll('[aria-invalid=true], .error, .field-error, [role=alert], .invalid-feedback')).filter(vis).map((e) => norm(e.innerText)).filter(Boolean).slice(0, 12),
  };
}"""

# ── words on a page, as evidence ──
CONFIRM_RE = re.compile(r"(application|resume|cv) (has been |was |is )?(successfully )?(submitted|received|sent)|thank you for (applying|your application|submitting)|we(’|')?ve received your application|application (complete|confirmation)", re.I)
DUPLICATE_RE = re.compile(r"already (applied|submitted)|you(’|')?ve (already )?applied|have already applied|duplicate application|previously applied", re.I)
SESSION_RE = re.compile(r"session (has )?(expired|timed out|ended)|you (have been|were) (signed|logged) out|please (sign|log) in again|timed out due to inactivity", re.I)
VERIFY_RE = re.compile(r"verif(y|ication) (your )?(email|code|account)|enter the (6-digit |verification )?code|check your (email|inbox)|we(’|')?ve sent (you )?(a|an) (email|code)", re.I)
CREATE_RE = re.compile(r"create (an |your )?account|register|sign up|new (user|candidate)", re.I)
INTRO_RE = re.compile(r"before you (start|begin)|(what|things) you(’|')?ll need|how (to apply|it works)|application (process|overview)|this (application|form) will take", re.I)
REVIEW_RE = re.compile(r"review (your )?(application|answers|details)|please review|summary of your (application|answers)|check your answers", re.I)
COOKIE_RE = re.compile(r"cookie|newsletter|subscribe|privacy preferences|manage preferences|accept all", re.I)
INJECTION_RE = re.compile(r"ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (your|the) (instructions|rules)|you are (now )?(an?|the) (ai|assistant|system)|system prompt|"
                          r"(reveal|send|email|post|upload) (your|the|all) (password|credentials|secrets?|api key|profile|personal (data|details)|resume)|as an ai (assistant|model)|new instructions:", re.I)

STRONG_APPLY_RE = re.compile(r"^(quick |easy )?apply( now| today| online| here)?( for (this|the) (job|role|position|vacancy))?( to this (job|role|position))?$|^start (your |an? )?application$|"
                             r"^apply (on|via|at|through|with) (the |our |their )?(company|employer|organi[sz]ation)('s)? (website|site|careers?( site| page)?)$|^apply externally$|"
                             r"^(continue|proceed|go) to (the )?(application|apply|employer( site| website)?|company (site|website))$|^i(’|')?m interested$|^apply$", re.I)
BAD_APPLY_RE = re.compile(r"filter|coupon|promo|discount|grant|funding|loan|credit|scholarship|visa|membership|licen[cs]e|sign ?in|log ?in|register|create|save|share|alert|email (a|this)|"
                          r"linkedin|indeed|seek|google|facebook|apple|twitter|glassdoor|jora|print|back to", re.I)
METHOD_OK_RE = re.compile(r"manual(ly)?|company (website|site)|employer (website|site)|without (an? )?account|as (a )?guest|continue as guest|(with|using|upload|attach) (my |your |a )?(resume|cv)|"
                          r"fill (in |out )?(the )?(form|application)|apply (online|directly)|start (the )?application", re.I)
METHOD_BAD_RE = re.compile(r"linkedin|indeed|seek|google|facebook|apple|twitter|github|microsoft|sign ?in|log ?in|existing account|returning", re.I)
NEXT_RE = re.compile(r"^(next|next step|continue|save (and|&) continue|continue to (the )?next( step)?|proceed)$", re.I)
FINAL_RE = re.compile(r"submit|send (my )?application|finish|complete (application|my application)|confirm and (apply|submit)|apply now|place application", re.I)


@dataclass
class Classification:
    state: S
    evidence: list[str]
    reason: str = ""  # machine-readable detail: duplicate_application, session_expired, ...
    injection: bool = False


def _form_state(snap: dict) -> tuple[S, list[str]]:
    ev = [f"{snap['fieldCount']} visible form field(s)"]
    if snap.get("step"):
        ev.append(f"step {snap['step']['n']} of {snap['step']['of']}")
    text = snap.get("text", "")
    if REVIEW_RE.search(text[:1500]) and any(FINAL_RE.search(b["text"]) for b in snap["buttons"]) and snap["fieldCount"] <= 2:
        return S.REVIEW_REQUIRED, ev + ["review wording and a final button"]
    if snap["fileFields"] and snap["fieldCount"] <= snap["fileFields"] + 1:
        return S.DOCUMENT_UPLOAD_REQUIRED, ev + [f"{snap['fileFields']} upload field(s)"]
    if re.search(r"eligib|work rights|right to work|citizen|visa|sponsor", text[:2500], re.I) and snap["fieldCount"] <= 8 and snap.get("step"):
        return S.ELIGIBILITY_QUESTIONS, ev
    if snap.get("step"):
        return S.APPLICATION_STEP_ACTIVE, ev
    return S.APPLICATION_FORM_DETECTED, ev


def classify(snap: dict, ctx: dict | None = None) -> Classification:
    """Which stage is this page? Order matters: things that need the PERSON come before anything the assistant could do."""
    ctx = ctx or {}
    text, injection = snap.get("text", ""), bool(INJECTION_RE.search(snap.get("text", "")))
    mk = lambda st, ev, reason="": Classification(st, ev, reason, injection)
    if snap.get("captcha") and snap["fieldCount"] < 3:  # a CAPTCHA on a real form is flagged for the person at the end; a challenge page stops everything
        return mk(S.CAPTCHA_OR_SECURITY_CHALLENGE, [f"{snap['captcha']} CAPTCHA element(s) on the page"], "captcha")
    if re.search(r"verify you are (a )?human|checking your browser|unusual traffic|access denied", text[:600], re.I):
        return mk(S.CAPTCHA_OR_SECURITY_CHALLENGE, ["a security check page"], "security_check")
    if SESSION_RE.search(text[:1500]):
        return mk(S.SESSION_EXPIRED, ["session expiry wording"], "session_expired")
    if DUPLICATE_RE.search(text[:2500]):
        return mk(S.USER_INTERVENTION_REQUIRED, ["the employer says you already applied"], "duplicate_application")
    if CONFIRM_RE.search(text[:2500]) and snap["fieldCount"] <= 1:
        return mk(S.SUBMISSION_CONFIRMED, ["confirmation wording and no form"], "confirmation")
    if snap.get("loading") and snap["fieldCount"] == 0 and snap["textLength"] < 1500:
        return mk(S.APPLICATION_FORM_LOADING if ctx.get("applying") else S.PAGE_LOADING, [f"{snap['loading']} loading indicator(s), no content yet"])
    if snap["passwordFields"]:
        if VERIFY_RE.search(text[:2000]):
            return mk(S.EMAIL_VERIFICATION_REQUIRED, ["verification wording"])
        if CREATE_RE.search(text[:1500]) and not re.search(r"already have an account|sign in|log in", text[:400], re.I):
            return mk(S.ACCOUNT_CREATION_REQUIRED, ["a password field with account-creation wording"])
        return mk(S.LOGIN_REQUIRED, ["a password field"], "login")
    if VERIFY_RE.search(text[:1500]) and snap["fieldCount"] <= 2:
        return mk(S.EMAIL_VERIFICATION_REQUIRED, ["verification wording"])
    for d in snap.get("dialogs", []):
        if COOKIE_RE.search(d["text"]) and not re.search(r"apply|application", d["text"], re.I):
            continue  # a cookie banner is not part of the application
        if d["fields"] >= 1:
            return mk(S.APPLICATION_MODAL_OPEN, [f"a dialog with {d['fields']} form field(s)"])
        if any(METHOD_OK_RE.search(b) or STRONG_APPLY_RE.search(b) for b in d["buttons"]) and re.search(r"apply|application", d["text"], re.I):
            return mk(S.APPLICATION_METHOD_SELECTION, ["a dialog offering ways to apply"])
    if snap["fieldCount"] >= 3 or (snap["fieldCount"] >= 1 and snap["fileFields"]):
        st, ev = _form_state(snap)
        return mk(st, ev)
    if snap.get("iframes") and any(f["w"] >= 400 and f["h"] >= 300 and not re.search(r"captcha|recaptcha|analytics|tag ?manager|chat|video|youtube", f"{f['src']} {f['title']}", re.I) for f in snap["iframes"]):
        return mk(S.APPLICATION_FORM_LOADING, ["a large embedded frame"], "embedded_frame")
    opts = [b for b in snap["buttons"] if METHOD_OK_RE.search(b["text"]) and not METHOD_BAD_RE.search(b["text"])]
    if len(opts) >= 2 and re.search(r"how would you like to apply|choose how|apply (with|using)|select an? (option|method)", text[:1500], re.I):
        return mk(S.APPLICATION_METHOD_SELECTION, ["several ways to apply are offered"])
    if INTRO_RE.search(text[:1500]) and any(NEXT_RE.match(b["text"]) or STRONG_APPLY_RE.search(b["text"]) for b in snap["buttons"]):
        return mk(S.APPLICATION_INTRODUCTION, ["introduction wording and a way to continue"])
    if ctx.get("applying") and snap["textLength"] < 400:
        return mk(S.APPLICATION_FORM_LOADING, ["the page has little content after Apply"])
    if any(STRONG_APPLY_RE.search(b["text"]) and not BAD_APPLY_RE.search(b["text"]) for b in snap["buttons"]):
        return mk(S.APPLICATION_ACTION_REQUIRED, ["a visible Apply control"])
    if snap.get("heading") or snap["textLength"] >= 400:
        return mk(S.JOB_DETAILS_VISIBLE, ["job details are visible"])
    if snap["textLength"] < 150 and not snap["buttons"]:
        return mk(S.PAGE_LOADING, ["the page is nearly empty"])
    return mk(S.UNKNOWN_PAGE_STATE, ["nothing recognisable"])


@dataclass
class ActionChoice:
    kind: str  # click | ambiguous | none
    button: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    why: str = ""


def _score_apply(b: dict, heading: str) -> float:
    t = b["text"].strip()
    if b["disabled"] or not STRONG_APPLY_RE.search(t) or BAD_APPLY_RE.search(t):
        return -99
    s = 3.0
    if re.match(r"^(quick |easy )?apply( now)?$", t, re.I):
        s += 1
    s += 2 if b["inMain"] else 0
    s += 1 if b["inDialog"] else 0
    s -= 3 if b["inChrome"] else 0
    s -= 4 if b["inList"] else 0
    if heading and heading.lower()[:40] in (b.get("context", "") + " " + t).lower():
        s += 1
    if b["inForm"] and b["formHasFields"] and not b["inDialog"]:
        s -= 5  # a button inside a form submits it: never an "opener"
    if b["type"] == "submit":
        s -= 5
    return s


def pick_apply_action(snap: dict) -> ActionChoice:
    """The ONE control that starts the application, or a clear reason why we cannot tell."""
    scored = sorted(((_score_apply(b, snap.get("heading", "")), b) for b in snap["buttons"]), key=lambda x: -x[0])
    good = [(s, b) for s, b in scored if s > 0]
    if not good:
        return ActionChoice("none", why="no control that starts an application was found")
    top = good[0]
    rivals = [b for s, b in good[1:] if s >= top[0] - 1 and (b["href"] or b["text"]) != (top[1]["href"] or top[1]["text"]) and b["href"] != top[1]["href"]]
    if rivals:
        return ActionChoice("ambiguous", candidates=[top[1], *rivals][:5], why="several Apply controls look equally likely, so I will not guess")
    return ActionChoice("click", top[1], why="the clearest Apply control on the page")


def pick_method(snap: dict) -> ActionChoice:
    """On a 'how would you like to apply' screen: a way that needs no account and no third-party sign-in, else leave it to the person."""
    pool = [b for d in snap.get("dialogs", []) for b in snap["buttons"] if b["inDialog"]] or snap["buttons"]
    ok = [b for b in pool if METHOD_OK_RE.search(b["text"]) and not METHOD_BAD_RE.search(b["text"]) and not b["disabled"]]
    if len(ok) == 1:
        return ActionChoice("click", ok[0], why="the only option that needs no account or third-party sign-in")
    if len(ok) > 1:
        ok.sort(key=lambda b: (not re.search(r"manual|company|employer|without|guest", b["text"], re.I), b["i"]))
        return ActionChoice("click", ok[0], ok[1:], "the option that needs no account")
    return ActionChoice("none", why="every way to apply needs an account or a third-party sign-in")


@dataclass
class NavChoice:
    next: dict | None = None
    final: dict | None = None
    back: dict | None = None
    why: str = ""


def find_nav(snap: dict) -> NavChoice:
    """Next / final-submit controls. We only ever CLICK a Next on a page that shows a step indicator (a wizard). We never click a final button."""
    nav = NavChoice()
    for b in snap["buttons"]:
        t = b["text"].strip()
        if b["disabled"]:
            continue
        if FINAL_RE.search(t) and not NEXT_RE.match(t):
            nav.final = nav.final or b
        elif NEXT_RE.match(t) and not b["inChrome"]:
            nav.next = nav.next or b
        elif re.match(r"^(back|previous)$", t, re.I):
            nav.back = b
    st = snap.get("step")
    if nav.next and not st:
        nav.why = "a Continue button without a visible step indicator could be the final submit, so I will not press it"
        nav.next = None
    if st and st["n"] >= st["of"]:
        nav.next = None  # the last step: the next thing is the final submit
    return nav
