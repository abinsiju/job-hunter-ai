import type { Gate, Status } from "./types";

export const STATUS_LABEL: Record<Status, string> = {
  drafted: "Draft", approved: "Approved", applied: "Applied", interview: "Interview", offer: "Offer", rejected: "Not selected", withdrawn: "Withdrawn",
};
export const STATUS_TONE: Record<Status, string> = {
  drafted: "", approved: "blue", applied: "indigo", interview: "purple", offer: "green", rejected: "red", withdrawn: "",
};
export const STATUS_COLOR: Record<Status, string> = {
  drafted: "#8e8e93", approved: "#0a84ff", applied: "#5e5ce6", interview: "#bf5af2", offer: "#34c759", rejected: "#ff453a", withdrawn: "#aeaeb2",
};
export const GATE_LABEL: Record<NonNullable<Gate>, string> = { PASS: "Good match", REVIEW: "Check first", FAIL: "Not eligible" };
export const GATE_TONE: Record<NonNullable<Gate>, string> = { PASS: "green", REVIEW: "orange", FAIL: "red" };
export const CATEGORY_LABEL: Record<string, string> = {
  technical_skills: "Technical skills", experience: "Experience", qualification: "Qualifications", role_relevance: "Role fit",
  location_preferences: "Location & hours", other_eligibility: "Other eligibility",
};
export const EMPLOYMENT_LABEL: Record<string, string> = {
  full_time: "Full-time", part_time: "Part-time", casual: "Casual", contract: "Contract", temporary: "Temporary", internship: "Internship",
  graduate_program: "Graduate program", unknown: "",
};

export function scoreColor(score: number | null): string {
  if (score == null) return "var(--text-3)";
  return score >= 80 ? "#34c759" : score >= 65 ? "#0a84ff" : "#ff9f0a";
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).getTime();
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const days = Math.floor(s / 86400);
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`;
  return new Date(d).toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" });
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString("en-AU", { day: "numeric", month: "short", year: "numeric" });
}

export function greeting(now: Date | null): string {
  if (!now) return "Hello";
  const h = now.getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function plural(n: number, one: string, many = `${one}s`): string { return `${n} ${n === 1 ? one : many}`; }
