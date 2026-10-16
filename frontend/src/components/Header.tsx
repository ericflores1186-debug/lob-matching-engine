import type { ConnectionStatus } from "../hooks/useMarketData";
import type { Operator } from "../hooks/useOperator";

const STATUS_STYLE: Record<ConnectionStatus, { dot: string; label: string }> = {
  open: { dot: "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]", label: "Live" },
  connecting: { dot: "bg-amber-400 animate-pulse", label: "Connecting" },
  closed: { dot: "bg-rose-500", label: "Disconnected" },
};

export function Header({
  status,
  seq,
  operator,
  onOperator,
}: {
  status: ConnectionStatus;
  seq: number;
  operator: Operator;
  onOperator: (next: Partial<Operator>) => void;
}) {
  const style = STATUS_STYLE[status];
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-ink-950/90 backdrop-blur">
      <div className="mx-auto flex max-w-[1680px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
        <div className="flex items-center gap-3">
          <div className="flex h-7 w-7 flex-col justify-center gap-[3px] rounded-md bg-ink-800 px-1.5 ring-1 ring-line" aria-hidden>
            <span className="h-[3px] w-3 rounded-sm bg-rose-400" />
            <span className="h-[3px] w-4 rounded-sm bg-rose-400/60" />
            <span className="h-[3px] w-3.5 rounded-sm bg-emerald-400/60" />
            <span className="h-[3px] w-4 rounded-sm bg-emerald-400" />
          </div>
          <div>
            <div className="text-sm font-semibold tracking-tight text-slate-100">LOBX Operator Workbench</div>
            <div className="text-[11px] text-slate-500">Price-time matching · FIX 4.2/4.4 inspector · four-eyes replay</div>
          </div>
        </div>

        <div className="flex items-center gap-2 rounded-md bg-ink-900 px-2.5 py-1 ring-1 ring-line">
          <span className={`h-2 w-2 rounded-full ${style.dot}`} />
          <span className="text-xs text-slate-300">{style.label}</span>
          <span className="num text-[11px] text-slate-500">seq {seq}</span>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-3">
          <div className="flex rounded-md bg-ink-900 p-0.5 ring-1 ring-inset ring-line" role="radiogroup" aria-label="Active operator role">
            {(["MAKER", "CHECKER"] as const).map((role) => (
              <button
                key={role}
                type="button"
                role="radio"
                aria-checked={operator.role === role}
                onClick={() => onOperator({ role })}
                className={`rounded px-3 py-1 text-xs font-semibold tracking-wide ${
                  operator.role === role ? (role === "MAKER" ? "bg-sky-600 text-white" : "bg-violet-600 text-white") : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {role === "MAKER" ? "Maker" : "Checker"}
              </button>
            ))}
          </div>
          <OperatorField label="Maker ID" value={operator.makerId} active={operator.role === "MAKER"} onChange={(makerId) => onOperator({ makerId })} />
          <OperatorField label="Checker ID" value={operator.checkerId} active={operator.role === "CHECKER"} onChange={(checkerId) => onOperator({ checkerId })} />
        </div>
      </div>
    </header>
  );
}

function OperatorField({ label, value, active, onChange }: { label: string; value: string; active: boolean; onChange: (value: string) => void }) {
  return (
    <label className={`flex items-center gap-2 text-[11px] ${active ? "text-slate-300" : "text-slate-500"}`}>
      {label}
      <input
        className={`field w-40 py-1 font-mono text-xs ${active ? "border-sky-500/40" : ""}`}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/[^A-Za-z0-9._@-]/g, "").slice(0, 64))}
        spellCheck={false}
      />
    </label>
  );
}
