import { useEffect, useMemo, useState } from "react";
import { ExceptionQueue } from "./components/ExceptionQueue";
import { FixInspector } from "./components/FixInspector";
import { Header } from "./components/Header";
import { OrderBookLadder } from "./components/OrderBookLadder";
import { TradeTape } from "./components/TradeTape";
import { Panel } from "./components/ui";
import { useMarketData } from "./hooks/useMarketData";
import { useOperator } from "./hooks/useOperator";
import { fmtBps, fmtPx } from "./lib/format";

export default function App() {
  const market = useMarketData();
  const [operator, setOperator] = useOperator();
  const symbols = useMemo(() => Object.keys(market.books).sort(), [market.books]);
  const [symbol, setSymbol] = useState("AAPL");
  const book = market.books[symbol];

  useEffect(() => {
    if (symbols.length > 0 && !symbols.includes(symbol)) setSymbol(symbols[0] ?? "AAPL");
  }, [symbols, symbol]);

  return (
    <div className="min-h-screen">
      <Header status={market.status} seq={market.seq} operator={operator} onOperator={setOperator} />
      <main className="mx-auto grid max-w-[1680px] grid-cols-12 gap-4 px-4 py-4">
        <Panel
          className="col-span-12 xl:col-span-5"
          title="L2 order book ladder"
          subtitle={
            book ? (
              <span className="num">
                {symbol} · bid {fmtPx(book.best_bid_cents)} / ask {fmtPx(book.best_ask_cents)} · spread {fmtBps(book.spread_bps_e2)}
              </span>
            ) : (
              "Aggregated depth by price level"
            )
          }
        >
          <OrderBookLadder symbols={symbols} symbol={symbol} onSymbol={setSymbol} book={book} />
        </Panel>
        <Panel className="col-span-12 xl:col-span-7" title="Trade tape & execution blotter" subtitle="35=8 ExecutionReports and prints streamed over /ws/market-data">
          <TradeTape trades={market.trades} executions={market.executions} symbol={symbol} />
        </Panel>
        <Panel
          className="col-span-12"
          title="FIX log inspector & tag decoder"
          subtitle="Dry-run framing, sequence, idempotency and price-collar validation before anything touches the book"
        >
          <FixInspector />
        </Panel>
        <Panel
          className="col-span-12"
          title="Exception & maker-checker replay queue"
          subtitle="Stage as Maker, switch role, approve as Checker — the API enforces maker ≠ checker and verifies the staged payload hash"
        >
          <ExceptionQueue operator={operator} refreshKey={market.exceptionsVersion} />
        </Panel>
      </main>
    </div>
  );
}
