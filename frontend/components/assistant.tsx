"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { IconSend, IconSparkle } from "./icons";
import { JobRow, ApplicationRowItem } from "./shared";
import { Sheet } from "./ui";
import { api } from "@/lib/api";
import type { AssistantReply, ApplicationRow, ListingRow } from "@/lib/types";

interface Msg { who: "me" | "bot"; text: string; reply?: AssistantReply }
const START = ["How am I doing?", "Show my best matches", "What is waiting for my approval?", "Have I applied to Example Hospital?"];

export function AssistantSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) setTimeout(() => inputRef.current?.focus(), 120); }, [open]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  async function ask(q: string) {
    const m = q.trim();
    if (!m || busy) return;
    setText(""); setBusy(true);
    setMsgs((x) => [...x, { who: "me", text: m }]);
    try {
      const reply = await api<AssistantReply>("/assistant", { method: "POST", json: { message: m } });
      setMsgs((x) => [...x, { who: "bot", text: reply.text, reply }]);
    } catch (e) {
      setMsgs((x) => [...x, { who: "bot", text: (e as Error).message }]);
    } finally { setBusy(false); }
  }

  return (
    <Sheet open={open} onClose={onClose} title="Ask" wide>
      <div style={{ display: "grid", gap: 12, minHeight: 280 }}>
        {msgs.length === 0 && (
          <div style={{ textAlign: "center", padding: "12px 0 4px" }}>
            <div className="icon-wrap" style={{ width: 56, height: 56, borderRadius: 18, background: "var(--blue-tint)", color: "var(--blue)", display: "grid", placeItems: "center", margin: "0 auto 12px" }}><IconSparkle style={{ width: 28, height: 28 }} /></div>
            <div style={{ fontWeight: 650, fontSize: 18, letterSpacing: "-0.02em" }}>Ask about your job search</div>
            <p className="muted" style={{ margin: "4px auto 14px", maxWidth: 420 }}>Answers come straight from your own data. I never guess, and I never apply for anything.</p>
            <div className="suggest" style={{ justifyContent: "center" }}>{START.map((s) => <button key={s} onClick={() => ask(s)}>{s}</button>)}</div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ display: "grid", gap: 10 }}>
            <div className={`assist-msg ${m.who}`}>{m.text}</div>
            {m.reply && m.reply.cards.length > 0 && (
              <div className="list">
                {m.reply.cards.map((c, j) =>
                  c.type === "listing" ? <div key={j} onClick={onClose}><JobRow l={c as unknown as ListingRow} /></div>
                  : c.type === "application" ? <div key={j} onClick={onClose}><ApplicationRowItem a={c as unknown as ApplicationRow} /></div>
                  : <div className="row" key={j}><div className="row-main" style={{ display: "grid", gap: 6 }}>{c.lines?.map((l, k) => <div key={k} style={{ fontSize: 14.5 }}>{l}</div>)}</div></div>)}
              </div>
            )}
            {m.reply && m.reply.suggestions.length > 0 && i === msgs.length - 1 && <div className="suggest">{m.reply.suggestions.map((s) => <button key={s} onClick={() => ask(s)}>{s}</button>)}</div>}
          </div>
        ))}
        {busy && <div className="assist-msg bot" aria-live="polite"><span className="spinner" style={{ width: 16, height: 16, display: "inline-block", verticalAlign: "-3px", marginRight: 8 }} />Looking…</div>}
        <div ref={endRef} />
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(text); }} style={{ display: "flex", gap: 8, marginTop: 14, position: "sticky", bottom: 0 }}>
        <input ref={inputRef} className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask anything about your jobs…" aria-label="Your question" maxLength={500} />
        <button className="btn btn-primary" style={{ width: 46, padding: 0 }} type="submit" disabled={busy || !text.trim()} aria-label="Send"><IconSend /></button>
      </form>
      <p className="muted small" style={{ textAlign: "center", margin: "10px 0 0" }}>Tip: press <kbd>⌘</kbd> <kbd>K</kbd> anywhere to open this. <Link className="link" href="/settings" onClick={onClose}>Privacy &amp; rules</Link></p>
    </Sheet>
  );
}
