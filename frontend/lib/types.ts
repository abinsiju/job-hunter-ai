export type Gate = "PASS" | "REVIEW" | "FAIL" | null;
export type Status = "drafted" | "approved" | "applied" | "interview" | "offer" | "rejected" | "withdrawn";

export interface TrackedBrief { id: number; status: Status; employer: string; title: string | null }

export interface ListingRow {
  id: number; title: string; employer: string | null; location: string | null; source: string; source_label: string; snippet_only: boolean;
  score: number | null; gate: Gate; fail_reasons: string[]; flags: string[]; posted_at: string | null; url: string; similar: number;
  employment: string | null; salary: string | null; triaged: boolean; tracked: TrackedBrief | null;
  confidence: Confidence | null; open_status: "open" | "closed" | "unknown"; application_route: Route | null; discrepancy_count: number;
}
export type Confidence = "VERIFIED" | "THIRD_PARTY_FULL" | "SNIPPET_ONLY";
export type Route = "employer_ats" | "employer_site" | "recruiter" | "email" | "seek_quick_apply" | "unknown";
export interface Discrepancy { field: string; severity: "review" | "note"; label: string; third_party: string; employer: string }
export interface Verification {
  confidence: Confidence | null; open_status: "open" | "closed" | "unknown"; closed_reason: string | null; employer_url: string | null;
  application_route: Route | null; route_url: string | null; discrepancies: Discrepancy[]; notes: string[]; verified_at: string | null;
  third_party: { gate_status: Gate; score: number | null; fail_reasons: string[]; flags: string[] } | null;
}
export interface Category { points: number; max_points: number; matched: string[]; missing: string[]; notes: string[] }
export type Breakdown = Record<string, Category>;
export interface ListingDetail extends ListingRow {
  description: string; breakdown: Breakdown; explanation: string[]; dismissed: boolean;
  extract: { required_skills?: string[]; nice_to_have_skills?: string[]; qualifications?: { text: string; mandatory: boolean }[]; min_years_experience?: number | null;
    employment_type?: string; hours_per_week_min?: number | null; hours_per_week_max?: number | null; deadline?: string | null; requires_drivers_licence?: boolean; remote?: boolean };
  same_employer: { id: number; status: Status; title: string | null }[]; employer_blocked: boolean; can_draft: boolean; draft_hint: string | null; verification: Verification;
}
export interface ListingsResponse {
  total: number; items: ListingRow[];
  counts: { pass: number; review: number; fail: number; dismissed: number; closed: number; untriaged: number };
}
export interface HistoryEvent { status: Status; at: string; note: string | null }
export interface ApplicationRow {
  id: number; employer: string; title: string | null; location: string | null; status: Status; source: string; source_label: string; url: string | null;
  score: number | null; gate: Gate; drafted_at: string | null; approved_at: string | null; applied_at: string | null; interview_at: string | null;
  outcome_at: string | null; updated_at: string | null; documents: string[]; has_evidence: boolean; before_tracker: boolean; allowed_next: Status[];
  evidence?: string | null; notes?: string | null; history?: HistoryEvent[];
  same_employer?: TrackedBrief[]; listing_id?: number | null;
}
export interface ApplicationsResponse { total: number; counts: Record<Status, number>; items: ApplicationRow[] }
export interface Overview {
  owner: { first_name: string | null };
  listings: { total: number; pass: number; review: number; fail: number };
  pipeline: { status: Status; count: number }[]; applied_total: number; top_matches: ListingRow[];
  attention: { kind: "review_draft" | "record_application"; title: string; detail: string; application_id: number }[];
  activity: { at: string; status: Status; note: string | null; application_id: number; employer: string; title: string | null }[];
  has_discovery: boolean;
}
export interface Fact {
  id: number; category: string; key: string; value: unknown; status: "verified" | "needs_confirmation"; source: string; last_confirmed: string | null;
  visibility: "document" | "forms_only" | "internal"; notes: string | null; used_in_documents: boolean;
}
export interface Profile {
  groups: { category: string; label: string; facts: Fact[] }[];
  summary: { total: number; verified: number; needs_confirmation: number; used_in_documents: number };
}
export interface SystemStatus {
  services: { database: boolean; redis: boolean; ollama: { ok: boolean; version: string | null; loaded: string[] } };
  ai: { provider: string; chat_model: string; embed_model: string; context: number };
  discovery: { adzuna_configured: boolean };
  mail: { configured: boolean; account: string | null };
  counts: { listings: number; applications: number; facts: number; generated_folders: number };
  disk_free_gb: number; python: string; env: string; backup_hint: string;
}
export interface Job<T = unknown> { id: string; kind: string; status: "queued" | "running" | "done" | "error"; message: string; result: T | null; error: string | null }
export interface DraftResult {
  job: { title: string; employer: string | null; location: string | null; employment_type: string; required_skills: string[]; nice_to_have_skills: string[];
    min_years: number | null; qualifications: string[]; deadline: string | null };
  score: number; gate: { status: Gate; fail_reasons: string[]; flags: string[] }; breakdown: Breakdown; skipped_reason: string | null;
  warnings: string[]; files: string[]; application: ApplicationRow | null; same_employer: TrackedBrief[];
}
export interface DiscoverResult { fetched?: number; new?: number; known?: number; scored: number; waiting_to_score: number; reset?: number }
export interface AssistantCard { type: "listing" | "application" | "note"; lines?: string[] }
export interface AssistantReply {
  intent: string; text: string; suggestions: string[];
  cards: (AssistantCard & Partial<ListingRow> & Partial<ApplicationRow>)[];
}

export interface EmailInfo {
  configured: boolean; account: string | null; candidates: { address: string; near_apply_cue: boolean }[]; subject: string; body: string; attachments: string[];
  can_draft: boolean; reason: string | null; draft: { to: string; subject: string; at: string; folder: string; attachments: string[] } | null; compose_url: string;
}
export interface Blank { label: string; required?: boolean; type?: string; reason?: string; category?: string; step?: number | null }
export interface AssistStatus {
  state: "starting" | "opening" | "navigating" | "filling" | "waiting" | "ready" | "handed_over" | "completed" | "blocked" | "closed" | "error";
  stage?: string; message: string; platform: string | null;
  filled: { label: string; kind: string; source: string; category?: string; value?: string; step?: number | null }[]; left_blank: (Blank | string)[];
  captcha: boolean; login_required: boolean; screenshot: string | null; submit_clicked: boolean; missing_required?: number;
  gate?: { status: Gate; reasons: string[]; questions: string[] };
  needs_review?: { label: string; value: string; category: string; reason: string }[];
  uploads?: { label: string; file: string; ok: boolean; detail: string }[];
  timeline?: { at: string; frm: string; to: string; evidence: string[] }[]; notes?: string[]; pages?: string[]; step?: { n: number; of: number } | null;
  candidates?: { text: string; context: string }[];
  confirmation?: { url: string; text: string; screenshot: string | null; at: string } | null;
}
export interface AssistInfo { platforms: string[]; suggested_url: string | null; can_start: boolean; reason: string | null; running: boolean; status: AssistStatus | null }

export type SourceStatus = "API_CONNECTED" | "FEED_CONNECTED" | "PUBLIC_PAGES" | "MANUAL_IMPORT" | "SEARCH_LINK_ONLY" | "APPROVAL_PENDING" | "DISABLED" | "ERROR";
export interface SourceCard {
  source: string; label: string; status: SourceStatus; methods: string[]; permission: string; permission_note: string; auth: string; search_link: boolean; manual_import: boolean;
  returns_data: boolean; real_data_connection: boolean; jobs_discovered: number; enabled: boolean; notes: string[]; docs_url: string;
  last_success_at: string | null; last_link_at: string | null; last_import_at: string | null; last_error: string | null; last_error_at: string | null;
}
export interface SourcesResponse { sources: SourceCard[]; legend: Record<string, string> }
export interface SourceSearchResult {
  run_id: number; new_listing_ids: number[]; scored?: number; waiting_to_score?: number;
  sources: { source: string; label: string; status: string; ok: boolean; fetched_data: boolean; jobs_found: number; stored: Record<string, number>; link: string | null;
    filters_applied: string[]; filters_not_applied: string[]; error: string | null; error_code: string | null; took_ms: number }[];
  summary: { returned_data: string[]; link_only: string[]; failed: string[] };
}
export interface ImportResult { id: number; outcome: "created" | "merged" | "known"; title: string; employer: string | null; needs_scoring: boolean; sources: string[]; message: string }

export interface AtsReport {
  score: number; coverage_pct: number; similarity_pct: number; keywords_total: number; matched: string[]; missing: string[]; missing_skills: string[];
  format: { parsable: boolean; pages: number; headings_found: string[]; issues: string[] } | null; method: string; note: string;
  target: { goal: number; reached: boolean; potential_score: number; reachable_with_true_skills: boolean; add_if_true: { keyword: string; score_if_added: number }[] };
}
export interface QuickDraftResult extends DraftResult { ats: AtsReport | null; timings: Record<string, number>; elapsed_ms: number; within_budget: boolean; engine: string }
