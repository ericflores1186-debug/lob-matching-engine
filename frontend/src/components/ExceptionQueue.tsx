import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ExceptionItem, MessageDetail, Remediation } from "../api/types";
import type { Operator } from "../hooks/useOperator";
import { fmtTime } from "../lib/format";
import { RemediationPanel } from "./RemediationPanel";
import { IssueList, MessageHeader, TagDecoderTable } from "./TagDecoderTable";
import { Badge, Button, Empty, useToast } from "./ui";

interface Props {
  operator: Operator;
  refreshKey: number;
}

export function ExceptionQueue({ operator, refreshKey }: Props) {
  const toast = useToast();
  const [scope, setScope] = useState<"open" | "all">("open");
  const [items, setItems] = useState<ExceptionItem[] | null>(null);
  const [requests, setRequests] = useState<Remediation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<MessageDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [localVersion, setLocalVersion] = useState(0);

  const reload = useCallback(() => setLocalVersion((v) => v + 1), []);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([api.exceptions(scope), api.remediations()])
      .then(([exceptions, remediations]) => {
        if (!live) return;
        setItems(exceptions);
        setRequests(remediations);
        setSelectedId((current) => (current !== null && exceptions.some((e) => e.id === current) ? current : exceptions[0]?.id ?? null));
      })
      .catch((err) => toast.error(err, "Exception queue unavailable"))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [scope, refreshKey, localVersion, toast]);

  useEffect(() => {
    if (selectedId === null) {
      setDetail(null);
      return;
    }
    let live = true;
    api
      .message(selectedId)
      .then((d) => live && setDetail(d))
      .catch((err) => toast.error(err, "Message detail unavailable"));
    return () => {
      live = false;
    };
  }, [selectedId, toast]);

  const selected = items?.find((e) => e.id === selectedId) ?? null;
  const selectedRequests = useMemo(() => requests.filter((r) => r.exception_message_id === selectedId), [requests, selectedId]);
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const item of items ?? []) out[item.status] = (out[item.status] ?? 0) + 1;
    return out;
  }, [items]);
  const pendingCount = requests.filter((r) => r.status === "PENDING_APPROVAL").length;

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <div className="flex rounded-md bg-ink-950 p-0.5 ring-1 ring-inset ring-line">
          {(["open", "all"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setScope(s)}
              className={`rounded px-2.5 py-1 text-xs font-medium ${scope === s ? "bg-ink-700 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}
            >
              {s === "open" ? "Open" : "All"}
            </button>
          ))}
        </div>
        {Object.entries(counts).map(([status, n]) => (
          <span key={status} className="flex items-center gap-1">
            <Badge value={status} /> <span className="num text-xs text-slate-500">{n}</span>
          </span>
        ))}
        <span className="ml-auto text-xs text-slate-500">
          <span className="num text-amber-300">{pendingCount}</span> awaiting checker
        </span>
        <Button size="xs" onClick={reload} busy={loading}>
          Refresh
        </Button>
      </div>

      <div className="max-h-[300px] overflow-auto">
        {items === null ? (
          <Empty>Loading…</Empty>
        ) : items.length === 0 ? (
          <Empty>{scope === "open" ? "No open exceptions. Rejected, gapped and fat-finger messages land here for maker-checker review." : "No exceptions recorded."}</Empty>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className="th">ID</th>
                <th className="th">Received</th>
                <th className="th">Sender</th>
                <th className="th">Seq</th>
                <th className="th">Type</th>
                <th className="th">ClOrdID</th>
                <th className="th">Sym</th>
                <th className="th">Status</th>
                <th className="th">Root cause</th>
                <th className="th">Remediation</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => setSelectedId(item.id)}
                  className={`cursor-pointer border-t border-line/60 ${item.id === selectedId ? "bg-sky-500/10" : "hover:bg-white/[0.03]"}`}
                >
                  <td className="td num text-slate-500">{item.id}</td>
                  <td className="td num text-slate-500">{fmtTime(item.received_at)}</td>
                  <td className="td truncate text-slate-300">{item.sender_comp_id ?? "?"}</td>
                  <td className="td num text-slate-300">{item.msg_seq_num ?? "?"}</td>
                  <td className="td font-mono text-slate-400">{item.msg_type ?? "?"}</td>
                  <td className="td num whitespace-nowrap text-slate-400">{item.cl_ord_id ?? ""}</td>
                  <td className="td font-mono text-slate-300">{item.symbol ?? ""}</td>
                  <td className="td">
                    <Badge value={item.status} />
                  </td>
                  <td className="td max-w-[420px] truncate text-slate-400" title={item.root_cause ?? undefined}>
                    {item.root_cause}
                  </td>
                  <td className="td">
                    {item.remediation ? <Badge value={item.remediation.status} /> : <span className="text-slate-600">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selected ? (
        <div className="grid grid-cols-1 border-t border-line xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <div className="min-w-0 border-b border-line xl:border-b-0 xl:border-r">
            {detail && detail.id === selected.id ? (
              <>
                <MessageHeader decoded={detail.decoded} status={detail.status} />
                <IssueList issues={detail.decoded.issues} />
                <TagDecoderTable decoded={detail.decoded} maxHeight="max-h-[420px]" />
              </>
            ) : (
              <Empty>Loading message…</Empty>
            )}
          </div>
          <RemediationPanel key={selected.id} exception={selected} requests={selectedRequests} operator={operator} onChanged={reload} />
        </div>
      ) : null}
    </div>
  );
}
