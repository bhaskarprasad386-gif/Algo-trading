{orderedRows.map((tracked) => (
            <OpportunityCard key={tracked.key} tracked={tracked} expiryView={expiryView} />
          ))type OpportunityCardProps = { tracked: TrackedRow; expiryView: "MONTHLY" | "WEEKLY" };
const OpportunityCard = memo(function OpportunityCard({ tracked, expiryView }: OpportunityCardProps) {
  const { key, row, lastSeen, status } = tracked;
  return (
  <article className={"overflow-hidden rounded-xl border " + (status === "LIVE" ? "theme-border theme-surface-2" : "border-amber-500/20 theme-surface-2 opacity-75")}>
                <div className="flex flex-wrap items-center justify-between gap-3 border-b theme-border px-3 py-3 sm:px-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-extrabold tracking-wide theme-text">{String(row.symbol || "—")}</span>
                    <span className={"rounded-md px-2 py-1 text-[9px] font-extrabold " + (expiryView === "WEEKLY" ? "bg-cyan-400/10 text-cyan-200" : "bg-violet-400/10 text-violet-200")}>{expiryView}</span>
                    <span className="text-[11px] theme-muted">Expiry {String(row.expiry_date || row.expiry || "—")}</span>
                    <span className="inline-flex flex-wrap items-center gap-1.5 text-[11px]">
                      <span className="theme-text">Spot ₹{fmt(row.spot_price)}</span>
                      {(() => { const spot = spotFeedStatus(row, lastSeen); return <><span className={"rounded px-1.5 py-0.5 text-[9px] font-bold " + (spot.live ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-200")}>{spot.label}</span>{spot.age !== null && <span className="theme-muted">{fmt(spot.age, 2)}s</span>}</>; })()}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={"rounded-md px-2 py-1 text-[9px] font-bold " + (status === "LIVE" ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-200")}>{status}</span>
                    <div className="flex flex-col items-end gap-0.5 text-[9px] tabular-nums theme-muted">
                      <span>Quote {quoteTime(row.last_update_timestamp_ns)}</span>
                      <span>4-leg skew {(() => { const skew = n(row, "timestamp_skew_ms"); return skew === null || skew < 0 ? "—" : fmt(skew, 3) + " ms"; })()}</span>
                      <span>Received {timeFmt(lastSeen)} IST</span>
                      <span className={status === "LIVE" ? "text-emerald-400" : "text-amber-300"}>{status === "LIVE" ? "Updated" : "No update"} {Math.max(0, (Date.now() - lastSeen) / 1000).toFixed(1)}s ago</span>
                    </div>
                  </div>
                </div>
                <div className="grid gap-3 p-3 sm:p-4 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
                  <div>
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] theme-muted">
                      <span>Strikes <b className="theme-text">{fmt(row.low_strike, 0)} / {fmt(row.high_strike, 0)}</b></span>
                      <span className="theme-muted">•</span><span>Direction <b className="theme-text">{String(row.direction || "—")}</b></span>
                      <span className="theme-muted">•</span><span>Lot size <b className="theme-text">{fmt(row.lot_size, 0)}</b></span>
                    </div>
                    <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                      {String(row.direction || "LONG").toUpperCase() === "SHORT" ? (
                        <>
                          <LegQuote row={row} name="LOW CE" depthKey="low_call" price="low_call_bid" qty="low_call_bid_qty" side="SELL"/>
                          <LegQuote row={row} name="LOW PE" depthKey="low_put" price="low_put_ask" qty="low_put_ask_qty" side="BUY"/>
                          <LegQuote row={row} name="HIGH CE" depthKey="high_call" price="high_call_ask" qty="high_call_ask_qty" side="BUY"/>
                          <LegQuote row={row} name="HIGH PE" depthKey="high_put" price="high_put_bid" qty="high_put_bid_qty" side="SELL"/>
                        </>
                      ) : (
                        <>
                          <LegQuote row={row} name="LOW CE" depthKey="low_call" price="low_call_ask" qty="low_call_ask_qty" side="BUY"/>
                          <LegQuote row={row} name="LOW PE" depthKey="low_put" price="low_put_bid" qty="low_put_bid_qty" side="SELL"/>
                          <LegQuote row={row} name="HIGH CE" depthKey="high_call" price="high_call_bid" qty="high_call_bid_qty" side="SELL"/>
                          <LegQuote row={row} name="HIGH PE" depthKey="high_put" price="high_put_ask" qty="high_put_ask_qty" side="BUY"/>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2 xl:min-w-[230px] xl:grid-cols-1">
                    <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.07] p-3">
                      <p className="text-[9px] font-bold uppercase tracking-wider text-emerald-200/70">Executable edge</p>
                      <p className="mt-1 text-xl font-extrabold tabular-nums text-emerald-300">₹{fmt(row.executable_edge)}</p>
                    </div>
                    <div className="rounded-xl border theme-border theme-surface-2 p-3">
                      <p className="text-[9px] font-bold uppercase tracking-wider theme-muted">Edge / lot · Gross P&amp;L</p>
                      <p className="mt-1 text-sm font-bold tabular-nums theme-text">₹{fmt(row.edge_per_lot)} <span className="theme-muted">·</span> ₹{fmt(row.gross_pnl)}</p>
                    </div>
                    <div className="rounded-xl border theme-border theme-surface-2 p-3">
                      <p className="text-[9px] font-bold uppercase tracking-wider theme-muted">Max Executable Lots</p>
                      <p className="mt-1 text-lg font-extrabold tabular-nums theme-text">{(() => { const lots = n(row, "max_executable_lots"); return lots === null || lots < 0 || !Number.isInteger(lots) ? "—" : lots.toLocaleString("en-IN") + " lots"; })()}</p>
                      <p className="mt-1 text-[9px] theme-muted">Common four-leg depth capacity · not a fill guarantee</p>
                    </div>
                  </div>
                </div>
              </article>
  );
});


}