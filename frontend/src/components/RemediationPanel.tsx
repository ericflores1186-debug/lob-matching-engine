import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { ExceptionItem, Issue, Remediation, RemediationAction } from "../api/types";
import type { Operator } from "../hooks/useOperator";
import { fmtTime, shortHash } from "../lib/format";
import { Badge, Button, useToast } from "./ui";

const ACTION_HELP: Record<RemediationAction, string> = {
  REPLAY: "Inject the original bytes into the matching engine unchanged.",
  AMEND_AND_REPLAY: "Override specific tags, re-frame with recomputed BodyLength(9) and CheckSum(10), then inject.",
  CANCEL_REPLACE: "Synthesize a 35=G (PossDup) against the live resting order linked to this exception.",
};

interface OverrideRow {
  tag: string;
  value: string;
}

function suggestedOverrides(exception: ExceptionItem): OverrideRow[] {
  switch (exception.status) {
    case "COLLAR_REJECT":
      return [{ tag: "44", value: "" }];
    case "DUPLICATE_CLORDID":
      return [{ tag: "11", value: exception.cl_ord_id ? `${exception.cl_ord_id}-B` : "" }];
    case "CHECKSUM_ERR":
    case "BODY_LENGTH_ERR":
      return [{ tag: "", value: "" }];
    default:
      return [{ tag: "", value: "" }];
  }
}

export function RemediationPanel({
  exception,
  requests,
  operator,
  onChanged,
}: {
  exception: ExceptionItem;
  requests: Remediation[];
  operator: Operator;
  onChanged: () => void;
}) {
  const pending = requests.find((r) => r.status === "PENDING_APPROVAL");
  const executed = requests.find((r) => r.status === "EXECUTED");
  const history = requests.filter((r) => r !== pending && r !== executed);

  return (
    <div className="flex min-w-0 flex-col gap-3 p-4">
      {executed ? <RequestCard request={executed} /> : null}
      {pending ? <PendingCard request={pending} operator={operator} onChanged={onChanged} /> : null}
      {!pending && !executed ? <MakerForm exception={exception} operator={operator} onChanged={onChanged} /> : null}
      {history.length > 0 ? (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-slate-500">Prior requests</div>
          {history.map((r) => (
            <RequestCard key={r.id} request={r} compact />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MakerForm({ exception, operator, onChanged }: { exception: ExceptionItem; operator: Operator; onChanged: () => void }) {
  const toast = useToast();
  const allowed = exception.allowed_actions;
  const [action, setAction] = useState<RemediationAction | undefined>(allowed[0]);
  const [rows, setRows] = useState<OverrideRow[]>(() => suggestedOverrides(exception));
  const [reason, setReason] = useState("");
  const [bypass, setBypass] = useState(false);
  const [busy, setBusy] = useState(false);
  const isMaker = operator.role === "MAKER";

  if (allowed.length === 0 || !action) {
    return (
      <div className="rounded-md border border-line bg-ink-850 px-3 py-3 text-xs text-slate-400">
        {exception.status === "POSS_DUP"
          ? "PossDup resend of an already-processed sequence number — informational only."
          : `35=${exception.msg_type ?? "?"} cannot be replayed into the matching engine.`}
      </div>
    );
  }

  const overrides = Object.fromEntries(rows.filter((r) => r.tag.trim() && r.value.trim()).map((r) => [r.tag.trim(), r.value.trim()]));
  const needsOverrides = action !== "REPLAY";
  // A garbled frame may be re-framed as-is; any other amend must change at least one tag
  const reframeOnly = exception.status === "CHECKSUM_ERR" || exception.status === "BODY_LENGTH_ERR";
  const missingOverride = action === "AMEND_AND_REPLAY" && Object.keys(overrides).length === 0 && !reframeOnly;

  const stage = async () => {
    setBusy(true);
    try {
      await api.createRemediation({
        exception_message_id: exception.id,
        action,
        maker_id: operator.makerId,
        reason,
        overrides: needsOverrides ? overrides : {},
        bypass_risk_checks: bypass,
      });
      toast.push({ tone: "success", title: `Staged ${action.replace(/_/g, " ")} for #${exception.id}`, detail: "Pending approval by a second operator." });
      onChanged();
    } catch (err) {
      toast.error(err, "Could not stage remediation");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-md border border-line bg-ink-850">
      <div className="flex items-center justify-between border-b border-line px-3 py-2">
        <span className="text-xs font-semibold text-slate-200">Stage remediation</span>
        <span className="text-[10px] uppercase tracking-wider text-slate-500">Maker · {operator.makerId}</span>
      </div>
      <div className="space-y-3 p-3">
        <div className="flex flex-wrap gap-1.5">
          {allowed.map((a) => (
            <button
              key={a}
              type="button"
              onClick={() => setAction(a)}
              className={`rounded px-2 py-1 font-mono text-[10px] uppercase ring-1 ring-inset ${
                a === action ? "bg-sky-500/15 text-sky-200 ring-sky-500/40" : "text-slate-400 ring-line hover:bg-ink-800"
              }`}
            >
              {a.replace(/_/g, " ")}
            </button>
          ))}
        </div>
        <p className="text-xs text-slate-500">{ACTION_HELP[action]}</p>

        {needsOverrides ? (
          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider text-slate-500">Tag overrides</div>
            {rows.map((row, i) => (
              <div key={i} className="flex gap-2">
                <input
                  className="field w-20 font-mono"
                  placeholder="tag"
                  inputMode="numeric"
                  value={row.tag}
                  onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, tag: e.target.value.replace(/\D/g, "") } : r)))}
                />
                <input
                  className="field min-w-0 flex-1 font-mono"
                  placeholder={row.tag === "44" ? "corrected limit price, e.g. 431.25" : "value"}
                  value={row.value}
                  onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))}
                />
                <Button variant="ghost" size="xs" onClick={() => setRows(rows.filter((_, j) => j !== i))} disabled={rows.length === 1}>
                  ✕
                </Button>
              </div>
            ))}
            <Button variant="ghost" size="xs" onClick={() => setRows([...rows, { tag: "", value: "" }])}>
              + Add tag
            </Button>
          </div>
        ) : null}

        <label className="flex items-start gap-2 text-xs text-slate-400">
          <input type="checkbox" className="mt-0.5 accent-amber-500" checked={bypass} onChange={(e) => setBypass(e.target.checked)} />
          <span>
            Bypass price collar / notional limits <span className="text-slate-600">(checker sees this flag)</span>
          </span>
        </label>
        <textarea
          className="field h-16 w-full resize-none text-xs"
          placeholder="Justification (required) — e.g. client confirmed 34=7 is live after ResendRequest timed out"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] text-slate-500">{isMaker ? "Requires a different operator to approve." : "Switch the active role to Maker to stage."}</span>
          <Button variant="primary" onClick={stage} busy={busy} disabled={!isMaker || reason.trim().length < 5 || missingOverride}>
            Stage for approval
          </Button>
        </div>
      </div>
    </div>
  );
}

function PendingCard({ request, operator, onChanged }: { request: Remediation; operator: Operator; onChanged: () => void }) {
  const toast = useToast();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<"approve" | "decline" | null>(null);
  const isChecker = operator.role === "CHECKER";
  const sameOperator = operator.checkerId.trim().toLowerCase() === request.maker_id.toLowerCase();

  const decide = async (kind: "approve" | "decline") => {
    setBusy(kind);
    try {
      const fn = kind === "approve" ? api.approve : api.decline;
      const outcome = await fn(request.id, operator.checkerId, comment.trim() || null);
      toast.push({
        tone: outcome.status === "EXECUTED" ? "success" : outcome.status === "FAILED" ? "error" : "info",
        title: `Request #${request.id} ${outcome.status.replace(/_/g, " ").toLowerCase()}`,
        detail:
          outcome.status === "EXECUTED"
            ? (outcome.result?.responses ?? []).join(" · ") || "Injected into matching engine"
            : outcome.result?.issues?.map((i) => i.detail).join("; "),
      });
      onChanged();
    } catch (err) {
      toast.error(err, kind === "approve" ? "Approval rejected" : "Decline failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/[0.04]">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-500/20 px-3 py-2">
        <div className="flex items-center gap-2">
          <Badge value="PENDING_APPROVAL" />
          <span className="text-xs font-semibold text-slate-200">
            #{request.id} {request.action.replace(/_/g, " ")}
          </span>
        </div>
        <span className="text-[11px] text-slate-400">
          staged by <span className="font-mono text-slate-200">{request.maker_id}</span> · {fmtTime(request.created_at)}
        </span>
      </div>
      <div className="space-y-3 p-3">
        <p className="text-xs text-slate-300">“{request.reason}”</p>
        <StagedPayload request={request} />
        {request.bypass_risk_checks ? <div className="rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-300">Maker requested risk-check bypass</div> : null}
        <PreviewIssues issues={request.preview} />
        <textarea
          className="field h-14 w-full resize-none text-xs"
          placeholder="Checker comment (optional)"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          disabled={!isChecker}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className={`text-[11px] ${isChecker && sameOperator ? "text-rose-300" : "text-slate-500"}`}>
            {!isChecker
              ? "Switch the active role to Checker to decide."
              : sameOperator
                ? "Four-eyes: checker must differ from maker (the API rejects this with 403)."
                : `Deciding as ${operator.checkerId}`}
          </span>
          <div className="flex gap-2">
            <Button variant="danger" onClick={() => decide("decline")} busy={busy === "decline"} disabled={!isChecker}>
              Decline
            </Button>
            <Button variant="success" onClick={() => decide("approve")} busy={busy === "approve"} disabled={!isChecker}>
              Approve &amp; inject
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function StagedPayload({ request }: { request: Remediation }) {
  const overrideTags = useMemo(() => new Set(Object.keys(request.overrides)), [request.overrides]);
  const fields = request.staged_pipe.split("|").filter(Boolean);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
        <span>Staged payload</span>
        <span className="num normal-case tracking-normal text-slate-600" title={request.staged_payload_sha256}>
          sha256 {shortHash(request.staged_payload_sha256, 16)}
        </span>
      </div>
      <div className="break-all rounded bg-ink-950/70 px-2 py-1.5 font-mono text-[11px] leading-relaxed text-slate-400">
        {fields.map((f, i) => {
          const tag = f.split("=", 1)[0] ?? "";
          return (
            <span key={i} className={overrideTags.has(tag) ? "rounded bg-amber-500/20 text-amber-200" : ""}>
              {f}
              <span className="text-slate-700">|</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function PreviewIssues({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return <div className="text-[11px] text-emerald-400">Pre-trade checks pass against the live book.</div>;
  return (
    <ul className="space-y-1">
      {issues.map((i, idx) => (
        <li key={idx} className="rounded bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          <span className="font-mono">{i.code}</span> — {i.detail}
        </li>
      ))}
    </ul>
  );
}

function RequestCard({ request, compact = false }: { request: Remediation; compact?: boolean }) {
  return (
    <div className="rounded-md border border-line bg-ink-850 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={request.status} />
        <span className="font-semibold text-slate-200">
          #{request.id} {request.action.replace(/_/g, " ")}
        </span>
        <span className="text-slate-500">
          maker <span className="font-mono text-slate-300">{request.maker_id}</span>
          {request.checker_id ? (
            <>
              {" "}
              · checker <span className="font-mono text-slate-300">{request.checker_id}</span>
            </>
          ) : null}
        </span>
        <span className="ml-auto text-slate-600">{fmtTime(request.decided_at ?? request.created_at)}</span>
      </div>
      {!compact && request.result?.responses ? (
        <div className="mt-1.5 font-mono text-[11px] text-emerald-300">
          {request.result.responses.join(" · ")} {request.result_message_id ? <span className="text-slate-500">→ fix_messages.id {request.result_message_id}</span> : null}
        </div>
      ) : null}
      {request.result?.issues ? (
        <div className="mt-1.5 text-[11px] text-rose-300">{request.result.issues.map((i) => `${i.code}: ${i.detail}`).join("; ")}</div>
      ) : null}
      {request.checker_comment ? <div className="mt-1 text-[11px] text-slate-400">“{request.checker_comment}”</div> : null}
    </div>
  );
}
