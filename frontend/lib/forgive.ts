// 18 Postel's law: be liberal in what you accept. People paste links with spaces and quotes, type "80k" or "$80,000", and forget "https://".
/** A web address from whatever was pasted: trims, drops quotes and brackets, adds https:// when it is missing. Empty if it is not recognisable. */
export function normaliseUrl(input: string): string {
  let s = (input ?? "").replace(/[​-‍﻿]/g, "").trim().replace(/^[<"'(\[]+|[>"')\].,;]+$/g, "").trim();
  if (!s) return "";
  if (/^javascript:|^data:|^file:/i.test(s)) return "";
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) {
    if (!/^[^\s/]+\.[a-z]{2,}([/?#].*)?$/i.test(s)) return s; // not address-shaped: leave it for the server to explain
    s = "https://" + s;
  }
  return s.replace(/\s+/g, "");
}

/** A money amount from "80k", "$80,000", "80 000", "80K/yr", "$35.50", "1.2m". null if there is no number. */
export function parseMoney(input: string): number | null {
  const m = (input ?? "").toLowerCase().replace(/[,\s$aud]/g, "").match(/^(\d+(?:\.\d+)?)(k|m)?/);
  if (!m) return null;
  const n = parseFloat(m[1]) * (m[2] === "k" ? 1_000 : m[2] === "m" ? 1_000_000 : 1);
  return Number.isFinite(n) ? n : null;
}

/** Number of kilometres or days from "20", "20km", " 20 km ". */
export function parseCount(input: string): number | null {
  const m = (input ?? "").match(/(\d+(?:\.\d+)?)/);
  return m ? Math.round(parseFloat(m[1])) : null;
}

/** Collapses stray whitespace without touching line breaks (for titles, employers, places). */
export const tidy = (s: string) => (s ?? "").replace(/[ \t ]+/g, " ").trim();
