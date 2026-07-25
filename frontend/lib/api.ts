/**
 * Typed client for the backend.
 *
 * Requests go through Next's `/api/backend/*` rewrite (see next.config.ts), which
 * forwards to FastAPI. That keeps the browser on one origin, so no CORS
 * preflight and no backend URL baked into the bundle.
 */

const BASE = "/api/backend";

/** Raised for any non-2xx response, carrying the backend's own message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { cache: "no-store", ...init });
  if (!response.ok) {
    // FastAPI puts the useful text in `detail`. Surfacing it beats "500".
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body; the status line is all we have.
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

const get = <T>(path: string) => request<T>(path);

const send = <T>(method: "POST" | "PATCH" | "PUT" | "DELETE", path: string, body?: unknown) =>
  request<T>(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const query = (params: Record<string, string | number | boolean | undefined>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
};

// ---------------------------------------------------------------------------
// Types — mirror app/schemas.py
// ---------------------------------------------------------------------------

/** Whether text was written by the local model or assembled from the numbers. */
export type Origin = "model" | "computed";

export interface MonthlySpend {
  month: string;
  debit_total: number;
  credit_total: number;
  net: number;
}

export interface CategorySpend {
  category: string;
  total: number;
  count: number;
  is_essential: boolean;
}

export interface MerchantSpend {
  merchant: string;
  total: number;
  count: number;
  last_seen: string;
}

export interface PersonOut {
  id: number;
  name: string;
  relationship_type: string;
  notes: string | null;
}

export interface PersonBalance {
  person: PersonOut;
  they_owe_you: number;
  transaction_count: number;
}

export interface DashboardSummary {
  /** Gross money out, including transfers between your own accounts. */
  total_debit: number;
  /** What was actually spent: total_debit minus those internal transfers. */
  spend: number;
  internal_transfers: number;
  total_credit: number;
  /** total_credit - spend. Netting the gross figure double-counts card spend. */
  net: number;
  transaction_count: number;
  needs_review: number;
  monthly: MonthlySpend[];
  by_category: CategorySpend[];
  top_merchants: MerchantSpend[];
  people: PersonBalance[];
}

export interface PipelineRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "error";
  trigger: "demo" | "files" | "upload";
  transactions_processed: number;
  rule_tagged: number;
  llm_tagged: number;
  needs_review: number;
  /** False means some rows were deliberately left untagged. */
  llm_available: boolean;
  llm_failed: number;
  error_message: string | null;
  node_timings_ms: Record<string, number>;
}

export interface Transaction {
  id: number;
  external_id: string;
  posted_at: string;
  amount: number;
  direction: "debit" | "credit";
  source: string;
  raw_description: string;
  merchant_normalized: string | null;
  category: string | null;
  subcategory: string | null;
  /** null means nothing has categorised this row — not a failed model call. */
  tag_source: "rule" | "llm" | "validator" | "user" | null;
  tag_confidence: number | null;
  tag_reason: string | null;
  person_id: number | null;
  is_loan: boolean;
  needs_review: boolean;
}

export interface InsightCard {
  title: string;
  body: string;
  severity: "good" | "info" | "warn" | "critical";
  metric: string | null;
  generated_by: Origin;
}

export interface SystemHealth {
  ok: boolean;
  app: { name: string; version: string };
  llm: {
    ok: boolean;
    host: string;
    model: string;
    model_pulled: boolean;
    available_models?: string[];
    error?: string;
  };
  config: {
    llm_first: boolean;
    confidence_threshold: number;
    thinking_enabled: boolean;
  };
  stats: { transactions: number; pipeline_runs: number; needs_review: number };
}

export interface PipelineTopology {
  nodes: { id: string; label: string; kind: "deterministic" | "llm"; mode?: string }[];
  edges: { from: string; to: string; conditional?: boolean }[];
}

export interface IngestResult {
  file: string;
  parser: string;
  parsed: number;
  inserted: number;
  duplicates: number;
  rule_tagged: number | null;
  llm_tagged: number | null;
  needs_review: number | null;
  llm_available: boolean | null;
}

export interface SupportedFormat {
  name: string;
  label: string;
  extensions: string;
}

export interface ReviewGroup {
  counterparty: string;
  transaction_ids: number[];
  total: number;
  count: number;
  suggested_category: string | null;
}

export interface DetectedFriend {
  canonical_name: string;
  upi_ids: string[];
  sent_total: number;
  received_total: number;
  net_owed_to_you: number;
  confidence: number;
  reason: string;
}

export interface CategoryRow {
  id: number;
  name: string;
  icon: string | null;
  color: string | null;
  is_essential: boolean;
}

/** What the vision model read off a receipt image. Mirrors RECEIPT_SCHEMA. */
export interface ReceiptExtraction {
  is_receipt: boolean;
  merchant: string | null;
  amount: number | null;
  date: string | null;
  category: string;
  confidence: number;
  items: string[];
}

export interface ReceiptScanResult {
  /** False means the model could not be reached — not that the image was bad. */
  ok: boolean;
  error: string | null;
  /** Present when the scan succeeded but produced nothing usable. */
  message?: string;
  extracted: ReceiptExtraction | null;
  transaction: Transaction | null;
}

export interface TransactionFilters {
  limit?: number;
  offset?: number;
  category?: string;
  direction?: "debit" | "credit";
  needs_review?: boolean;
  untagged?: boolean;
  person_id?: number;
  search?: string;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  health: () => get<SystemHealth>("/system/health"),
  reset: () => send<{ deleted: Record<string, number> }>("POST", "/system/reset"),

  topology: () => get<PipelineTopology>("/pipeline/topology"),
  runDemo: (months = 12, seed = 42) =>
    send<PipelineRun>("POST", `/pipeline/run${query({ months, seed })}`),
  runs: (limit = 20) => get<PipelineRun[]>(`/pipeline/runs${query({ limit })}`),

  dashboard: (months = 12) => get<DashboardSummary>(`/dashboard/summary${query({ months })}`),
  insights: (months = 12) => get<InsightCard[]>(`/insights${query({ months })}`),

  transactions: (filters: TransactionFilters = {}) =>
    get<Transaction[]>(`/transactions${query({ limit: 100, ...filters })}`),
  setTag: (id: number, category: string, subcategory?: string) =>
    send<Transaction>("PATCH", `/transactions/${id}/tag`, { category, subcategory }),
  bulkTag: (transaction_ids: number[], category: string, subcategory?: string) =>
    send<{ updated: number; not_found: number }>("POST", "/transactions/bulk-tag", {
      transaction_ids,
      category,
      subcategory,
    }),

  people: () => get<PersonBalance[]>("/people"),
  categories: () => get<CategoryRow[]>("/categories"),
  reviewGroups: (limit = 30) => get<ReviewGroup[]>(`/review/groups${query({ limit })}`),

  patterns: () => get<Record<string, unknown[]>>("/patterns"),
  trends: (granularity: "daily" | "weekly" | "monthly" = "monthly", months = 12) =>
    get<{ period: string; debit: number; credit: number; net: number }[]>(
      `/trends${query({ granularity, months })}`,
    ),
  budgetEnvelopes: () => get<Record<string, unknown>[]>("/budget/envelopes"),

  detectFriends: () => get<DetectedFriend[]>("/agents/friend-detect"),
  applyFriends: () => send<Record<string, unknown>>("POST", "/agents/friend-detect"),
  runValidator: (limit?: number) =>
    send<Record<string, unknown>>("POST", `/agents/validate${query({ limit })}`),

  supportedFormats: () => get<SupportedFormat[]>("/ingest/formats"),

  /** Statement upload. FormData, so no JSON Content-Type. */
  uploadStatement: async (file: File, categorise = true): Promise<IngestResult> => {
    const form = new FormData();
    form.append("file", file);
    return request<IngestResult>(`/ingest/file${query({ categorise })}`, {
      method: "POST",
      body: form,
    });
  },

  scanReceipt: async (file: File, save = true): Promise<ReceiptScanResult> => {
    const form = new FormData();
    form.append("file", file);
    return request<ReceiptScanResult>(`/receipt/scan${query({ save })}`, {
      method: "POST",
      body: form,
    });
  },

  chat: (messages: { role: string; content: string }[]) =>
    send<{ ok: boolean; reply: string | null; error: string | null }>("POST", "/chat", {
      messages,
    }),
  chatStreamUrl: () => `${BASE}/chat/stream`,

  reportUrl: () => `${BASE}/report/spend-analysis`,
};
