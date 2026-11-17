"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { AssistantSheet } from "./assistant";
import { FindJobsSheet } from "./findjobs";
import { IconGear, IconJobs, IconLink, IconLogo, IconPerson, IconSparkle, IconStudio, IconToday, IconTracker } from "./icons";
import { useSlowRequest } from "@/lib/api";
import { ToastProvider } from "./ui";

interface Shell { openAssistant: () => void; openFind: () => void }
const ShellCtx = createContext<Shell>({ openAssistant: () => {}, openFind: () => {} });
export const useShell = () => useContext(ShellCtx);

// 20 frequency of use: what you use every day comes first (and is on the phone tab bar); what you use rarely sits lower, and rare things are desktop-only.
// 16 Miller: five items on the phone bar.
const NAV = [
  { href: "/", label: "Today", Icon: IconToday }, { href: "/jobs", label: "Jobs", Icon: IconJobs }, { href: "/quick", label: "Quick draft", short: "Quick", Icon: IconSparkle },
  { href: "/tracker", label: "Tracker", Icon: IconTracker }, { href: "/studio", label: "Draft studio", short: "Draft", Icon: IconStudio, desktopOnly: true },
  { href: "/sources", label: "Sources", Icon: IconLink, desktopOnly: true }, { href: "/profile", label: "Profile", Icon: IconPerson, desktopOnly: true }, { href: "/settings", label: "Settings", Icon: IconGear },
];
const TITLES: Record<string, string> = { "/": "Today", "/jobs": "Jobs", "/quick": "Quick draft", "/studio": "Draft studio", "/tracker": "Tracker", "/sources": "Sources", "/profile": "Profile", "/settings": "Settings" };
const active = (path: string, href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [assistant, setAssistant] = useState(false);
  const [find, setFind] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 46);
    onScroll(); window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [path]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setAssistant((v) => !v); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);
  const slow = useSlowRequest();
  const base = "/" + (path.split("/")[1] ?? "");
  const title = TITLES[base] ?? "Job Hunter";

  return (
    <ToastProvider>
      <ShellCtx.Provider value={{ openAssistant: () => setAssistant(true), openFind: () => setFind(true) }}>
        <div className="netbar" data-on={slow} aria-hidden="true" data-testid="netbar" />
        <aside className="sidebar" aria-label="Main">
          <div className="brand"><span className="brand-mark"><IconLogo style={{ width: 18, height: 18 }} /></span>Job Hunter</div>
          {NAV.map(({ href, label, Icon }) => <Link key={href} href={href} className="nav-item" aria-current={active(path, href) ? "page" : undefined}><Icon />{label}</Link>)}
          <div className="nav-sep" />
          <button className="nav-item" onClick={() => setAssistant(true)} style={{ width: "100%", textAlign: "left" }}><IconSparkle />Ask<span style={{ marginLeft: "auto" }}><kbd>⌘K</kbd></span></button>
          <div className="nav-foot">Runs on your Mac. Nothing is ever submitted for you.</div>
        </aside>
        <div className="main">
          <header className="topbar" data-scrolled={scrolled}>
            <div className="brand mobile-brand" style={{ padding: 0, fontSize: 15 }}>
              <span className="brand-mark" style={{ width: 26, height: 26, borderRadius: 7 }}><IconLogo style={{ width: 15, height: 15 }} /></span>
            </div>
            <div className="topbar-title">{title}</div>
            <button className="icon-btn" onClick={() => setAssistant(true)} aria-label="Ask"><IconSparkle /></button>
          </header>
          <main className="page">{children}</main>
        </div>
        <nav className="tabbar" aria-label="Main">
          {NAV.filter((n) => !n.desktopOnly).map(({ href, label, short, Icon }) => (
            <Link key={href} href={href} className="tab" aria-current={active(path, href) ? "page" : undefined}><Icon />{short ?? label}</Link>
          ))}
        </nav>
        <AssistantSheet open={assistant} onClose={() => setAssistant(false)} />
        <FindJobsSheet open={find} onClose={() => setFind(false)} />
      </ShellCtx.Provider>
    </ToastProvider>
  );
}
