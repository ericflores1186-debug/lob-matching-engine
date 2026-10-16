import { useEffect, useReducer } from "react";
import type { BookSnapshot, CancelRejectEvent, Execution, StreamMessage, Trade } from "../api/types";

export type ConnectionStatus = "connecting" | "open" | "closed";

const MAX_ROWS = 400;

export interface MarketState {
  status: ConnectionStatus;
  snapshotSeq: number;
  seq: number;
  books: Record<string, BookSnapshot>;
  bookSeq: Record<string, number>;
  trades: Trade[];
  executions: Execution[];
  exceptionsVersion: number;
  lastEventAt: number | null;
}

type Action = { type: "status"; status: ConnectionStatus } | { type: "message"; message: StreamMessage; at: number };

const initial: MarketState = {
  status: "connecting",
  snapshotSeq: 0,
  seq: 0,
  books: {},
  bookSeq: {},
  trades: [],
  executions: [],
  exceptionsVersion: 0,
  lastEventAt: null,
};

function cancelRejectRow(event: CancelRejectEvent): Execution {
  return {
    exec_id: `CXLREJ-${event.log_id}`,
    exec_type: "CANCEL_REJECT",
    ord_status: event.ord_status,
    order_id: event.order_id,
    cl_ord_id: event.cl_ord_id,
    orig_cl_ord_id: event.orig_cl_ord_id,
    client_comp_id: event.client_comp_id,
    symbol: event.symbol,
    side: "",
    price_cents: null,
    order_qty: 0,
    leaves_qty: 0,
    cum_qty: 0,
    cum_notional_cents: 0,
    avg_px: "0",
    last_qty: 0,
    last_px_cents: null,
    ord_rej_reason: event.reason,
    text: event.text,
    transact_time: event.transact_time,
  };
}

function prependUnique<T>(rows: T[], incoming: T[], key: (row: T) => string): T[] {
  if (incoming.length === 0) return rows;
  const seen = new Set(rows.map(key));
  const fresh = incoming.filter((row) => {
    const k = key(row);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  return [...fresh.reverse(), ...rows].slice(0, MAX_ROWS);
}

function reducer(state: MarketState, action: Action): MarketState {
  if (action.type === "status") return { ...state, status: action.status };
  const message = action.message;

  if (message.type === "snapshot") {
    const books: Record<string, BookSnapshot> = {};
    const bookSeq: Record<string, number> = {};
    for (const book of message.books) {
      books[book.symbol] = book;
      bookSeq[book.symbol] = message.seq;
    }
    return {
      ...state,
      snapshotSeq: message.seq,
      seq: message.seq,
      books,
      bookSeq,
      trades: [...message.trades].reverse().slice(0, MAX_ROWS),
      executions: [...message.executions].reverse().slice(0, MAX_ROWS),
      exceptionsVersion: state.exceptionsVersion + 1,
      lastEventAt: action.at,
    };
  }

  if (message.type !== "batch" || message.seq <= state.snapshotSeq) return state;

  // Batches can arrive out of order across concurrent requests: books keep the newest seq,
  // trades and executions are de-duplicated by id
  const books = { ...state.books };
  const bookSeq = { ...state.bookSeq };
  const trades: Trade[] = [];
  const executions: Execution[] = [];
  let exceptionsVersion = state.exceptionsVersion;
  for (const event of message.events) {
    switch (event.type) {
      case "trade":
        trades.push(event.trade);
        break;
      case "execution":
        executions.push(event.execution);
        break;
      case "cancel_reject":
        executions.push(cancelRejectRow(event.cancel_reject));
        break;
      case "book":
        if (message.seq >= (bookSeq[event.book.symbol] ?? 0)) {
          books[event.book.symbol] = event.book;
          bookSeq[event.book.symbol] = message.seq;
        }
        break;
      case "exception":
      case "remediation":
        exceptionsVersion += 1;
        break;
      case "notice":
        break;
    }
  }
  return {
    ...state,
    seq: Math.max(state.seq, message.seq),
    books,
    bookSeq,
    trades: prependUnique(state.trades, trades, (t) => t.match_id),
    executions: prependUnique(state.executions, executions, (e) => e.exec_id),
    exceptionsVersion,
    lastEventAt: action.at,
  };
}

function streamUrl(): string {
  const base = import.meta.env.VITE_API_BASE;
  if (base) return base.replace(/^http/, "ws") + "/ws/market-data";
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws/market-data`;
}

export function useMarketData(): MarketState {
  const [state, dispatch] = useReducer(reducer, initial);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let attempts = 0;
    let stopped = false;
    let heartbeat: number | undefined;
    let reconnect: number | undefined;

    const connect = () => {
      dispatch({ type: "status", status: "connecting" });
      socket = new WebSocket(streamUrl());
      socket.onopen = () => {
        attempts = 0;
        dispatch({ type: "status", status: "open" });
        heartbeat = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
        }, 20_000);
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        dispatch({ type: "message", message: JSON.parse(event.data) as StreamMessage, at: Date.now() });
      };
      socket.onclose = () => {
        window.clearInterval(heartbeat);
        dispatch({ type: "status", status: "closed" });
        if (!stopped) {
          const delay = Math.min(10_000, 500 * 2 ** attempts);
          attempts += 1;
          reconnect = window.setTimeout(connect, delay);
        }
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      window.clearInterval(heartbeat);
      window.clearTimeout(reconnect);
      socket?.close();
    };
  }, []);

  return state;
}
