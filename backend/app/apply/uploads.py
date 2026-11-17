"""Document uploads: check the file BEFORE sending it, and check the page accepted it AFTER. Choosing a file is not an upload."""
import re
from pathlib import Path

ALLOWED = {".pdf", ".doc", ".docx", ".rtf", ".txt", ".odt"}
MAX_BYTES = 10 * 1024 * 1024
ERROR_RE = re.compile(r"(upload|file)[^.\n]{0,40}(fail|error|invalid|too (large|big)|not (supported|allowed|accepted)|unsupported|could not)|"
                      r"(fail|error|invalid|unsupported)[^.\n]{0,30}(upload|file)|virus|corrupt", re.I)
CONTAINER_TEXT_JS = """(el) => { const c = el.closest('.field, .form-group, .form-field, fieldset, li, [class*=upload]') || (el.parentElement && el.parentElement.parentElement) || el.parentElement;
  return { text: (c ? c.innerText : '').replace(/\\s+/g, ' ').trim().slice(0, 600), files: Array.from(el.files || []).map((f) => f.name) }; }"""


def validate_file(path: str) -> str | None:
    """None if the file can be uploaded, else the reason."""
    p = Path(path)
    if not p.is_file():
        return "the file does not exist"
    if p.suffix.lower() not in ALLOWED:
        return f"{p.suffix or 'this'} files are not accepted (use PDF or Word)"
    if p.stat().st_size == 0:
        return "the file is empty"
    if p.stat().st_size > MAX_BYTES:
        return "the file is larger than 10 MB"
    return None


def judge_upload(info: dict, path: str) -> tuple[bool, str]:
    """`info` is what the page shows after the file was chosen. Success needs the browser to hold the file AND no error shown next to it."""
    name = Path(path).name
    if name not in info.get("files", []):
        return False, "the page did not keep the file"
    m = ERROR_RE.search(info.get("text", ""))
    if m:
        return False, f"the page shows an error: “{m.group(0)[:80]}”"
    return True, "the file is attached" + (" and its name is shown" if name in info.get("text", "") else "")
