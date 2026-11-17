"""Assisted form fill. Opens a VISIBLE browser on an employer ATS page, fills in what the verified profile supports, highlights everything
else, takes a screenshot, and STOPS. It never submits, never touches a CAPTCHA or a login, and never leaves the allowed platforms.

Guarantees, each enforced here and covered by tests:
  1. The only thing it ever clicks (besides ticking a yes/no answer it is sure of) is a plain 'Apply' opener that is NOT inside a form.
  2. While it works, form submission is blocked in the page; the block is removed afterwards so the PERSON can press Submit.
  3. If the page ends up on any host that is not an allowed ATS platform, it stops.
  4. Consent boxes, passwords, CAPTCHAs and anything unknown are left alone and highlighted."""
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright



class UnsafeAction(Exception):
    """Raised when asked to click something that could submit, send, or sign in."""


@dataclass
class Decision:
    kind: str  # text | select | radio | file
    value: "str | list[str]" = ""
    source: str = ""
    option_ref: str = ""
    review: bool = False  # the person must read this answer before submitting


# ───────────────────────────── what goes where (pure, no browser) ─────────────────────────────
TEXT_RULES = [
    (r"cover letter|covering letter", "cover_letter_text", "draft: cover letter"),
    (r"e-?mail", "email", "contact/email"),
    (r"\bfirst name\b|given name|forename", "first_name", "identity/name"),
    (r"\blast name\b|surname|family name", "last_name", "identity/name"),
    (r"^(your |full |legal )?name\W*$|\bfull name\b|\byour name\b|\blegal name\b", "full_name", "identity/name"),
    (r"phone|mobile|contact number|telephone", "phone", "contact/phone"),
    (r"address line 2|address 2|unit( number)?\b|apartment|flat\b", "", ""),  # never guessed
    (r"post ?code|postal code|\bzip\b", "postcode", "contact/full_address"),
    (r"street|address line 1|address 1", "street", "contact/full_address"),
    (r"\baddress\b", "address_full", "contact/full_address"),
    (r"^country( of residence)?\W*$|\bcountry\b", "country", "contact/full_address"),
    (r"\b(city|suburb|town)\b", "city", "contact/location"),
    (r"\bstate\b|province|territory", "state", "contact/location"),
    (r"\blocation\b|where are you (based|located)|current location", "location", "contact/location"),
]
STATES = {"VIC": "Victoria", "NSW": "New South Wales", "QLD": "Queensland", "SA": "South Australia", "WA": "Western Australia", "TAS": "Tasmania",
          "ACT": "Australian Capital Territory", "NT": "Northern Territory"}
NEVER_FILL = re.compile(r"refer(ee|ence)|salary|expect|notice|linkedin|github|portfolio|website|url|how did you hear|source|gender|ethnic|race|disab|veteran|pronoun|consent|agree|privacy|terms", re.I)


def _yes_no(options: list, want: str):
    for o in options:
        text = (o["text"] if isinstance(o, dict) else o).strip().lower()
        if re.match(rf"^{want.lower()}(\W|$)", text):
            return o
    return None


def qa_apply(d: dict, e: dict) -> Decision | None:
    """Apply ONE confirmed answer to a control. A choice is answered only if an option clearly says it."""
    from app.apply.answers import pick_option
    kind = d["type"]
    if kind in ("radio", "select"):
        opt = pick_option(d.get("options", []), e)
        if opt is None:
            return None
        return Decision("radio", opt["text"], e["source"], opt["ref"], e["review"]) if kind == "radio" else Decision("select", opt, e["source"], review=e["review"])
    if kind in ("text", "textarea", "number"):
        v = e["number"] if kind == "number" and e.get("number") is not None else e["text"]
        return Decision("text", str(v), e["source"], review=e["review"])
    return None


def qa_decide(d: dict, plan: dict, label: str) -> Decision | None:
    """Answers you confirmed once (form_answers/standard), matched by the question's wording."""
    for e in plan.get("qa", []):
        if re.search(e["pattern"], label, re.I) and (dec := qa_apply(d, e)):
            return dec
    return None


def decide(d: dict, plan: dict, n_files: int = 1, hints: dict | None = None) -> Decision | None:
    """What (if anything) to put in this control. None means: leave it blank for the person."""
    label = f'{d.get("label", "")} {d.get("name", "")} {d.get("id", "")}'.lower().replace("_", " ").replace("-", " ")
    kind, person = d["type"], plan["person"]
    hints = hints or {}
    if kind in ("checkbox", "password"):
        return None  # consent boxes are never ticked for you
    if kind == "select" and re.search(r"country code|dial(l)?ing code|calling code|phone country|mobile country|country/region code|phone prefix|international code", label):
        for o in d.get("options", []):  # "Australia (+61)", "+61", "AU +61"
            if re.search(r"australia|\+\s?61\b|^61$", o, re.I):
                return Decision("select", o, "contact/phone: country code +61")
        return None
    if kind == "select" and person.get("country") and re.search(r"^country( of residence)?\W*$|\bcountry\b", label) and not re.search(r"code", label):
        for o in d.get("options", []):  # only from a verified address: never a guess that reveals more questions
            if o.strip().lower() == person["country"].strip().lower():
                return Decision("select", o, "contact/full_address")
    q = qa_decide(d, plan, label)
    if q:
        return q
    if kind == "file":
        if d.get("multiple") and re.search(r"attach|documents?|upload (your )?files|supporting|additional files|application files", label) and not re.search(r"transcript|certificate|passport|licen[cs]e|identity|\bid\b|reference", label):
            both = [p for p in (plan["files"].get("resume"), plan["files"].get("cover_letter")) if p]
            return Decision("file", both, "documents: resume.pdf + cover_letter.pdf") if both else None
        if "cover" in label and re.search(r"resume|\bcv\b|curriculum", label):  # one box for both: the resume goes there (cover letter has its own box when there is one)
            return Decision("file", plan["files"]["resume"], "documents: resume.pdf") if plan["files"].get("resume") else None
        if "cover" in label:
            return Decision("file", plan["files"]["cover_letter"], "documents: cover_letter.pdf") if plan["files"].get("cover_letter") else None
        if re.search(r"resume|\bcv\b|curriculum", label) or (n_files == 1 and not d.get("label", "").strip(" *")):  # the only, unlabelled upload is the resume
            return Decision("file", plan["files"]["resume"], "documents: resume.pdf") if plan["files"].get("resume") else None
        return None
    if kind in ("radio", "select"):
        for rule in plan.get("answers", []):  # explicit, source-tagged yes/no answers only
            if re.search(rule["pattern"], label, re.I):
                opt = _yes_no(d.get("options", []), rule["answer"])
                if opt is None or _yes_no(d.get("options", []), "yes" if rule["answer"] == "No" else "no") is None:
                    return None  # not a yes/no question: do not guess
                if kind == "radio":
                    return Decision("radio", opt["text"], rule["source"], opt["ref"])
                return Decision("select", opt, rule["source"])
        if kind == "select" and person.get("state") and re.search(r"\bstate\b|territory", label):
            want = {person["state"].upper(), STATES.get(person["state"].upper(), "").upper()} - {""}
            for o in d.get("options", []):
                if o.strip().upper() in want:
                    return Decision("select", o, "contact/location")
        return None
    # text-like
    if NEVER_FILL.search(label) and not re.search(r"cover letter", label):
        return None
    if kind == "email":
        label += " email"
    for pattern, field, source in TEXT_RULES:
        if re.search(pattern, label, re.I):
            if field == "cover_letter_text" and kind != "textarea":
                continue
            if field == "phone":  # +61 where the box wants an international number, national form beside a country-code box, else as stored
                value = person.get("phone_intl") if d.get("intl") else person.get("phone_national") if hints.get("cc_set") else person.get("phone", "")
                value = value or person.get("phone", "")
            else:
                value = plan.get("cover_letter_text", "") if field == "cover_letter_text" else person.get(field, "")
            return Decision("text", value, source) if value else None
    return None


# ───────────────────────────── in the page ─────────────────────────────
SCAN_JS = r"""() => {
  const roots = [document];
  const collect = (r) => { for (const el of r.querySelectorAll('*')) if (el.shadowRoot) { roots.push(el.shadowRoot); collect(el.shadowRoot); } };
  collect(document);
  for (const r of roots) r.querySelectorAll('[data-jh]').forEach((e) => e.removeAttribute('data-jh'));  // indices restart each scan: stale markers would point at the wrong control
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const clean = (t) => { t = norm(t).replace(/SVGs? not supported by (this|the) browser\.?/gi, '').replace(/\(optional\)/gi, ' ').replace(/\s+/g, ' ').trim();
    const dup = t.match(/^(.{2,}?)\s*\*?\s*\1\s*\*?$/i);  // "First name *First name" -> "First name"
    return (dup ? dup[1] : t).slice(0, 300); };
  const groupLabel = (el) => {  // a radio group's question lives on its wrapper (aria-labelledby / aria-label / legend), not on each option
    const grp = el.closest('[role=radiogroup],[role=group],fieldset'); if (!grp) return null;
    const root = el.getRootNode(), parts = [];
    const by = grp.getAttribute('aria-labelledby');
    if (by) by.split(/\s+/).forEach((id) => { const n = root.querySelector('#' + CSS.escape(id)); if (n) parts.push(n.textContent); });
    const al = grp.getAttribute('aria-label'); if (al) parts.push(al);
    const lg = grp.querySelector('legend'); if (lg) parts.push(lg.textContent);
    const t = clean(parts.join(' ')); return t || null; };
  const labelText = (el) => {
    const root = el.getRootNode(), parts = [];
    const al = el.getAttribute('aria-label'); if (al) parts.push(al);
    const by = el.getAttribute('aria-labelledby');
    if (by) by.split(/\s+/).forEach((id) => { const n = root.querySelector('#' + CSS.escape(id)); if (n) parts.push(n.textContent); });
    if (el.id) { const l = root.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) parts.push(l.textContent); }
    const wrap = el.closest('label');
    if (wrap) { const c = wrap.cloneNode(true); c.querySelectorAll('input,select,textarea').forEach((x) => x.remove()); parts.push(c.textContent); }
    if (el.placeholder) parts.push(el.placeholder);
    if (!parts.length || el.type === 'radio') {  // a control that already has its own label never borrows one from elsewhere
      const fs = el.closest('fieldset'), lg = fs && fs.querySelector('legend');
      if (lg) parts.push(lg.textContent);
      else { let p = el.parentElement; for (let i = 0; i < 3 && p; i++, p = p.parentElement) {
        const h = p.querySelector(':scope > label, :scope > legend, :scope > .label, :scope > [class*=question], :scope > h3, :scope > h4');
        if (h && !h.contains(el)) { parts.push(h.textContent); break; } } }
    }
    return clean(parts.join(' '));
  };
  const out = [], radios = new Map(); let idx = 0;
  for (const root of roots) for (const el of root.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
    if (type === 'file' ? !(vis(el) || (el.parentElement && vis(el.parentElement))) : (type !== 'password' && !vis(el))) continue;  // a styled upload hides the input but shows its wrapper
    if (type === 'radio') {
      const key = (el.name || '') + '|' + Array.from(document.forms).indexOf(el.form);
      let g = radios.get(key);
      const lab = el.closest('label') || (el.id && el.getRootNode().querySelector('label[for="' + CSS.escape(el.id) + '"]'));
      const text = clean((lab ? lab.textContent : '') || el.value);
      if (!g) { g = { idx: idx++, type: 'radio', name: el.name, id: el.id, label: groupLabel(el) || labelText(el), required: el.required || el.getAttribute('aria-required') === 'true', options: [], checked: false }; radios.set(key, g); out.push(g); }
      const ref = g.idx + '-' + g.options.length;
      el.setAttribute('data-jh', ref); g.options.push({ text: text, ref: ref }); if (el.checked) g.checked = true;
      continue;
    }
    const label = labelText(el);
    const d = { idx: idx++, type: type, name: el.name || '', id: el.id || '', label: label, required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label)), options: [], value: el.value || '', placeholder: el.placeholder || '', multiple: !!el.multiple, intl: !!(el.closest('.iti, .react-tel-input, [class*=PhoneInput], [class*=phone-input]') || /^\+/.test(el.placeholder || '') || /country code|international|\+61/i.test(label)), hasFile: type === 'file' ? (el.files && el.files.length > 0) : false, combo: !!(el.getAttribute('role') === 'combobox' || el.getAttribute('aria-autocomplete') || el.getAttribute('list')), checked: !!el.checked };
    if (type === 'select') d.options = Array.from(el.options).map((o) => norm(o.textContent)).filter(Boolean);
    el.setAttribute('data-jh', String(d.idx));
    out.push(d);
  }
  return out;
}"""
CUSTOM_JS = r"""() => {  // dropdowns that are not real <select>/<input>: we cannot operate them reliably, so we say so
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const out = [];
  document.querySelectorAll('[role=combobox]:not(input):not(select), [aria-haspopup=listbox]:not(select), [data-custom-select], [class*=select__control], [class*=custom-select]:not(select)').forEach((el, i) => {
    if (!vis(el) || el.closest('select')) return;
    el.setAttribute('data-jh-custom', String(i));
    const lab = el.getAttribute('aria-label') || (el.getAttribute('aria-labelledby') && (document.getElementById(el.getAttribute('aria-labelledby')) || {}).textContent) ||
      (el.closest('label, .field, .form-group') && (el.closest('label, .field, .form-group').querySelector('label, legend, .label') || {}).textContent) || el.textContent;
    out.push({ i: i, label: norm(lab).slice(0, 160) });
  });
  return out;
}"""
FLAG_JS = """(el) => { const t = el.type === 'radio' ? (el.closest('fieldset') || el.parentElement.parentElement || el) : el;
  t.style.outline = '3px solid #ff9f0a'; t.style.outlineOffset = '2px'; t.setAttribute('data-jh-flag', 'needs-you'); }"""
BANNER_JS = """(msg) => { const b = document.createElement('div'); b.id = 'jh-banner'; b.textContent = msg;
  b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:12px 18px;background:#1d1d1f;color:#fff;font:600 14px -apple-system,system-ui,sans-serif;text-align:center;pointer-events:none;box-shadow:0 2px 12px rgba(0,0,0,.4)';
  document.body.appendChild(b); }"""
GUARD_ON = "() => { window.__jhBlock = (e) => { e.preventDefault(); e.stopImmediatePropagation(); }; document.addEventListener('submit', window.__jhBlock, true); }"
GUARD_OFF = "() => { if (window.__jhBlock) document.removeEventListener('submit', window.__jhBlock, true); window.__jhBlock = null; }"
CAPTCHA_SEL = 'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], iframe[title*="captcha" i], .g-recaptcha, .h-captcha, .cf-turnstile, [data-sitekey]'

OPENER_RE = re.compile(r"^(apply( now| for this job| for job| to this job| for this position)?|i['’]m interested)$", re.I)
FORBIDDEN_CLICK_RE = re.compile(r"submit|send|complete|finish|confirm|post|register|sign|log ?in|create|save", re.I)


def safe_click(locator) -> None:
    """Click a plain 'Apply' opener, and nothing else. Anything that could submit, send or sign in is refused."""
    info = locator.evaluate("el => ({text: (el.innerText || el.value || '').trim(), type: (el.getAttribute('type') || '').toLowerCase(), inForm: !!el.closest('form')})")
    text = info["text"]
    if info["type"] == "submit" or info["inForm"] or FORBIDDEN_CLICK_RE.search(text) or not OPENER_RE.match(text):
        raise UnsafeAction(f"refused to click {text!r}")
    locator.click()


# ───────────────────────────── the run ─────────────────────────────
def run_assist(plan: dict, out_dir: Path, *, headless: bool = False, keep_open: bool = True,
               on_status: Callable[[dict], None] | None = None, on_ready: Callable | None = None) -> dict:
    """Open the employer's page, find the way to the application, fill what the verified profile supports, and STOP before Submit.
    The journey (state machine, navigation, steps, uploads, audit) is in `app.apply.workflow`."""
    from app.apply.workflow import run_journey
    return run_journey(plan, out_dir, headless=headless, keep_open=keep_open, on_status=on_status, on_ready=on_ready)
