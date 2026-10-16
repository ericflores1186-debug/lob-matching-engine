import type {
  BookView,
  DecodeResult,
  ExceptionItem,
  IngestResult,
  MessageDetail,
  RebuildResult,
  Remediation,
  RemediationAction,
  SamplePayload,
  SymbolSummary,
} from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const payload = (body ?? {}) as { error?: string; detail?: unknown };
    const detail = payload.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string; loc?: unknown[] }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? ""}`).join("; ")
          : response.statusText;
    throw new ApiError(response.status, payload.error ?? `HTTP_${response.status}`, message);
  }
  return body as T;
}

function post<T>(path: string, body?: unknown, headers?: Record<string, string>): Promise<T> {
  return request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body), headers });
}

export async function idempotencyKey(payload: string): Promise<string> {
  // Content hash: re-submitting the same log is recognised server-side and answered from the ledger
  if (globalThis.crypto?.subtle) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
    return "sha256:" + Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  }
  return `ui-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export interface RemediationRequestBody {
  exception_message_id: number;
  action: RemediationAction;
  maker_id: string;
  reason: string;
  overrides: Record<string, string>;
  bypass_risk_checks: boolean;
}

export const api = {
  symbols: () => request<SymbolSummary[]>("/api/symbols"),
  book: (symbol: string, depth = 10) => request<BookView>(`/api/book/${encodeURIComponent(symbol)}?depth=${depth}`),
  rebuild: (symbol: string) => post<RebuildResult>(`/api/book/${encodeURIComponent(symbol)}/rebuild`),
  sample: () => request<SamplePayload>("/api/fix/sample"),
  decode: (payload: string) => post<DecodeResult>("/api/fix/decode", { payload }),
  ingest: async (payload: string) => post<IngestResult>("/api/fix/ingest", { payload }, { "Idempotency-Key": await idempotencyKey(payload) }),
  exceptions: (scope: "open" | "all") => request<ExceptionItem[]>(`/api/exceptions?scope=${scope}`),
  message: (id: number) => request<MessageDetail>(`/api/messages/${id}`),
  remediations: () => request<Remediation[]>("/api/remediation?limit=500"),
  createRemediation: (body: RemediationRequestBody) => post<Remediation>("/api/remediation/create", body),
  approve: (id: number, checker_id: string, comment: string | null) => post<Remediation>(`/api/remediation/${id}/approve`, { checker_id, comment }),
  decline: (id: number, checker_id: string, comment: string | null) => post<Remediation>(`/api/remediation/${id}/decline`, { checker_id, comment }),
};
