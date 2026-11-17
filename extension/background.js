// Talks to Job Hunter on THIS Mac (127.0.0.1) on behalf of the page. The page itself never sees your code or your data stores.
async function config() {
  const { jhCode } = await chrome.storage.local.get("jhCode");
  const m = /^jh1:(\d{2,5}):(.+)$/.exec(jhCode || "");
  return m ? { api: `http://127.0.0.1:${m[1]}`, token: m[2] } : null;
}
async function call(path, init) {
  const c = await config();
  if (!c) return { error: "Paste your autofill code first (make one in Job Hunter, on the application's page)." };
  try {
    const r = await fetch(c.api + path, { ...init, headers: { Authorization: `Bearer ${c.token}`, ...(init && init.headers) } });
    return { r };
  } catch (e) {
    return { error: "Job Hunter is not running on this Mac (start it with scripts/start.sh)." };
  }
}
chrome.runtime.onMessage.addListener((msg, _sender, send) => {
  (async () => {
    if (msg.type === "plan") {
      const { r, error } = await call("/autofill/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg.body) });
      if (error) return send({ error });
      const j = await r.json().catch(() => ({}));
      return send(r.ok ? j : { error: j.detail || `Job Hunter answered HTTP ${r.status}`, refused: r.status === 403 });
    }
    if (msg.type === "file") {
      const { r, error } = await call(`/autofill/file/${msg.kind}`);
      if (error || !r.ok) return send({ error: error || `HTTP ${r.status}` });
      const buf = new Uint8Array(await r.arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      return send({ name: msg.kind === "resume" ? "Resume.pdf" : "Cover Letter.pdf", b64: btoa(bin) });
    }
    send({ error: "unknown request" });
  })();
  return true; // answer asynchronously
});
