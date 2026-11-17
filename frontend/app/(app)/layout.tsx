import { Suspense } from "react";
import { AppShell } from "@/components/shell";

export default function AppLayout({ children }: LayoutProps<"/">) {
  // usePathname() is runtime data on dynamic routes (/jobs/[id]); the boundary lets everything else prerender.
  return <Suspense fallback={<div className="main" aria-busy="true" />}><AppShell>{children}</AppShell></Suspense>;
}
