import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { api } from "../api/client";
import type { DecodeResult, IngestResult } from "../api/types";
import { Badge, Button, Empty, useToast } from "./ui";
import { IssueList, MessageHeader, TagDecoderTable } from "./TagDecoderTable";

export function FixInspector() {
  const toast = useToast();
  const [text, setText] = useState("");
  const [result, setResult] = useState<DecodeResult | null>(null);
  const [selected, setSelected] = useState(0);
  const [filter, setFilter] = useState<string | null>(null);
  const [busy, setBusy] = useState<"sample" | "analyze" | "ingest" | null>(null);
  const [ingest, setIngest] = useState<IngestResult | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const analyze = async (payload: string) => {
    if (!payload.trim()) return;
    setBusy("analyze");
    try {
      const decoded = await api.decode(payload);
      setResult(decoded);
      setFilter(null);
      const firstProblem = decoded.messages.findIndex((m) => m.status !== "VALID");
      setSelected(firstProblem >= 0 ? firstProblem : 0);
    } catch (err) {
      toast.error(err, "Decode failed");
    } finally {
      setBusy(null);
    }
  };

  const loadSample = async () => {
    setBusy("sample");
    try {
      const sample = await api.sample();
      setText(sample.payload);
      setIngest(null);
      toast.push({ tone: "info", title: `Loaded ${sample.messages}-message institutional sample`, detail: sample.description });
      await analyze(sample.payload);
    } catch (err) {
      toast.error(err, "Sample unavailable");
    } finally {
      setBusy(null);
    }
  };

  const submit = async () => {
    if (!text.trim()) return;
    setBusy("ingest");
    try {
      const outcome = await api.ingest(text);
      setIngest(outcome);
      toast.push(
        outcome.replayed
          ? { tone: "info", title: "Idempotent replay", detail: "This exact payload was already ingested; the original response was returned and nothing was re-applied." }
          : { tone: "success", title: `Ingested ${outcome.received} messages`, detail: `${outcome.applied} applied · ${outcome.trades} prints · ${outcome.exceptions} exceptions queued` },
      );
    } catch (err) {
      toast.error(err, "Ingest failed");
    } finally {
      setBusy(null);
    }
  };

  const readFile = async (file: File) => {
    const content = await file.text();
    setText(content);
    setIngest(null);
    await analyze(content);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void readFile(file);
  };

  useEffect(() => {
    // Deep link: /#sample opens the inspector pre-loaded with the reference batch
    if (window.location.hash === "#sample") void loadSample();
  }, []);

  const visible = useMemo(() => {
    if (!result) return [];
    return result.messages.filter((m) => filter === null || m.status === filter);
  }, [result, filter]);
  const current = result?.messages[selected];

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="flex min-w-0 flex-col border-b border-line xl:border-b-0 xl:border-r">
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <Button variant="primary" onClick={loadSample} busy={busy === "sample"}>
            Load 25-msg sample batch
          </Button>
          <Button onClick={() => fileInput.current?.click()}>Open .fix / .log</Button>
          <input
            ref={fileInput}
            type="file"
            accept=".fix,.log,.txt,text/plain"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void readFile(file);
              e.target.value = "";
            }}
          />
          <Button onClick={() => analyze(text)} busy={busy === "analyze"} disabled={!text.trim()}>
            Analyze
          </Button>
          <Button variant="success" onClick={submit} busy={busy === "ingest"} disabled={!text.trim()} title="POST /api/fix/ingest with a content-hash Idempotency-Key">
            Ingest to engine
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setText("");
              setResult(null);
              setIngest(null);
            }}
            disabled={!text}
          >
            Clear
          </Button>
        </div>

        <div
          className="relative"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            placeholder={"Paste raw FIX (SOH, | or ^A delimited) or drop a .fix log file here.\n\n8=FIX.4.2|9=…|35=D|49=BUYSIDE|56=LOBX|34=12|…|10=123|"}
            className="block h-44 w-full resize-y border-0 bg-ink-950/60 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-slate-300 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-inset focus:ring-sky-500/40"
          />
          {dragging ? (
            <div className="pointer-events-none absolute inset-1 flex items-center justify-center rounded border-2 border-dashed border-sky-400/70 bg-sky-500/10 text-sm font-medium text-sky-200">
              Drop FIX log to decode
            </div>
          ) : null}
        </div>

        {ingest ? <IngestSummary result={ingest} /> : null}

        {result ? (
          <>
            <div className="flex flex-wrap items-center gap-1.5 border-y border-line px-3 py-2">
              <FilterChip label="All" count={result.messages.length} active={filter === null} onClick={() => setFilter(null)} />
              {Object.entries(result.counts)
                .sort(([a], [b]) => (a === "VALID" ? -1 : b === "VALID" ? 1 : a.localeCompare(b)))
                .map(([status, count]) => (
                  <FilterChip key={status} label={status} count={count} active={filter === status} onClick={() => setFilter(status)} tone={status !== "VALID"} />
                ))}
            </div>
            <div className="max-h-[360px] overflow-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className="th w-10">#</th>
                    <th className="th">Seq</th>
                    <th className="th">Sender</th>
                    <th className="th">MsgType</th>
                    <th className="th">ClOrdID</th>
                    <th className="th">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((m) => (
                    <tr
                      key={m.index}
                      onClick={() => setSelected(m.index)}
                      className={`cursor-pointer border-t border-line/60 ${m.index === selected ? "bg-sky-500/10" : m.status !== "VALID" ? "bg-rose-500/[0.04] hover:bg-rose-500/[0.08]" : "hover:bg-white/[0.03]"}`}
                    >
                      <td className="td num text-slate-600">{m.index + 1}</td>
                      <td className="td num text-slate-300">{m.decoded.msg_seq_num ?? "?"}</td>
                      <td className="td truncate text-slate-300">{m.decoded.sender_comp_id ?? "?"}</td>
                      <td className="td whitespace-nowrap font-mono text-slate-400">
                        {m.decoded.msg_type ?? "?"} <span className="text-slate-500">{m.decoded.msg_type_name ?? ""}</span>
                      </td>
                      <td className="td num whitespace-nowrap text-slate-400">{m.decoded.tags["11"] ?? ""}</td>
                      <td className="td">
                        <Badge value={m.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-col">
        {current ? (
          <>
            <MessageHeader decoded={current.decoded} status={current.status} />
            <IssueList issues={current.decoded.issues} />
            <div className="border-b border-line bg-ink-950/50 px-4 py-2 font-mono text-[11px] leading-relaxed text-slate-500 break-all">{current.raw_pipe}</div>
            <TagDecoderTable decoded={current.decoded} />
          </>
        ) : (
          <Empty>
            Load the sample batch or paste a FIX log, then select a message to see a tag-by-tag decode with framing, sequence and risk findings highlighted.
          </Empty>
        )}
      </div>
    </div>
  );
}

function FilterChip({ label, count, active, onClick, tone = false }: { label: string; count: number; active: boolean; onClick: () => void; tone?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ring-1 ring-inset transition-colors ${
        active ? "bg-sky-500/15 text-sky-200 ring-sky-500/40" : tone ? "text-rose-300 ring-rose-500/30 hover:bg-rose-500/10" : "text-slate-400 ring-line hover:bg-ink-800"
      }`}
    >
      {label.replace(/_/g, " ")} <span className="text-slate-500">{count}</span>
    </button>
  );
}

function IngestSummary({ result }: { result: IngestResult }) {
  return (
    <div className={`border-t border-line px-3 py-2 text-xs ${result.replayed ? "bg-sky-500/[0.06]" : "bg-emerald-500/[0.06]"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-semibold text-slate-200">{result.replayed ? "Idempotent replay — nothing re-applied" : "Ingested"}</span>
        <span className="num text-slate-400">received {result.received}</span>
        <span className="num text-emerald-300">applied {result.applied}</span>
        <span className="num text-sky-300">prints {result.trades}</span>
        <span className="num text-slate-300">exec reports {result.executions}</span>
        <span className={`num ${result.exceptions ? "text-rose-300" : "text-slate-400"}`}>exceptions {result.exceptions}</span>
      </div>
      <div className="mt-1 truncate font-mono text-[10px] text-slate-600" title={result.idempotency_key}>
        Idempotency-Key {result.idempotency_key}
      </div>
    </div>
  );
}
