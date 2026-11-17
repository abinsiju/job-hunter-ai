"use client";
import { useSyncExternalStore } from "react";

/** Live CSS media query. False on the server and during hydration, so markup never mismatches. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (cb) => { const m = window.matchMedia(query); m.addEventListener("change", cb); return () => m.removeEventListener("change", cb); },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Current time, ticking each minute. Null on the server and during hydration so prerendered output never bakes in a date. */
export function useNow(): Date | null {
  const minute = useSyncExternalStore(
    (cb) => { const t = setInterval(cb, 60_000); return () => clearInterval(t); },
    () => Math.floor(Date.now() / 60_000),
    () => 0,
  );
  return minute ? new Date(minute * 60_000) : null;
}
