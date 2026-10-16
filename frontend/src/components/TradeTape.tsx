import { useMemo, useState } from "react";
import type { Execution, Trade } from "../api/types";
import { fmtCompactUsd, fmtPx, fmtQty, fmtTime } from "../lib/format";
import { Badge, Empty, SideTag } from "./ui";

interface Props {
  trades: Trade[];
  executions: Execution[];
  symbol: string;
}

export function TradeTape({ trades, executions, symbol }: Props) {
  const [tab, setTab] = useState<"prints" | "execs">("prints");
  const [allSymbols, setAllSymbols] = useState(true);
  const prints = useMemo(() => (allSymbols ? trades : trades.filter((t) => t.symbol === symbol)), [trades, allSymbols, symbol]);
  const reports = useMemo(() => (allSymbols ? executions : executions.filter((e) => e.symbol === symbol)), [executions, allSymbols, symbol]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-line px-3 py-2">
        <TabButton active={tab === "prints"} onClick={() => setTab("prints")} label="Prints" count={prints.length} />
        <TabButton active={tab === "execs"} onClick={() => setTab("execs")} label="Execution reports" count={reports.length} />
        <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" className="accent-sky-500" checked={!allSymbols} onChange={(e) => setAllSymbols(!e.target.checked)} />
          {symbol} only
        </label>
      </div>
      <div className="h-[488px] overflow-auto">
        {tab === "prints" ? <Prints trades={prints} /> : <Reports executions={reports} />}
      </div>
    </div>
  );
}

function TabButton({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${active ? "bg-ink-700 text-slate-100" : "text-slate-400 hover:text-slate-200"}`}
    >
      {label} <span className="num ml-1 text-slate-500">{count}</span>
    </button>
  );
}

function Prints({ trades }: { trades: Trade[] }) {
  if (trades.length === 0) return <Empty>No prints yet. Ingest the sample batch from the FIX inspector below.</Empty>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr>
          <th className="th">Time (UTC)</th>
          <th className="th">Symbol</th>
          <th className="th">Aggressor</th>
          <th className="th text-right">Qty</th>
          <th className="th text-right">Price</th>
          <th className="th text-right">Notional</th>
          <th className="th">Buyer → Seller</th>
          <th className="th">Match ID</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t) => (
          <tr key={t.match_id} className="animate-flash border-t border-line/60 hover:bg-white/[0.02]">
            <td className="td num text-slate-500">{fmtTime(t.transact_time)}</td>
            <td className="td font-mono font-semibold text-slate-200">{t.symbol}</td>
            <td className="td">
              <SideTag side={t.aggressor_side} />
            </td>
            <td className="td num text-right text-slate-200">{fmtQty(t.qty)}</td>
            <td className={`td num text-right font-semibold ${t.aggressor_side === "BUY" ? "text-emerald-400" : "text-rose-400"}`}>{fmtPx(t.price_cents)}</td>
            <td className="td num text-right text-slate-400">{fmtCompactUsd(t.notional_cents)}</td>
            <td className="td truncate text-slate-400">
              {t.buy_comp_id} <span className="text-slate-600">→</span> {t.sell_comp_id}
            </td>
            <td className="td num text-slate-600">{t.match_id}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Reports({ executions }: { executions: Execution[] }) {
  if (executions.length === 0) return <Empty>No execution reports yet.</Empty>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr>
          <th className="th">Time (UTC)</th>
          <th className="th">Client</th>
          <th className="th">ClOrdID</th>
          <th className="th">Sym</th>
          <th className="th">Side</th>
          <th className="th">ExecType</th>
          <th className="th">OrdStatus</th>
          <th className="th text-right">Last</th>
          <th className="th text-right">Cum / Qty</th>
          <th className="th text-right">AvgPx</th>
        </tr>
      </thead>
      <tbody>
        {executions.map((e) => (
          <tr key={e.exec_id} className="animate-flash border-t border-line/60 hover:bg-white/[0.02]" title={e.text ?? undefined}>
            <td className="td num text-slate-500">{fmtTime(e.transact_time)}</td>
            <td className="td truncate text-slate-300">{e.client_comp_id}</td>
            <td className="td num text-slate-400">{e.cl_ord_id}</td>
            <td className="td font-mono font-semibold text-slate-200">{e.symbol}</td>
            <td className="td">
              <SideTag side={e.side} />
            </td>
            <td className="td">
              <Badge value={e.exec_type} />
            </td>
            <td className="td">
              <Badge value={e.ord_status} />
            </td>
            <td className="td num text-right text-slate-200">{e.last_qty ? `${fmtQty(e.last_qty)} @ ${fmtPx(e.last_px_cents)}` : <span className="text-slate-600">—</span>}</td>
            <td className="td num text-right text-slate-400">
              {fmtQty(e.cum_qty)} / {fmtQty(e.order_qty)}
            </td>
            <td className="td num text-right text-slate-400">{e.cum_qty ? e.avg_px : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
