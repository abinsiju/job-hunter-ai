"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { Job } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string) { super(message); }
}

function messageFrom(body: unknown, fallback: string): { message: string; code?: string } {
  const d = (body as { detail?: unknown })?.detail;
  if (typeof d === "string") return { message: d };
  if (d && typeof d === "object" && "message" in d) return { message: String((d as { message: unknown }).message), code: (d as { code?: string }).code };
  if (Array.isArray(d) && d[0]?.msg) return { message: String(d[0].msg) };
  return { message: fallback };
}

let pending = 0;
const listeners = new Set<(n: number) => void>();
const bump = (d: number) => { pending += d; listeners.forEach((f) => f(pending)); };
/** True when a request has been running for more than ~150 ms (below that the page already feels instant). */
export function useSlowRequest(): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const on = (n: number) => {
      if (n > 0 && !timer) timer = setTimeout(() => setSlow(true), 150);
      if (n === 0) { if (timer) clearTimeout(timer); timer = null; setSlow(false); }
    };
    listeners.add(on);
    return () => { listeners.delete(on); if (timer) clearTimeout(timer); };
  }, []);
  return slow;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  bump(1);
  try { return await apiInner<T>(path, init); } finally { bump(-1); }
}

async function apiInner<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`/api/v1${path}`, {
    ...rest, cache: "no-store",
    headers: { ...(json !== undefined ? { "content-type": "application/json" } : {}), ...headers },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 401) {
    if (typeof window !== "undefined" && !location.pathname.startsWith("/login")) // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- a full reload on session expiry also clears all client state
    window.location.assign(`/login?next=${encodeURIComponent(location.pathname)}`);
    throw new ApiError(401, "Your session has ended. Please sign in again.");
  }
  const text = await res.text();
  let body: unknown = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* non-JSON body */ }
  if (!res.ok) {
    const { message, code } = messageFrom(body, res.status >= 500 ? "Something went wrong on the server." : `Request failed (${res.status})`);
    throw new ApiError(res.status, message, code);
  }
  return body as T;
}

// Stale-while-revalidate: pages show cached data instantly on back/forward, then refresh.
const cache = new Map<string, unknown>();

export function useApi<T>(path: string | null, opts: { refreshMs?: number } = {}) {
  // State is only ever set after a fetch completes (never synchronously inside an effect). What the component sees is derived
  // during render: the latest result for THIS path, else whatever is cached for it.
  const [res, setRes] = useState<{ path: string; data?: T; error?: ApiError } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api<T>(path);
      cache.set(path, d);
      if (alive.current) setRes({ path, data: d });
    } catch (e) {
      if (alive.current) setRes((r) => ({ path, data: r?.path === path ? r.data : undefined, error: e as ApiError }));
    }
  }, [path]);

  useEffect(() => {
    load();
    if (!opts.refreshMs) return;
    const t = setInterval(load, opts.refreshMs);
    return () => clearInterval(t);
  }, [load, opts.refreshMs]);

  const mine = res && res.path === path ? res : null;
  const data = (mine?.data ?? (path ? (cache.get(path) as T | undefined) : undefined)) as T | undefined;
  const error = mine?.error ?? null;
  const loading = !!path && data === undefined && !error;
  return { data, error, loading, reload: load, setData: (d: T) => { if (path) { cache.set(path, d); setRes({ path, data: d }); } } };
}

export function invalidate(prefix = "") { for (const k of [...cache.keys()]) if (k.startsWith(prefix)) cache.delete(k); }

/** Start a background job and follow it until it finishes. */
export function useJob<R = unknown>() {
  const [job, setJob] = useState<Job<R> | null>(null);
  const [running, setRunning] = useState(false);
  const stop = useRef(false);
  useEffect(() => () => { stop.current = true; }, []);

  const run = useCallback(async (kind: string, params: Record<string, unknown>): Promise<Job<R>> => {
    setRunning(true); setJob(null); stop.current = false;
    try {
      let j = await api<Job<R>>("/jobs", { method: "POST", json: { kind, params } });
      setJob(j);
      while (!stop.current && (j.status === "queued" || j.status === "running")) {
        await new Promise((r) => setTimeout(r, 900));
        j = await api<Job<R>>(`/jobs/${j.id}`);
        setJob(j);
      }
      invalidate();
      return j;
    } finally { setRunning(false); }
  }, []);
  return { job, running, run, reset: () => setJob(null) };
}
