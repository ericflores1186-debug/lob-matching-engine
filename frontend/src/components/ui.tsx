import { createContext, useCallback, useContext, useMemo, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function Panel({
  title,
  subtitle,
  actions,
  className = "",
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`flex min-w-0 flex-col overflow-hidden rounded-lg border border-line bg-ink-900 ${className}`}>
      <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line bg-ink-850/60 px-4 py-2.5">
        <div className="min-w-0">
          <h2 className="text-[12px] font-semibold uppercase tracking-[0.12em] text-slate-300">{title}</h2>
          {subtitle ? <div className="mt-0.5 truncate text-xs text-slate-500">{subtitle}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost" | "success";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-sky-600 text-white hover:bg-sky-500 border-sky-500/50",
  success: "bg-emerald-600 text-white hover:bg-emerald-500 border-emerald-500/50",
  danger: "bg-rose-600/90 text-white hover:bg-rose-500 border-rose-500/50",
  secondary: "bg-ink-800 text-slate-200 hover:bg-ink-700 border-line",
  ghost: "bg-transparent text-slate-400 hover:text-slate-200 hover:bg-ink-800 border-transparent",
};

export function Button({
  variant = "secondary",
  size = "sm",
  busy = false,
  className = "",
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "xs" | "sm"; busy?: boolean }) {
  const sizing = size === "xs" ? "px-2 py-1 text-[11px]" : "px-3 py-1.5 text-xs";
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || busy}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 disabled:cursor-not-allowed disabled:opacity-40 ${sizing} ${VARIANTS[variant]} ${className}`}
    >
      {busy ? <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden /> : null}
      {children}
    </button>
  );
}

const TONES: Record<string, string> = {
  VALID: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  EXECUTED: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  FILLED: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  TRADE: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  NEW: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  PARTIALLY_FILLED: "bg-teal-500/10 text-teal-300 ring-teal-500/30",
  REPLACED: "bg-violet-500/10 text-violet-300 ring-violet-500/30",
  CANCELED: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
  DECLINED: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
  POSS_DUP: "bg-slate-500/10 text-slate-300 ring-slate-500/30",
  PENDING_APPROVAL: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  SEQ_GAP: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  STALE_TIMESTAMP: "bg-yellow-500/10 text-yellow-300 ring-yellow-500/30",
  DUPLICATE_CLORDID: "bg-orange-500/10 text-orange-300 ring-orange-500/30",
  BUSINESS_REJECT: "bg-orange-500/10 text-orange-300 ring-orange-500/30",
  COLLAR_REJECT: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  REJECTED: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  CANCEL_REJECT: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  FAILED: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  CHECKSUM_ERR: "bg-red-500/15 text-red-300 ring-red-500/40",
  BODY_LENGTH_ERR: "bg-red-500/15 text-red-300 ring-red-500/40",
  MALFORMED: "bg-red-500/15 text-red-300 ring-red-500/40",
  SEQ_TOO_LOW: "bg-red-500/15 text-red-300 ring-red-500/40",
};

export function Badge({ value, className = "" }: { value: string; className?: string }) {
  const tone = TONES[value] ?? "bg-slate-500/10 text-slate-300 ring-slate-500/30";
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wide ring-1 ring-inset ${tone} ${className}`}>
      {value.replace(/_/g, " ")}
    </span>
  );
}

export function SideTag({ side }: { side: string }) {
  const buy = side === "BUY";
  const sell = side.startsWith("SELL");
  return <span className={`font-mono text-[11px] font-semibold ${buy ? "text-emerald-400" : sell ? "text-rose-400" : "text-slate-500"}`}>{side.replace(/_/g, " ") || "—"}</span>;
}

export function Stat({ label, value, tone = "" }: { label: string; value: ReactNode; tone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`num truncate text-sm text-slate-200 ${tone}`}>{value}</div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-full min-h-[120px] items-center justify-center px-6 text-center text-sm text-slate-500">{children}</div>;
}

interface Toast {
  id: number;
  tone: "error" | "success" | "info";
  title: string;
  detail?: string;
}

const ToastContext = createContext<{ push: (toast: Omit<Toast, "id">) => void; error: (err: unknown, title?: string) => void } | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const push = useCallback((toast: Omit<Toast, "id">) => {
    const id = nextId.current++;
    setToasts((current) => [...current.slice(-3), { ...toast, id }]);
    window.setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), toast.tone === "error" ? 8_000 : 4_500);
  }, []);
  const error = useCallback(
    (err: unknown, title = "Request failed") => {
      if (err instanceof ApiError) push({ tone: "error", title: `${title} · ${err.code}`, detail: err.message });
      else push({ tone: "error", title, detail: err instanceof Error ? err.message : String(err) });
    },
    [push],
  );
  const value = useMemo(() => ({ push, error }), [push, error]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[380px] max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`pointer-events-auto rounded-md border px-3 py-2 shadow-lg shadow-black/40 backdrop-blur ${
              t.tone === "error" ? "border-rose-500/40 bg-rose-950/80" : t.tone === "success" ? "border-emerald-500/40 bg-emerald-950/80" : "border-sky-500/40 bg-ink-800/90"
            }`}
          >
            <div className="text-xs font-semibold text-slate-100">{t.title}</div>
            {t.detail ? <div className="mt-0.5 break-words text-xs text-slate-300">{t.detail}</div> : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}
