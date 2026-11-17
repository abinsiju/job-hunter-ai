// Runs inside the page you opened, when YOU press Fill. It sets field values, ticks the answer radios, picks dropdown options and attaches your two documents.
// It never clicks a button or link, never submits, and does nothing about security checks. Decisions come from Job Hunter on this Mac.
(function () {
  const bridge = window.__jhBridge || {
    plan: (body) => chrome.runtime.sendMessage({ type: "plan", body }),
    file: (kind) => chrome.runtime.sendMessage({ type: "file", kind }),
  };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const FLAG = (el) => { const t = el.type === "radio" ? (el.closest("fieldset,[role=radiogroup]") || el.parentElement) : el; t.style.outline = "3px solid #ff9f0a"; t.style.outlineOffset = "2px"; };
  const q = (idx) => document.querySelector('[data-jh="' + idx + '"]');

  function setValue(el, v) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    el.focus();
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);  // works with React/Vue controlled inputs
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  }
  const digits = (s) => (s || "").replace(/\D/g, "");

  async function attach(el, kinds) {
    const dt = new DataTransfer();
    for (const k of kinds) {
      const f = await bridge.file(k);
      if (!f || f.error) return "could not fetch " + k;
      const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
      dt.items.add(new File([bytes], f.name, { type: "application/pdf" }));
    }
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(500);
    return el.files && el.files.length === kinds.length ? null : "the page did not keep the file";
  }

  async function apply(a, c) {
    if (a.kind === "radio") {  // a radio group has no single element: its choices carry the markers
      const r = q(a.option_ref);
      if (!r) return "that choice is not on the page";
      if (!r.checked) r.click();  // choosing an answer is the only thing this ever clicks
      return r.checked ? null : "the choice did not stay selected";
    }
    const el = q(a.idx);
    if (!el) return "the field disappeared";
    if (a.kind === "text") {
      setValue(el, a.value);
      const ok = c.type === "tel" || /phone|mobile/i.test(c.label) ? digits(el.value).endsWith(digits(a.value).slice(-9)) : el.value.trim() === a.value.trim();
      return ok ? null : "the field did not keep the value";
    }
    if (a.kind === "select") {
      const opt = Array.from(el.options).find((o) => o.text.trim() === a.value.trim());
      if (!opt) return "that option is not in the list";
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return el.options[el.selectedIndex].text.trim() === a.value.trim() ? null : "the dropdown did not keep the choice";
    }
    if (a.kind === "file") return attach(el, a.files);
    return "unsupported field";
  }

  async function run() {
    if (!window.__jhScan) return null;
    const handled = new Set(), filled = [], needs = [], review = [];
    let ccSet = false, stop = null;
    for (let round = 0; round < 6; round++) {
      const controls = window.__jhScan();
      if (round === 0 && !controls.length) return null;
      const seen = {}, fresh = [], sigOf = {};
      for (const c of controls) {
        const lab = c.label || c.name || c.type, k = lab + "|" + c.type;
        seen[k] = (seen[k] || 0) + 1; c.occ = seen[k];
        const sig = k + "|" + c.occ; sigOf[c.idx] = sig;
        if (!handled.has(sig)) fresh.push(c);
      }
      if (!fresh.length) break;
      const res = await bridge.plan({ url: location.href, page_text: document.body.innerText.slice(0, 50000), controls: fresh, cc_set: ccSet });
      if (!res || res.error) return { error: (res && res.error) || "no answer from Job Hunter", refused: !!(res && res.refused) };
      if (res.stop) { stop = res.stop; break; }
      for (const c of fresh) handled.add(sigOf[c.idx]);
      const byIdx = Object.fromEntries(fresh.map((c) => [c.idx, c]));
      for (const a of res.actions) {
        const err = await apply(a, byIdx[a.idx]);
        if (err) { needs.push({ label: a.label, reason: err }); const e = q(a.kind === "radio" ? a.option_ref : a.idx); if (e) FLAG(e); } else filled.push(a.label);
      }
      for (const n of res.needs) { needs.push(n); const e = q(n.type === "radio" ? n.idx + "-0" : n.idx); if (e) FLAG(e); }
      review.push(...res.review);
      ccSet = res.cc_set || ccSet;
      await sleep(500);  // answers can reveal new questions: look again
    }
    if (stop) return { stop };
    const b = document.createElement("div");
    b.textContent = "Job Hunter filled " + filled.length + " field(s). Orange fields need YOUR answer. Solve any security check yourself, review everything, then press Submit yourself.";
    b.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:12px 18px;background:#1d1d1f;color:#fff;font:600 14px -apple-system,system-ui,sans-serif;text-align:center;pointer-events:none";
    document.body.appendChild(b);
    setTimeout(() => b.remove(), 20000);
    return { filled: filled.length, needs: needs.length, review: review.length, needs_list: needs, review_list: review };
  }
  window.__jhRun = run;
})();
