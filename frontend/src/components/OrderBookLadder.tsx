import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { BookSnapshot, BookView, L3Order, Level, RebuildResult } from "../api/types";
import { fmtBps, fmtMid, fmtPx, fmtQty, fmtTime, pct, shortHash, vwapCents } from "../lib/format";
import { Button, Empty, Stat, useToast } from "./ui";

const DEPTH = 10;

interface Props {
  symbols: string[];
  symbol: string;
  onSymbol: (symbol: string) => void;
  book: BookSnapshot | undefined;
}

export function OrderBookLadder({ symbols, symbol, onSymbol, book }: Props) {
  const toast = useToast();
  const [showL3, setShowL3] = useState(false);
  const [l3, setL3] = useState<BookView | null>(null);
  const [rebuild, setRebuild] = useState<RebuildResult | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const checksum = book?.state_checksum;

  useEffect(() => {
    setRebuild(null);
  }, [symbol]);

  useEffect(() => {
    if (!showL3) return;
    let live = true;
    api
      .book(symbol, DEPTH)
      .then((view) => live && setL3(view))
      .catch((err) => toast.error(err, "L3 fetch failed"));
    return () => {
      live = false;
    };
  }, [showL3, symbol, checksum, toast]);

  const asks = useMemo(() => (book?.asks ?? []).slice(0, DEPTH), [book]);
  const bids = useMemo(() => (book?.bids ?? []).slice(0, DEPTH), [book]);
  const maxQty = Math.max(1, ...asks.map((l) => l.qty), ...bids.map((l) => l.qty));
  const askRows: (Level | null)[] = [...Array<null>(DEPTH - asks.length).fill(null), ...[...asks].reverse()];
  const bidRows: (Level | null)[] = [...bids, ...Array<null>(DEPTH - bids.length).fill(null)];
  const vwap = book ? vwapCents(book.notional_cents, book.volume) : null;

  const runRebuild = async () => {
    setRebuilding(true);
    try {
      const result = await api.rebuild(symbol);
      setRebuild(result);
      toast.push({
        tone: result.matches_live ? "success" : "error",
        title: result.matches_live ? `${symbol} rebuilt deterministically` : `${symbol} rebuild diverged from live book`,
        detail: `${result.messages_replayed} messages replayed in ${result.elapsed_ms} ms`,
      });
    } catch (err) {
      toast.error(err, "Rebuild failed");
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1 border-b border-line px-3 py-2">
        {symbols.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSymbol(s)}
            className={`rounded px-2.5 py-1 font-mono text-xs font-semibold transition-colors ${
              s === symbol ? "bg-sky-500/15 text-sky-300 ring-1 ring-inset ring-sky-500/40" : "text-slate-400 hover:bg-ink-800 hover:text-slate-200"
            }`}
          >
            {s}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <Button size="xs" variant={showL3 ? "primary" : "secondary"} onClick={() => setShowL3((v) => !v)}>
            L3 queue
          </Button>
          <Button size="xs" onClick={runRebuild} busy={rebuilding} title="Replay the append-only fix_messages log into a fresh engine and compare state checksums">
            Rebuild from log
          </Button>
        </div>
      </div>

      {!book ? (
        <Empty>Waiting for market data…</Empty>
      ) : (
        <>
          <div className="grid grid-cols-[3rem_1fr_6.5rem_1fr_3rem] border-b border-line px-3 py-1.5 text-[10px] uppercase tracking-wider text-slate-500">
            <span>Ord</span>
            <span className="text-right">Bid size</span>
            <span className="text-center">Price</span>
            <span>Ask size</span>
            <span className="text-right">Ord</span>
          </div>
          <div className="px-3 py-1">
            {askRows.map((level, i) => (
              <LadderRow key={`a-${level?.price_cents ?? `pad${i}`}`} level={level} side="ask" maxQty={maxQty} best={i === DEPTH - 1} />
            ))}
            <div className="my-1 flex items-center justify-between rounded border border-line bg-ink-850 px-3 py-1.5 text-xs">
              <span className="text-slate-500">
                Spread <span className="num text-slate-200">{book.spread_cents === null ? "—" : fmtPx(book.spread_cents)}</span>
              </span>
              <span className="num font-semibold text-amber-300">{fmtBps(book.spread_bps_e2)}</span>
              <span className="text-slate-500">
                Mid <span className="num text-slate-200">{fmtMid(book.best_bid_cents, book.best_ask_cents)}</span>
              </span>
            </div>
            {bidRows.map((level, i) => (
              <LadderRow key={`b-${level?.price_cents ?? `pad${i}`}`} level={level} side="bid" maxQty={maxQty} best={i === 0} />
            ))}
          </div>

          <div className="grid grid-cols-3 gap-x-4 gap-y-3 border-t border-line px-4 py-3 sm:grid-cols-6">
            <Stat label="Last" value={fmtPx(book.last_trade_px_cents)} tone="text-sky-300" />
            <Stat label="Last qty" value={fmtQty(book.last_trade_qty || null)} />
            <Stat label="Volume" value={fmtQty(book.volume)} />
            <Stat label="VWAP" value={fmtPx(vwap)} />
            <Stat label="Prints" value={fmtQty(book.trade_count)} />
            <Stat label="Resting" value={fmtQty(book.resting_orders)} />
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-2 text-[11px] text-slate-500">
            <span>
              State checksum <span className="num text-slate-300" title={book.state_checksum}>{shortHash(book.state_checksum, 16)}</span>
            </span>
            {rebuild && rebuild.symbol === symbol ? (
              <span className={rebuild.matches_live ? "text-emerald-400" : "text-rose-400"}>
                {rebuild.matches_live ? "✓ log replay matches live" : "✗ log replay diverged"} · {rebuild.messages_replayed} msgs · {rebuild.elapsed_ms} ms ·{" "}
                <span className="num" title={rebuild.checksum}>
                  {shortHash(rebuild.checksum, 10)}
                </span>
              </span>
            ) : null}
          </div>

          {showL3 ? <L3Queue view={l3} /> : null}
        </>
      )}
    </div>
  );
}

function LadderRow({ level, side, maxQty, best }: { level: Level | null; side: "bid" | "ask"; maxQty: number; best: boolean }) {
  if (!level) {
    return <div className="grid h-[22px] grid-cols-[3rem_1fr_6.5rem_1fr_3rem]" aria-hidden />;
  }
  const width = `${Math.max(2, pct(level.qty, maxQty))}%`;
  const isBid = side === "bid";
  return (
    <div className={`grid h-[22px] grid-cols-[3rem_1fr_6.5rem_1fr_3rem] items-center text-xs ${best ? "font-semibold" : ""}`}>
      <span className="num text-slate-500">{isBid ? level.orders : ""}</span>
      <div className="relative h-full">
        {isBid ? (
          <>
            <div className="absolute inset-y-[3px] right-0 rounded-l-sm bg-emerald-500/20" style={{ width }} />
            <span className="num relative flex h-full items-center justify-end pr-2 text-emerald-300">{fmtQty(level.qty)}</span>
          </>
        ) : null}
      </div>
      <span className={`num text-center ${isBid ? "text-emerald-400" : "text-rose-400"} ${best ? "rounded bg-white/[0.04]" : ""}`}>{fmtPx(level.price_cents)}</span>
      <div className="relative h-full">
        {!isBid ? (
          <>
            <div className="absolute inset-y-[3px] left-0 rounded-r-sm bg-rose-500/20" style={{ width }} />
            <span className="num relative flex h-full items-center pl-2 text-rose-300">{fmtQty(level.qty)}</span>
          </>
        ) : null}
      </div>
      <span className="num text-right text-slate-500">{isBid ? "" : level.orders}</span>
    </div>
  );
}

function L3Queue({ view }: { view: BookView | null }) {
  if (!view) return <Empty>Loading resting orders…</Empty>;
  return (
    <div className="grid max-h-72 grid-cols-1 gap-px overflow-auto border-t border-line bg-line md:grid-cols-2">
      <L3Side title="Bids · price-time queue" orders={view.l3.bids} tone="text-emerald-400" />
      <L3Side title="Asks · price-time queue" orders={view.l3.asks} tone="text-rose-400" />
    </div>
  );
}

function L3Side({ title, orders, tone }: { title: string; orders: L3Order[]; tone: string }) {
  return (
    <div className="bg-ink-900">
      <div className="sticky top-0 bg-ink-850 px-3 py-1.5 text-[10px] uppercase tracking-wider text-slate-500">{title}</div>
      {orders.length === 0 ? (
        <div className="px-3 py-3 text-xs text-slate-600">No resting orders</div>
      ) : (
        <table className="w-full text-[11px]">
          <tbody>
            {orders.map((o) => (
              <tr key={o.order_id} className="border-t border-line/60">
                <td className={`num px-3 py-1 ${tone}`}>{fmtPx(o.price_cents)}</td>
                <td className="num px-1 py-1 text-slate-500">#{o.queue_position}</td>
                <td className="num px-1 py-1 text-right text-slate-200">{fmtQty(o.leaves_qty)}</td>
                <td className="truncate px-2 py-1 text-slate-400" title={`${o.order_id} · ClOrdID ${o.cl_ord_id}`}>
                  {o.client_comp_id}
                </td>
                <td className="num px-3 py-1 text-right text-slate-600">{fmtTime(o.transact_time)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
