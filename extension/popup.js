const out = document.getElementById("out"), code = document.getElementById("code");
chrome.storage.local.get("jhCode").then(({ jhCode }) => { if (jhCode) code.value = jhCode; });
document.getElementById("fill").addEventListener("click", async () => {
  out.textContent = "Working…";
  await chrome.storage.local.set({ jhCode: code.value.trim() });
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["scan.js", "apply.js"] });
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: () => (window.__jhRun ? window.__jhRun() : null) });
    const parts = res.map((r) => r.result).filter(Boolean);
    const refused = parts.find((p) => p.refused || p.error), stop = parts.find((p) => p.stop), main = parts.sort((a, b) => (b.filled || 0) - (a.filled || 0))[0];
    if (stop) out.textContent = "Stopped: " + stop.stop;
    else if (refused && !(main && main.filled)) out.textContent = refused.error;
    else if (!main) out.textContent = "I found no form on this page. Open the application form first.";
    else out.textContent = `Filled ${main.filled} field(s). ${main.needs} left for you (orange). ${main.review} to read before you submit.\nNow solve any security check yourself, review everything and press Submit yourself.`;
  } catch (e) {
    out.textContent = "Could not run on this page: " + (e && e.message ? e.message : e);
  }
});
