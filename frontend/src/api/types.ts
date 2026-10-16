export type Side = "BUY" | "SELL";
export type Severity = "ERROR" | "WARN" | "INFO";
export type RemediationAction = "REPLAY" | "AMEND_AND_REPLAY" | "CANCEL_REPLACE";
export type RemediationStatus = "PENDING_APPROVAL" | "EXECUTED" | "DECLINED" | "FAILED";
export type Role = "MAKER" | "CHECKER";

export interface Level {
  price_cents: number;
  qty: number;
  orders: number;
}

export interface BookSnapshot {
  symbol: string;
  bids: Level[];
  asks: Level[];
  best_bid_cents: number | null;
  best_ask_cents: number | null;
  spread_cents: number | null;
  spread_bps_e2: number | null;
  last_trade_px_cents: number | null;
  last_trade_qty: number;
  volume: number;
  notional_cents: number;
  trade_count: number;
  resting_orders: number;
  state_checksum: string;
}

export interface L3Order {
  order_id: string;
  cl_ord_id: string;
  client_comp_id: string;
  side: Side;
  price_cents: number;
  order_qty: number;
  leaves_qty: number;
  cum_qty: number;
  time_in_force: string;
  priority: number;
  queue_position: number;
  transact_time: string;
}

export interface BookView extends BookSnapshot {
  l3: { bids: L3Order[]; asks: L3Order[] };
  reference_price_cents: number | null;
}

export interface Trade {
  match_id: string;
  symbol: string;
  price_cents: number;
  qty: number;
  notional_cents: number;
  aggressor_side: Side;
  buy_order_id: string;
  sell_order_id: string;
  buy_comp_id: string;
  sell_comp_id: string;
  transact_time: string;
}

export interface Execution {
  exec_id: string;
  exec_type: string;
  ord_status: string;
  order_id: string;
  cl_ord_id: string;
  orig_cl_ord_id: string | null;
  client_comp_id: string;
  symbol: string;
  side: string;
  price_cents: number | null;
  order_qty: number;
  leaves_qty: number;
  cum_qty: number;
  cum_notional_cents: number;
  avg_px: string;
  last_qty: number;
  last_px_cents: number | null;
  ord_rej_reason: string | null;
  text: string | null;
  transact_time: string;
}

export interface CancelRejectEvent {
  order_id: string;
  cl_ord_id: string;
  orig_cl_ord_id: string;
  client_comp_id: string;
  symbol: string;
  ord_status: string;
  reason: string;
  text: string;
  transact_time: string;
  log_id: number;
}

export interface Issue {
  code: string;
  severity: Severity;
  status: string;
  tag: number | null;
  detail: string;
}

export interface DecodedField {
  index: number;
  tag: number | null;
  name: string;
  value: string;
  meaning: string | null;
  issues: string[];
}

export interface Decoded {
  begin_string: string | null;
  msg_type: string | null;
  msg_type_name: string | null;
  delimiter: string;
  sender_comp_id: string | null;
  target_comp_id: string | null;
  msg_seq_num: number | null;
  sending_time: string | null;
  body_length: { declared: number | null; computed: number | null };
  checksum: { declared: string | null; computed: string | null };
  fields: DecodedField[];
  tags: Record<string, string>;
  decoded: Record<string, unknown>;
  issues: Issue[];
}

export interface DecodedMessage {
  index: number;
  status: string;
  root_cause: string | null;
  raw_pipe: string;
  decoded: Decoded;
}

export interface DecodeResult {
  messages: DecodedMessage[];
  counts: Record<string, number>;
}

export interface IngestedMessage {
  id: number;
  msg_seq_num: number | null;
  sender_comp_id: string | null;
  msg_type: string | null;
  msg_type_name: string | null;
  cl_ord_id: string | null;
  symbol: string | null;
  status: string;
  applied: boolean;
  issues: Issue[];
  responses: string[];
}

export interface IngestResult {
  idempotency_key: string;
  replayed: boolean;
  received: number;
  applied: number;
  exceptions: number;
  executions: number;
  trades: number;
  messages: IngestedMessage[];
}

export interface MessageSummary {
  id: number;
  received_at: string | null;
  direction: "INBOUND" | "OUTBOUND";
  source: string;
  msg_type: string | null;
  msg_type_name: string | null;
  sender_comp_id: string | null;
  target_comp_id: string | null;
  msg_seq_num: number | null;
  cl_ord_id: string | null;
  symbol: string | null;
  status: string;
  engine_applied: boolean;
  replay_of_id: number | null;
  raw_pipe: string;
}

export interface MessageDetail extends MessageSummary {
  decoded: Decoded;
  issues: Issue[];
}

export interface ExceptionItem extends MessageSummary {
  sending_time: string | null;
  root_cause: string | null;
  issues: Issue[];
  allowed_actions: RemediationAction[];
  resolved: boolean;
  remediation: { id: number; status: RemediationStatus; action: RemediationAction; maker_id: string; checker_id: string | null } | null;
}

export interface Remediation {
  id: number;
  exception_message_id: number;
  action: RemediationAction;
  status: RemediationStatus;
  staged_payload: string;
  staged_pipe: string;
  staged_payload_sha256: string;
  overrides: Record<string, string>;
  bypass_risk_checks: boolean;
  reason: string;
  maker_id: string;
  checker_id: string | null;
  checker_comment: string | null;
  created_at: string | null;
  decided_at: string | null;
  result_message_id: number | null;
  result: { responses?: string[]; executions?: number; trades?: number; issues?: Issue[] } | null;
  preview: Issue[];
}

export interface RebuildResult {
  symbol: string;
  messages_replayed: number;
  checksum: string;
  live_checksum: string;
  matches_live: boolean;
  elapsed_ms: number;
  book: BookView;
}

export interface SymbolSummary {
  symbol: string;
  best_bid_cents: number | null;
  best_ask_cents: number | null;
  spread_cents: number | null;
  spread_bps_e2: number | null;
  last_trade_px_cents: number | null;
  reference_price_cents: number | null;
  volume: number;
  trade_count: number;
  resting_orders: number;
}

export interface SamplePayload {
  payload: string;
  messages: number;
  description: string;
}

export type StreamEvent =
  | { type: "execution"; execution: Execution }
  | { type: "trade"; trade: Trade }
  | { type: "book"; book: BookSnapshot }
  | { type: "cancel_reject"; cancel_reject: CancelRejectEvent }
  | { type: "exception"; message: ExceptionItem }
  | { type: "remediation"; remediation: Remediation }
  | { type: "notice"; text: string };

export type StreamMessage =
  | { type: "snapshot"; seq: number; books: BookSnapshot[]; trades: Trade[]; executions: Execution[] }
  | { type: "batch"; seq: number; events: StreamEvent[] }
  | { type: "pong" };
