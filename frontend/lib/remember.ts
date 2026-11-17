// 20 Frequency of use: what you use most should already be set up the way you left it. Never throws: storage can be blocked or empty.
export function recall<T>(key: string, fallback: T): T {
  try {
    const v = window.localStorage.getItem("jh:" + key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch { return fallback; }
}
export function remember(key: string, value: unknown): void {
  try { window.localStorage.setItem("jh:" + key, JSON.stringify(value)); } catch { /* private window or blocked storage: carry on without it */ }
}
