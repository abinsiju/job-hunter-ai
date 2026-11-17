"use client";
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { IconCheck, IconChevron, IconClose, IconInfo, IconSearch, IconWarning } from "./icons";
import { GATE_LABEL, GATE_TONE, STATUS_LABEL, STATUS_TONE, scoreColor } from "@/lib/format";
import type { Gate, Status } from "@/lib/types";

export function cx(...a: (string | false | null | undefined)[]) { return a.filter(Boolean).join(" "); }

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "tint" | "plain" | "gray" | "danger"; size?: "sm" | "md" | "lg"; block?: boolean; busy?: boolean };
export function Button({ variant = "primary", size = "md", block, busy, className, children, disabled, ...rest }: BtnProps) {
  return (
    <button className={cx("btn", `btn-${variant}`, size === "sm" && "btn-sm", size === "lg" && "btn-lg", block && "btn-block", className)} disabled={disabled || busy} {...rest}>
      {busy && <span className="spinner" style={{ width: 16, height: 16, borderColor: "rgba(255,255,255,.35)", borderTopColor: "#fff" }} />}
      {children}
    </button>
  );
}

export function LinkButton({ href, variant = "tint", size = "md", children, external }: { href: string; variant?: BtnProps["variant"]; size?: BtnProps["size"]; children: ReactNode; external?: boolean }) {
  const cls = cx("btn", `btn-${variant}`, size === "sm" && "btn-sm", size === "lg" && "btn-lg");
  return external ? <a className={cls} href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <Link className={cls} href={href}>{children}</Link>;
}

export function Pill({ tone = "", children, icon }: { tone?: string; children: ReactNode; icon?: ReactNode }) {
  return <span className={cx("pill", tone)}>{icon}{children}</span>;
}
export function GatePill({ gate }: { gate: Gate }) { return gate ? <Pill tone={GATE_TONE[gate]}>{GATE_LABEL[gate]}</Pill> : <Pill>Not scored</Pill>; }
export function StatusPill({ status }: { status: Status }) { return <Pill tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Pill>; }

function useCountUp(target: number | null, ms = 650): number | null {
  const [v, setV] = useState<number | null>(target);
  useEffect(() => {
    if (target == null || window.matchMedia("(prefers-reduced-motion: reduce)").matches) { const t = setTimeout(() => setV(target), 0); return () => clearTimeout(t); }
    let raf = 0, start = 0;
    const tick = (now: number) => {
      start ||= now;
      const p = Math.min(1, (now - start) / ms), e = 1 - Math.pow(1 - p, 3);  // slow out
      setV(Math.round(target * e));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

export function ScoreRing({ score, size = 48 }: { score: number | null; size?: number }) {
  const shown = useCountUp(score == null ? null : Math.round(score));
  const stroke = Math.max(4, Math.round(size / 11));
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, pct = score == null ? 0 : Math.max(0, Math.min(100, score)) / 100;
  return (
    <div className="score" style={{ width: size, height: size }} role="img" aria-label={score == null ? "Not scored" : `Score ${Math.round(score)} out of 100`}>
      <svg width={size} height={size}>
        <circle className="score-track" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} />
        <circle className="score-fill" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} stroke={scoreColor(score)} strokeDasharray={c} strokeDashoffset={c * (1 - pct)} />
      </svg>
      <span className="score-num" style={{ fontSize: size * 0.34 }}>{score == null ? "–" : shown}</span>
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string; count?: number }[] }) {
  return (
    <div className="segmented" role="group">
      {options.map((o) => (
        <button key={o.value} className="seg" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}{o.count != null && <span className="count">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SearchBox({ value, onChange, placeholder = "Search" }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div className="search">
      <IconSearch />
      <input className="input" type="search" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} aria-label={placeholder} />
    </div>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <div className="toggle-row">
      <span>{label}</span>
      <button className="switch" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)} />
    </div>
  );
}

export function Callout({ tone = "info", children }: { tone?: "info" | "warn" | "bad" | "good"; children: ReactNode }) {
  const Icon = tone === "warn" ? IconWarning : tone === "bad" ? IconWarning : tone === "good" ? IconCheck : IconInfo;
  return <div className={cx("callout", tone)} role={tone === "bad" ? "alert" : undefined}><Icon /><div>{children}</div></div>;
}

export function Skeleton({ h = 16, w = "100%", r }: { h?: number; w?: number | string; r?: number }) {
  return <div className="skeleton" style={{ height: h, width: w, borderRadius: r }} aria-hidden />;
}
export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="list" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="row" key={i}><Skeleton h={46} w={46} r={23} /><div className="row-main" style={{ display: "grid", gap: 8 }}><Skeleton h={15} w="60%" /><Skeleton h={12} w="40%" /></div></div>
      ))}
    </div>
  );
}

export function Empty({ icon, title, children, action }: { icon: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><div className="icon-wrap">{icon}</div><h3>{title}</h3>{children && <p style={{ margin: "0 auto 16px", maxWidth: 380 }}>{children}</p>}{action}</div>;
}

export function Bar({ value, max }: { value: number; max: number }) {
  const pct = max ? (value / max) * 100 : 0;
  return <div className="bar" role="presentation"><i style={{ width: `${pct}%`, background: pct >= 75 ? "#34c759" : pct >= 45 ? "var(--blue)" : "#ff9f0a" }} /></div>;
}

export function ErrorNote({ error, retry }: { error: Error | null; retry?: () => void }) {
  if (!error) return null;
  return <Callout tone="bad"><div>{error.message}</div>{retry && <button className="link" onClick={retry} style={{ marginTop: 6 }}>Try again</button>}</Callout>;
}

export function Row({ href, onClick, lead, title, sub, meta, trail }: { href?: string; onClick?: () => void; lead?: ReactNode; title: ReactNode; sub?: ReactNode; meta?: ReactNode; trail?: ReactNode }) {
  const inner = (
    <>
      {lead}
      <div className="row-main"><div className="row-title">{title}</div>{sub && <div className="row-sub">{sub}</div>}{meta && <div className="row-meta">{meta}</div>}</div>
      {trail}{(href || onClick) && <IconChevron className="chev" />}
    </>
  );
  if (href) return <Link className="row" href={href}>{inner}</Link>;
  if (onClick) return <button className="row" style={{ width: "100%" }} onClick={onClick}>{inner}</button>;
  return <div className="row">{inner}</div>;
}

// ───────────────────────── Sheet ─────────────────────────
export function Sheet({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow; document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={cx("sheet", wide && "wide")} role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet-grab" />
        <div className="sheet-head"><div className="sheet-title">{title}</div><button className="icon-btn" onClick={onClose} aria-label="Close"><IconClose /></button></div>
        {children}
      </div>
    </div>
  );
}

// ───────────────────────── Toasts ─────────────────────────
interface Toast { id: number; text: string; tone: "ok" | "err" }
const ToastCtx = createContext<(text: string, tone?: "ok" | "err") => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, tone }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "err" ? 6000 : 3200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">{items.map((t) => <div key={t.id} className={cx("toast", t.tone === "err" && "err")}>{t.tone === "err" ? <IconWarning /> : <IconCheck />}{t.text}</div>)}</div>
    </ToastCtx.Provider>
  );
}
