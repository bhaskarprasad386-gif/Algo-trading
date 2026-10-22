"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowLeft, Bell, Filter, RefreshCw, Search, ShieldCheck, Zap } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

export type WorkspaceConfig = {
  slug: string;
  title: string;
  description: string;
  universe: string;
  columns: string[];
  metrics: string[];
  controls: string[];
  pairTitle: string;
  pairColumns: string[];
  historyEndpoint: string;
  pairEndpoint?: string;
};

const configs: Record<string, WorkspaceConfig> = {
  "calendar-spread": {
    slug: "calendar-spread",
    title: "Calendar Spread",
    description: "Dedicated near-vs-far expiry spread scanner using the common live market-data feed.",
    universe: "F&O + INDEX + COMMODITIES",
    columns: ["Time", "Underlying", "Exchange", "Near", "Far", "Spread", "Gap / Edge", "Volume / OI", "Signal"],
    metrics: ["Eligible Contracts", "Spread Opportunities", "Signals", "Last Scan"],
    controls: ["All Eligible", "INDEX PRIORITY", "Near/Far", "Edge Threshold"],
    pairTitle: "Near / Far Pair Monitor",
    pairColumns: ["Time", "Underlying", "Exchange", "Near", "Far", "Spread", "Edge", "Liquidity", "State"],
    historyEndpoint: "/api/v1/scanner/calendar-spread/history?days=1&limit=100",
    pairEndpoint: "/api/v1/scanner/calendar-spread/pairs?limit=200",
  },
  "synthetic-arbitrage": {
    slug: "synthetic-arbitrage",
    title: "Synthetic Arbitrage",
    description: "Dedicated synthetic future/cash-carry scanner with the live option + future universe.",
    universe: "SUPPORTED INDEX + NIFTY TOP 15 LIQUID F&O STOCKS",
    columns: ["Time", "Underlying", "Expiry", "Strike", "Future", "Call (CE)", "Put (PE)", "Synthetic Future", "Edge", "Signal"],
    metrics: ["Eligible Combos", "Executable Edges", "Signals", "Last Scan"],
    controls: ["ALL", "TOP15", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"],
    pairTitle: "Option / Future Pair Monitor",
    pairColumns: ["Time", "Underlying", "Expiry", "Strike", "Future", "Call (CE)", "Put (PE)", "Synthetic Future", "Edge", "Direction", "State"],
    historyEndpoint: "/api/v1/scanner/synthetic-cash-carry/pair-history?days=2&limit=5000",
    pairEndpoint: "/api/v1/scanner/synthetic-cash-carry/pairs?limit=200",
  },
  "box-spread": {
    slug: "box-spread",
    title: "Box Spread",
    description: "Dedicated four-leg box scanner with executable edge and liquidity ranking.",
    universe: "INDEX + ELIGIBLE OPTIONS",
    columns: ["Underlying", "Expiry", "Low Strike", "High Strike", "Box Edge", "Liquidity", "Direction", "Signal"],
    metrics: ["Eligible Boxes", "Executable Edges", "Signals", "Last Scan"],
    controls: ["All Eligible", "Expiry", "Strike Range", "Edge Threshold"],
    pairTitle: "Low / High Strike Pair Monitor",
    pairColumns: ["Time", "Underlying", "Expiry", "Low Strike", "High Strike", "Box Edge", "Lot Size", "Direction", "State"],
    historyEndpoint: "/api/v1/scanner/box-spread/alerts?days=1&limit=100",
  },
  "custom-strategy": {
    slug: "custom-strategy",
    title: "Custom Strategy",
    description: "Independent strategy workspace.",
    universe: "CONFIGURATION ONLY",
    columns: ["Symbol", "Segment", "Contract", "Side", "Live Price", "Condition", "Action"],
    metrics: ["Eligible Symbols", "Conditions Met", "Alerts", "Last Scan"],
    controls: ["F&O Stocks", "Cash / Future / Option", "BUY / SELL", "Condition"],
    pairTitle: "Pair Monitor",
    pairColumns: ["Time", "Symbol", "State"],
    historyEndpoint: "",
  },
};

type Row = Record<string, unknown>;

function value(row: Row, ...keys: string[]) {
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key];
  }
  return undefined;
}

function numberValue(row: Row, ...keys: string[]) {
  const raw = value(row, ...keys);
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function nullableNumber(raw: unknown): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : null;
}

function formatExpiry(raw: unknown) {
  if (raw === undefined || raw === null || raw === "") return "—";
  let text = String(raw).trim();
  // Numeric JSON values can arrive as YYYYMMDD.0 and bypass the old 8-digit check.
  if (/^\d{8}\.0+$/.test(text)) text = text.split(".")[0];
  if (/^\d{8}$/.test(text)) {
    const year = Number(text.slice(0, 4));
    const month = Number(text.slice(4, 6));
    const day = Number(text.slice(6, 8));
    const date = new Date(Date.UTC(year, month - 1, day));
    if (date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day) {
      return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
    }
  }
  // Parse ISO dates emitted by the live API before the instrument-master formats.
  const isoMatch = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (isoMatch) {
    const year = Number(isoMatch[1]);
    const month = Number(isoMatch[2]);
    const day = Number(isoMatch[3]);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day) {
      return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
    }
  }
  // Parse common instrument-master expiry formats; leave unknown text untouched
  // rather than showing a misleading locale-formatted numeric value.
  const match = text.match(/^(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{2,4})$/);
  if (match) {
    const parsed = new Date(Date.parse(match[1] + " " + match[2] + " " + match[3] + " UTC"));
    if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  }
  return text;
}

function syntheticFutureValue(row: Row) {
  const strike = nullableNumber(value(row, "strike"));
  const callBid = nullableNumber(value(row, "call_bid"));
  const callAsk = nullableNumber(value(row, "call_ask"));
  const putBid = nullableNumber(value(row, "put_bid"));
  const putAsk = nullableNumber(value(row, "put_ask"));
  if (strike === null) return "Bid — / Ask —";
  // Raw parity values: K + CE Bid - PE Ask; K + CE Ask - PE Bid.
  const computedBid = callBid !== null && putAsk !== null ? strike + callBid - putAsk : null;
  const computedAsk = callAsk !== null && putBid !== null ? strike + callAsk - putBid : null;
  const syntheticBid = nullableNumber(value(row, "synthetic_bid")) ?? computedBid;
  const syntheticAsk = nullableNumber(value(row, "synthetic_ask")) ?? computedAsk;
  return "Bid " + formatValue(syntheticBid) + " / Ask " + formatValue(syntheticAsk);
}

function quoteSideNumber(raw: unknown, side: "bid" | "ask"): number | null {
  const direct = nullableNumber(raw);
  if (direct !== null) return direct;
  if (typeof raw !== "string") return null;
  // Some pair-monitor payloads provide display strings such as
  // "Bid 73,560 / Ask 73,583 · 15:05:48" instead of numeric fields.
  const label = side === "bid" ? "Bid" : "Ask";
  const match = raw.match(new RegExp(label + "\\s+([+-]?[\\d,]+(?:\\.\\d+)?)", "i"));
  if (match) {
    const parsed = Number(match[1].replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : null;
  }
  // Also accept a single formatted price when the field is explicitly a
  // bid/ask field, while avoiding accidental parsing of timestamps.
  const trimmed = raw.trim();
  if (/^[+-]?[\\d,]+(?:\\.\\d+)?$/.test(trimmed)) {
    const parsed = Number(trimmed.replaceAll(",", ""));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function syntheticRawEdges(row: Row) {
  const strike = nullableNumber(value(row, "strike"));
  // Prefer individual numeric fields, but tolerate combined quote summaries.
  const futureSummary = value(row, "future_summary");
  const callSummary = value(row, "call_summary");
  const putSummary = value(row, "put_summary");
  const futureBid = quoteSideNumber(value(row, "future_bid") ?? futureSummary, "bid");
  const futureAsk = quoteSideNumber(value(row, "future_ask") ?? futureSummary, "ask");
  const callBid = quoteSideNumber(value(row, "call_bid") ?? callSummary, "bid");
  const callAsk = quoteSideNumber(value(row, "call_ask") ?? callSummary, "ask");
  const putBid = quoteSideNumber(value(row, "put_bid") ?? putSummary, "bid");
  const putAsk = quoteSideNumber(value(row, "put_ask") ?? putSummary, "ask");
  if ([strike, futureBid, futureAsk, callBid, callAsk, putBid, putAsk].some(v => v === null)) return null;
  const syntheticBid = strike! + callBid! - putAsk!;
  const syntheticAsk = strike! + callAsk! - putBid!;
  return { long: syntheticBid - futureAsk!, short: futureBid! - syntheticAsk, syntheticBid, syntheticAsk };
}

function syntheticDisplayDirection(row: Row) {
  const edges = syntheticRawEdges(row);
  if (!edges) {
    // Do not present a stale backend direction as verified against displayed quotes.
    const backendEdge = nullableNumber(value(row, "executable_edge"));
    const backendDirection = String(value(row, "direction") ?? "").toUpperCase();
    return backendEdge !== null && backendEdge > 0 && (backendDirection === "LONG" || backendDirection === "SHORT")
      ? backendDirection
      : "NOT EVALUATED";
  }
  if (edges.long > 0 && edges.short <= 0) return "LONG";
  if (edges.short > 0 && edges.long <= 0) return "SHORT";
  if (edges.long > 0 && edges.short > 0) return edges.long >= edges.short ? "LONG" : "SHORT";
  return "NO EDGE";
}

function syntheticDisplayEdge(row: Row, raw: unknown) {
  const edges = syntheticRawEdges(row);
  if (!edges) return nullableNumber(raw);
  const direction = syntheticDisplayDirection(row);
  if (direction === "LONG") return edges.long > 0 ? edges.long : null;
  if (direction === "SHORT") return edges.short > 0 ? edges.short : null;
  return null;
}

function syntheticEdgeExplanation(row: Row, raw: unknown) {
  if (raw === undefined || raw === null || raw === "") return "NOT EVALUATED — executable quotes/config required";
  const direction = String(value(row, "direction") ?? "").toUpperCase();
  const futureBid = value(row, "future_bid"), futureAsk = value(row, "future_ask");
  const syntheticBuy = value(row, "carry_adjusted_synthetic_buy");
  const syntheticSell = value(row, "carry_adjusted_synthetic_sell");
  const fees = nullableNumber(value(row, "fees_per_unit"));
  const feeText = fees === null ? "" : " − fees " + formatValue(fees);
  const comparison = direction.includes("LONG") && syntheticBuy !== undefined && syntheticBuy !== null
    ? "Future Bid " + formatValue(futureBid) + " − carry-adjusted Synthetic Ask " + formatValue(syntheticBuy) + feeText
    : direction.includes("SHORT") && syntheticSell !== undefined && syntheticSell !== null
      ? "carry-adjusted Synthetic Bid " + formatValue(syntheticSell) + " − Future Ask " + formatValue(futureAsk) + feeText
      : "backend executable pricing (carry/fees applied)";
  return formatValue(raw) + " pts (" + comparison + ")";
}

function formatValue(raw: unknown) {
  if (raw === undefined || raw === null || raw === "") return "—";
  if (typeof raw === "boolean") return raw ? "YES" : "NO";
  if (typeof raw === "number") return Number.isFinite(raw) ? raw.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : "—";
  return String(raw);
}

function parseTimestamp(raw: unknown): Date | null {
  if (raw === undefined || raw === null || raw === "") return null;
  // Handle Unix seconds, milliseconds and nanoseconds without treating ms as ns.
  if (typeof raw === "number" || (typeof raw === "string" && /^\\d+(?:\\.\\d+)?$/.test(raw.trim()))) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    const millis = n >= 1e17 ? n / 1e6 : n >= 1e12 ? n : n >= 1e9 ? n * 1000 : NaN;
    if (!Number.isFinite(millis)) return null;
    const date = new Date(millis);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  const date = new Date(String(raw));
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatTimestamp(raw: unknown) {
  const date = parseTimestamp(raw);
  return date ? date.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "—";
}

function quoteTimestamp(row: Row) {
  const date = parseTimestamp(value(row, "timestamp_ns", "observed_at", "timestamp"));
  return date ? date.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "";
}

function withQuoteTimestamp(text: string, row: Row) {
  const timestamp = quoteTimestamp(row);
  return timestamp && text !== "—" ? text + " · " + timestamp : text;
}

function formatCell(slug: string, column: string, row: Row) {
  const keys: Record<string, Record<string, string[]>> = {
    "calendar-spread": {
      Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying"], Exchange: ["exchange"], Near: ["near_contract_month"],
      Far: ["far_contract_month"], Spread: ["gap_points"], "Gap / Edge": ["long_edge", "short_edge"],
      "Volume / OI": ["liquidity_qty"], Signal: ["direction"],
    },
    "synthetic-arbitrage": {
      Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying"], Expiry: ["expiry"], Strike: ["strike"],
      Future: ["future_bid", "future_ask", "future_summary"], "Call (CE)": ["call_bid", "call_ask"], "Put (PE)": ["put_bid", "put_ask"],
      "Synthetic Future": ["strike"], "Executable Edge": ["executable_edge"], "Edge / Lot": ["edge_per_lot"], "Gross P&L": ["gross_pnl"], Liquidity: ["lot_size"], Signal: ["direction"],
    },
    "box-spread": {
      Underlying: ["symbol"], Expiry: ["expiry"], "Low Strike": ["low_strike"],
      "High Strike": ["high_strike"], "Box Edge": ["executable_edge"],
      Liquidity: ["liquidity_qty"], Direction: ["direction"], Signal: ["direction"],
    },
  };
  const raw = value(row, ...(keys[slug]?.[column] ?? []));
  if (column === "Expiry") return formatExpiry(raw);
  if (column === "Underlying") return formatValue(value(row, "underlying", "symbol"));
  if (slug === "synthetic-arbitrage" && column === "Time") return formatTimestamp(raw);
  if (slug === "synthetic-arbitrage" && column === "Synthetic Future") return withQuoteTimestamp(syntheticFutureValue(row), row);
  if (slug === "synthetic-arbitrage" && column === "Executable Edge") {
    const edge = syntheticDisplayEdge(row, raw);
    const direction = syntheticDisplayDirection(row);
    return edge === null ? "NO EDGE" : formatValue(edge) + " pts — " + direction;
  }
  if (slug === "calendar-spread" && column === "Time") return formatTimestamp(raw);
  if (slug === "synthetic-arbitrage" && ["Future", "Call (CE)", "Put (PE)"].includes(column)) {
    const bid = column === "Future" ? row.future_bid : column === "Call (CE)" ? row.call_bid : row.put_bid;
    const ask = column === "Future" ? row.future_ask : column === "Call (CE)" ? row.call_ask : row.put_ask;
    const summary = column === "Future" ? row.future_summary : undefined;
    const shownBid = bid !== undefined && bid !== null && bid !== "" ? formatValue(bid) : "—";
    const shownAsk = ask !== undefined && ask !== null && ask !== "" ? formatValue(ask) : "—";
    if ((bid === undefined || bid === null || bid === "") && (ask === undefined || ask === null || ask === "") && summary !== undefined && summary !== null && summary !== "") return withQuoteTimestamp(formatValue(summary), row);
    return withQuoteTimestamp("Bid " + shownBid + " / Ask " + shownAsk, row);
  }
  return formatValue(raw);
}

function pairCell(slug: string, column: string, row: Row) {
  const maps: Record<string, Record<string, string[]>> = {
    "calendar-spread": {
      Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying"], Exchange: ["exchange"],
      Near: ["near_contract_month"], Far: ["far_contract_month"], Spread: ["gap_points"],
      Edge: ["long_edge", "short_edge"], Liquidity: ["liquidity_qty"], State: ["direction", "qualifies"],
    },
    "synthetic-arbitrage": {
      Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying", "symbol"], Expiry: ["expiry"], Strike: ["strike"],
      Future: ["future_bid", "future_ask", "future_summary"], "Call (CE)": ["call_bid", "call_ask"], "Put (PE)": ["put_bid", "put_ask"],
      "Synthetic Future": ["strike"], Edge: ["executable_edge"], "Edge / Lot": ["edge_per_lot"], "Gross P&L": ["gross_pnl"], Direction: ["direction"], State: ["executable_edge"],
    },
    "box-spread": {
      Time: ["timestamp_ns", "observed_at"], Underlying: ["symbol", "underlying"], Expiry: ["expiry"],
      "Low Strike": ["low_strike"], "High Strike": ["high_strike"], "Box Edge": ["executable_edge"],
      "Lot Size": ["lot_size"], Direction: ["direction"], State: ["executable_edge"],
    },
  };
  const raw = value(row, ...(maps[slug]?.[column] ?? []));
  if (slug === "synthetic-arbitrage" && column === "Synthetic Future") return withQuoteTimestamp(syntheticFutureValue(row), row);
  if (slug === "synthetic-arbitrage" && ["Future", "Call (CE)", "Put (PE)"].includes(column)) {
    const bid = column === "Future" ? row.future_bid : column === "Call (CE)" ? row.call_bid : row.put_bid;
    const ask = column === "Future" ? row.future_ask : column === "Call (CE)" ? row.call_ask : row.put_ask;
    const summary = column === "Future" ? row.future_summary : undefined;
    const shownBid = bid !== undefined && bid !== null && bid !== "" ? formatValue(bid) : "—";
    const shownAsk = ask !== undefined && ask !== null && ask !== "" ? formatValue(ask) : "—";
    if ((bid === undefined || bid === null || bid === "") && (ask === undefined || ask === null || ask === "") && summary !== undefined && summary !== null && summary !== "") return withQuoteTimestamp(formatValue(summary), row);
    return withQuoteTimestamp("Bid " + shownBid + " / Ask " + shownAsk, row);
  }
  if (column === "Time") return formatTimestamp(raw);
  if (column === "Expiry") return formatExpiry(raw);
  if (slug === "synthetic-arbitrage" && column === "Edge") {
    const edge = syntheticDisplayEdge(row, raw);
    const direction = syntheticDisplayDirection(row);
    return edge === null ? "—" : formatValue(edge) + " pts — " + direction;
  }
  if (slug === "synthetic-arbitrage" && (column === "Direction" || column === "Signal")) {
    return syntheticDisplayDirection(row);
  }
  if (column === "State") {
    const edgeRaw = value(row, "executable_edge", "long_edge", "short_edge");
    const edge = numberValue(row, "executable_edge", "long_edge", "short_edge");
    if (slug === "calendar-spread") return row.qualifies === true || edge > 0 ? "OPPORTUNITY" : "NO SIGNAL";
    if (slug === "synthetic-arbitrage") {
      const state = typeof row.state === "string" && row.state
        ? row.state.replaceAll("_", " ")
        : edgeRaw === undefined ? "NOT EVALUATED" : (edge > 0 ? "EDGE" : "NO EDGE");
      const reason = typeof row.reason === "string" && row.reason ? " · " + row.reason.replaceAll("_", " ") : "";
      return state + reason;
    }
    return edge > 0 ? "EDGE" : "NO EDGE";
  }
  return formatValue(raw);
}

async function copyTextWithFallback(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard?.writeText && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {}
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    area.setSelectionRange(0, area.value.length);
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch { return false; }
}

function historyCell(slug: string, column: string, row: Row) {
  if (slug === "synthetic-arbitrage" && column === "Time") {
    const observed = row.observed_at;
    if (observed !== undefined && observed !== null && observed !== "") {
      const date = new Date(String(observed));
      if (!Number.isNaN(date.getTime())) {
        return date.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
      }
    }
  }
  return pairCell(slug, column, row);
}

function isSignal(slug: string, row: Row) {
  if (slug === "calendar-spread") return row.qualifies === true || numberValue(row, "long_edge", "short_edge") > 0;
  return numberValue(row, "executable_edge") > 0;
}

function runnerLabel(slug: string) {
  if (slug === "calendar-spread") return "Calendar Spread Runner";
  if (slug === "synthetic-arbitrage") return "Synthetic Future Runner";
  if (slug === "box-spread") return "Box Spread Runner";
  return "Strategy Runner";
}

export function StrategyWorkspace({ slug }: { slug: keyof typeof configs }) {
  const c = configs[slug];
  const isCustom = slug === "custom-strategy";
  const endpoint =
    slug === "calendar-spread" ? "/api/v1/scanner/calendar-spread/live?limit=50" :
    slug === "synthetic-arbitrage" ? "/api/v1/scanner/synthetic-cash-carry/live?limit=50" :
    slug === "box-spread" ? "/api/v1/scanner/box-spread/live?limit=50&max_age_seconds=5" : null;

  const [search, setSearch] = useState("");
  const [control, setControl] = useState(c.controls[0]);
  const [top15StockSymbols, setTop15StockSymbols] = useState<string[]>([
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY",
    "TCS", "AXISBANK", "LT", "BHARTIARTL", "KOTAKBANK",
    "TMPV", "BAJFINANCE", "M&M", "HINDALCO", "TATASTEEL",
  ]);
  const [universeAvailability, setUniverseAvailability] = useState<{
    eligible_future_symbols?: string[]; missing_future_symbols?: string[];
    configured_index_symbols?: string[]; eligible_index_symbols?: string[]; missing_index_underlyings?: string[];
    eligible_target_symbols?: string[]; missing_option_expiry_symbols?: string[]; checked_at?: string | null;
  } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [rowsStale, setRowsStale] = useState(false);
  const rowsRef = useRef<Row[]>([]);
  const loadRequestRef = useRef(0);
  const [historyRows, setHistoryRows] = useState<Row[]>([]);
  const [pairRows, setPairRows] = useState<Row[]>([]);
  const [pairMonitorStatus, setPairMonitorStatus] = useState("CHECKING");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyFilter, setHistoryFilter] = useState("");
  const [historyPage, setHistoryPage] = useState(1);
  const [historyHealth, setHistoryHealth] = useState<{ last_save_at?: string | null; last_saved_count?: number; last_error?: string | null; storage?: string } | null>(null);
  const [copyStatus, setCopyStatus] = useState("");
  const [copyFallbackText, setCopyFallbackText] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedTicks, setFeedTicks] = useState<number | null>(null);
  const [feedAge, setFeedAge] = useState<number | null>(null);
  const [feedExchangeFreshness, setFeedExchangeFreshness] = useState<Record<string, { age_seconds?: number | null; status?: string }> | null>(null);
  const [feedStatus, setFeedStatus] = useState("CHECKING");
  const [feedInstruments, setFeedInstruments] = useState<number | null>(null);
  const [runner, setRunner] = useState<{ running: boolean; detail: string } | null>(null);
  const [feedErrors, setFeedErrors] = useState<{ delivery: number; normalizer: number } | null>(null);
  const [scannerDiagnostics, setScannerDiagnostics] = useState<Record<string, unknown> | null>(null);
  const [calendarRuntime, setCalendarRuntime] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (slug !== "synthetic-arbitrage") return;
    let cancelled = false;
    const base = appConfig.apiBaseUrl.replace(/\/$/, "");
    void fetch(base + "/api/v1/scanner/synthetic-cash-carry/universe", { cache: "no-store" })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body?.detail || "Unable to load synthetic universe");
        if (!cancelled && typeof body?.selection === "string") setControl(body.selection);
        if (!cancelled && Array.isArray(body?.top15_stock_symbols)) {
          setTop15StockSymbols(body.top15_stock_symbols.filter((symbol: unknown): symbol is string => typeof symbol === "string"));
        }
        if (!cancelled && body?.availability && typeof body.availability === "object") setUniverseAvailability(body.availability);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Unable to load synthetic universe");
      });
    return () => { cancelled = true; };
  }, [slug]);

  const changeControl = async (selection: string) => {
    if (slug !== "synthetic-arbitrage") {
      setControl(selection);
      return;
    }
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/universe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selection }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail?.message || body?.detail || "Universe update failed");
      setControl(String(body?.selection ?? selection));
      if (Array.isArray(body?.top15_stock_symbols)) {
        setTop15StockSymbols(body.top15_stock_symbols.filter((symbol: unknown): symbol is string => typeof symbol === "string"));
      }
      if (body?.availability && typeof body.availability === "object") setUniverseAvailability(body.availability);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Universe update failed");
    }
  };

  const load = async (showLoading = false) => {
    const requestId = ++loadRequestRef.current;
    if (!endpoint) {
      rowsRef.current = [];
      setRows([]);
      setRowsStale(false);
      setLoading(false);
      return;
    }
    if (showLoading) setLoading(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + endpoint, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || c.title + " HTTP " + response.status);
      const data = Array.isArray(body?.data) ? body.data.filter((item: unknown): item is Row => !!item && typeof item === "object") : [];
      if (requestId !== loadRequestRef.current) return;
      if (data.length > 0) {
        rowsRef.current = data;
        setRows(data);
        setRowsStale(false);
        if (slug === "synthetic-arbitrage") {
          // Cache only real backend rows so a reload during a temporary scanner
          // outage does not erase the last observed opportunities or their times.
          try {
            window.localStorage.setItem("algo:synthetic-arbitrage:last-live-opportunities:v1", JSON.stringify({
              savedAt: Date.now(),
              rows: data,
            }));
          } catch { /* Storage may be unavailable; live rendering still works. */ }
        }
      } else if (slug === "synthetic-arbitrage") {
        // Empty snapshots can be transient while the scanner recalculates.
        // Keep in-memory rows first, then fall back to the last real backend
        // snapshot after reload. The banner marks these rows as STALE, not live.
        let cachedRows: Row[] = [];
        if (rowsRef.current.length === 0) {
          try {
            const cached = JSON.parse(window.localStorage.getItem("algo:synthetic-arbitrage:last-live-opportunities:v1") || "null");
            if (Array.isArray(cached?.rows)) {
              cachedRows = cached.rows.filter((item: unknown): item is Row => !!item && typeof item === "object");
            }
          } catch { /* Ignore invalid/unavailable cache and keep normal empty state. */ }
        }
        const retained = rowsRef.current.length > 0 ? rowsRef.current : cachedRows;
        if (retained.length > 0) {
          rowsRef.current = retained;
          setRows(retained);
          setRowsStale(true);
        } else {
          rowsRef.current = [];
          setRows([]);
          setRowsStale(false);
        }
      } else {
        rowsRef.current = [];
        setRows([]);
        setRowsStale(false);
      }
      setError(null);
    } catch (e) {
      if (requestId !== loadRequestRef.current) return;
      // Preserve the last good rows during a temporary API failure, including
      // after a page reload. Cached rows are never presented as confirmed live.
      if (slug === "synthetic-arbitrage") {
        let cachedRows: Row[] = [];
        if (rowsRef.current.length === 0) {
          try {
            const cached = JSON.parse(window.localStorage.getItem("algo:synthetic-arbitrage:last-live-opportunities:v1") || "null");
            if (Array.isArray(cached?.rows)) {
              cachedRows = cached.rows.filter((item: unknown): item is Row => !!item && typeof item === "object");
            }
          } catch { /* Ignore invalid/unavailable cache. */ }
        }
        const retained = rowsRef.current.length > 0 ? rowsRef.current : cachedRows;
        if (retained.length > 0) {
          rowsRef.current = retained;
          setRows(retained);
          setRowsStale(true);
        }
      }
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  };

  const loadPairs = async () => {
    if (isCustom || !c.pairEndpoint) {
      setPairRows(rows);
      return;
    }
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + c.pairEndpoint, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(c.title + " pair monitor HTTP " + response.status);
      const data = Array.isArray(body?.data) ? body.data.filter((item: unknown): item is Row => !!item && typeof item === "object") : [];
      const responseStatus = String(body?.status ?? "UNKNOWN").toUpperCase();
      // Keep the last real pair snapshot across reloads as well as transient API errors.
      // Cached rows are historical observations only; the UI labels them STALE.
      if (data.length > 0) {
        setPairRows(data);
        if (slug === "synthetic-arbitrage") {
          try {
            window.localStorage.setItem("algo:synthetic-arbitrage:last-pairs:v1", JSON.stringify({
              savedAt: Date.now(),
              rows: data,
            }));
          } catch { /* Storage may be unavailable; live rendering still works. */ }
        }
        setPairMonitorStatus(responseStatus === "SUCCESS" ? "LIVE" : responseStatus);
      } else {
        let cachedRows: Row[] = [];
        if (slug === "synthetic-arbitrage" && pairRows.length === 0) {
          try {
            const cached = JSON.parse(window.localStorage.getItem("algo:synthetic-arbitrage:last-pairs:v1") || "null");
            if (Array.isArray(cached?.rows)) {
              cachedRows = cached.rows.filter((item: unknown): item is Row => !!item && typeof item === "object");
            }
          } catch { /* Ignore invalid/unavailable cache and keep normal empty state. */ }
        }
        const retained = pairRows.length > 0 ? pairRows : cachedRows;
        if (retained.length > 0) {
          setPairRows(retained);
          setPairMonitorStatus("STALE");
        } else {
          setPairRows([]);
          setPairMonitorStatus(responseStatus === "UNAVAILABLE" ? "UNAVAILABLE" : "WAITING");
        }
      }
    } catch {
      // On a transient error, preserve the last good snapshot and fall back to cache after reload.
      let cachedRows: Row[] = [];
      if (slug === "synthetic-arbitrage" && pairRows.length === 0) {
        try {
          const cached = JSON.parse(window.localStorage.getItem("algo:synthetic-arbitrage:last-pairs:v1") || "null");
          if (Array.isArray(cached?.rows)) {
            cachedRows = cached.rows.filter((item: unknown): item is Row => !!item && typeof item === "object");
          }
        } catch { /* Ignore invalid/unavailable cache and keep normal empty state. */ }
      }
      const retained = pairRows.length > 0 ? pairRows : cachedRows;
      if (retained.length > 0) setPairRows(retained);
      setPairMonitorStatus(retained.length > 0 ? "STALE" : "UNAVAILABLE");
    }
  };

  const loadHistory = async () => {
    if (isCustom || !c.historyEndpoint) return;
    setHistoryLoading(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + c.historyEndpoint, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(c.title + " history HTTP " + response.status);
      const data = Array.isArray(body?.data) ? body.data : [];
      setHistoryRows(data.filter((item: unknown): item is Row => !!item && typeof item === "object"));
      setHistoryHealth(body?.persistence && typeof body.persistence === "object" ? body.persistence : null);
      setHistoryError(null);
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : "History API unavailable");
      // Keep last good history snapshot during transient backend failures.
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => {
    if (isCustom) return;
    void loadHistory();
    const timer = window.setInterval(() => void loadHistory(), 10000);
    return () => window.clearInterval(timer);
  }, [isCustom, c.historyEndpoint]);

  useEffect(() => {
    if (isCustom || c.pairEndpoint) return;
    setPairRows(rows);
  }, [isCustom, c.pairEndpoint, rows]);

  useEffect(() => {
    if (isCustom || !c.pairEndpoint) return;
    void loadPairs();
    const timer = window.setInterval(() => void loadPairs(), 1500);
    return () => window.clearInterval(timer);
  }, [isCustom, c.pairEndpoint, pairRows.length]);

  useEffect(() => {
    if (slug !== "synthetic-arbitrage") {
      setScannerDiagnostics(null);
      return;
    }
    let cancelled = false;
    const loadScannerDiagnostics = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/diagnostics", { cache: "no-store" });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error("scanner diagnostics HTTP " + response.status);
        if (!cancelled) setScannerDiagnostics(body && typeof body === "object" ? body as Record<string, unknown> : null);
      } catch {
        if (!cancelled) setScannerDiagnostics({ status: "DIAGNOSTICS_UNAVAILABLE" });
      }
    };
    void loadScannerDiagnostics();
    const timer = window.setInterval(loadScannerDiagnostics, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [slug]);

  useEffect(() => {
    if (isCustom) return;
    let cancelled = false;
    const loadRuntime = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/market-data/runtime/health", { cache: "no-store" });
        if (!response.ok) throw new Error("runtime health");
        const body = await response.json();
        if (cancelled) return;
        const feed = body?.common_feed ?? {};
        // Number(null) and Number("") are 0; preserve missing backend values as unknown.
        setFeedTicks(nullableNumber(feed.ticks_received));
        setFeedAge(nullableNumber(feed.age_seconds));
        setFeedExchangeFreshness(
          feed.exchange_freshness && typeof feed.exchange_freshness === "object"
            ? feed.exchange_freshness as Record<string, { age_seconds?: number | null; status?: string }>
            : null,
        );
        setFeedInstruments(nullableNumber(feed.active_instruments));
        setFeedStatus(String(feed.status ?? body?.feed_status ?? "UNKNOWN").toUpperCase());
        setFeedErrors({
          delivery: Number(feed.delivery_errors ?? 0) || 0,
          normalizer: Number(feed.normalizer_errors ?? 0) || 0,
        });
        const rawCalendarRunner = body?.runners?.calendar_spread;
        setCalendarRuntime(
          slug === "calendar-spread" && rawCalendarRunner && typeof rawCalendarRunner === "object"
            ? rawCalendarRunner as Record<string, unknown>
            : null,
        );
        const rawRunner = body?.runners?.[slug === "synthetic-arbitrage" ? "synthetic" : slug.replace("-", "_")];
        const alternate = slug === "synthetic-arbitrage" ? body?.runners?.synthetic_arbitrage : slug === "box-spread" ? body?.runners?.box_spread : body?.runners?.calendar_spread;
        const selected = rawRunner ?? alternate;
        setRunner(selected ? { running: Boolean(selected.running), detail: String(selected.detail ?? (selected.running ? "RUNNING" : "STOPPED")) } : null);
      } catch {
        if (!cancelled) setFeedStatus("UNAVAILABLE");
      }
    };
    void loadRuntime();
    const timer = window.setInterval(loadRuntime, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [isCustom, slug]);

  useEffect(() => {
    if (isCustom) {
      setRows([]);
      setLoading(false);
      return;
    }
    void load(true);
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [endpoint, isCustom]);

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase();
    if (!q) return rows;
    return rows.filter((row) => Object.values(row).some((v) => String(v ?? "").toUpperCase().includes(q)));
  }, [rows, search]);

  const signals = rows.filter((row) => isSignal(slug, row)).length;
  const monitorRows = c.pairEndpoint ? pairRows : rows;
  const filteredPairRows = useMemo(() => {
    const q = search.trim().toUpperCase();
    if (!q) return monitorRows;
    return monitorRows.filter((row) => Object.values(row).some((v) => String(v ?? "").toUpperCase().includes(q)));
  }, [monitorRows, search]);
  const lastScan = rows.reduce<string | null>((latest, row) => {
    const raw = value(row, "timestamp_ns", "timestamp");
    if (raw == null) return latest;
    const current = String(raw);
    return latest == null || current > latest ? current : latest;
  }, null);

  const diagnosticSummary = (scannerDiagnostics?.calculation_summary && typeof scannerDiagnostics.calculation_summary === "object"
    ? scannerDiagnostics.calculation_summary : {}) as Row;
  const diagnosticBlockers = (Array.isArray(scannerDiagnostics?.top_blockers) ? scannerDiagnostics.top_blockers : []) as Row[];
  const diagnosticStatus = String(scannerDiagnostics?.status ?? "LOADING").replaceAll("_", " ");

  const refresh = async () => {
    setRefreshing(true);
    await Promise.all([load(), loadHistory(), loadPairs()]);
    setRefreshing(false);
  };

  const copyTableRows = async (kind: "history" | "pairs") => {
    const source = kind === "history" ? historyRows : filteredPairRows;
    const columns = kind === "history" && slug === "synthetic-arbitrage"
      ? ["Time", "Underlying", "Expiry", "Strike", "Direction", "Edge", "Edge / Lot", "Gross P&L", "State"]
      : c.pairColumns;
    const render = kind === "history" ? historyCell : pairCell;
    const payload = [columns.join("\t"), ...source.map((row) => columns.map((column) => render(slug, column, row)).join("\t"))].join("\n");
    const ok = await copyTextWithFallback(payload);
    if (ok) {
      setCopyFallbackText("");
      setCopyStatus(kind === "history" ? "History copied" : "Pair rows copied");
    } else {
      // Android WebViews may deny both Clipboard API and execCommand. Keep a
      // selectable text copy of the exact table payload instead of a dead end.
      setCopyFallbackText(payload);
      setCopyStatus("Automatic copy blocked — tap the text below, then Select all and Copy");
    }
    window.setTimeout(() => setCopyStatus(""), 6000);
  };

  const metricValue = (metric: string) => {
    if (loading) return "…";
    const staleSuffix = slug === "synthetic-arbitrage" && rowsStale ? " · STALE" : "";
    if (metric === "Eligible Contracts") return feedInstruments == null ? "—" : feedInstruments.toLocaleString("en-IN");
    if (metric === "Spread Opportunities" || metric === "Executable Edges" || metric === "Executable Gaps") return rows.length.toLocaleString("en-IN") + staleSuffix;
    if (metric === "Signals") return signals.toLocaleString("en-IN") + staleSuffix;
    if (metric === "Eligible Combos" || metric === "Eligible Boxes") return rows.length.toLocaleString("en-IN") + staleSuffix;
    if (metric === "Last Scan") return rowsStale && slug === "synthetic-arbitrage" ? "STALE" : lastScan ? "LIVE" : "—";
    return "—";
  };

  const normalizedHistoryFilter = historyFilter.trim().toUpperCase();
  const filteredHistoryRows = normalizedHistoryFilter
    ? historyRows.filter((row) => String(row.underlying ?? row.symbol ?? "").toUpperCase().includes(normalizedHistoryFilter))
    : historyRows;
  const historyPageSize = 25;
  const historyPageCount = Math.max(1, Math.ceil(filteredHistoryRows.length / historyPageSize));
  const safeHistoryPage = Math.min(historyPage, historyPageCount);
  const visibleHistoryRows = filteredHistoryRows.slice((safeHistoryPage - 1) * historyPageSize, safeHistoryPage * historyPageSize);

  return (
    <div className="min-h-screen space-y-5 theme-bg p-4 theme-text">
      <div className="flex items-center justify-between gap-3">
        <Link href="/scanner" className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface px-3 text-xs font-semibold theme-muted hover:theme-border">
          <ArrowLeft className="h-4 w-4" /> Cash Future Scanner
        </Link>
        <span className={"inline-flex min-h-10 items-center gap-2 rounded-xl border px-3 text-xs font-semibold " +
          (loading ? "theme-border theme-warning-bg theme-warning" : error ? "theme-border theme-danger-bg theme-danger" :
          feedStatus === "LIVE" ? "theme-border theme-success-bg theme-success" : "theme-border theme-warning-bg theme-warning")}>
          {loading ? "CONNECTING" : error ? "BACKEND ERROR" : feedStatus === "LIVE" ? "LIVE FEED" : feedStatus === "UNAVAILABLE" ? "FEED UNAVAILABLE" : feedStatus.replaceAll("_", " ")}
        </span>
      </div>

      <PageTitle eyebrow="Live Strategy Scanner" title={c.title} description={c.description} />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Universe</p><p className="mt-2 text-sm font-semibold theme-text">{c.universe}</p></Card>
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Common Market Data Feed</p><p className="mt-2 flex items-center gap-2 text-sm font-semibold theme-accent"><Activity className="h-4 w-4" />{feedTicks == null ? "CHECKING" : feedTicks.toLocaleString("en-IN") + " ticks"}</p></Card>
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">{runnerLabel(slug)}</p><p className={"mt-2 text-sm font-semibold " + (runner?.running ? "theme-success" : "theme-warning")}>{runner?.running ? "RUNNING" : "STOPPED / UNAVAILABLE"}</p></Card>
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Broker Orders</p><p className="mt-2 text-sm font-semibold theme-warning">OFF</p></Card>
      </div>

      <Card className="theme-border theme-surface p-4">
        <div className="flex items-center gap-2 text-sm font-semibold theme-text"><Filter className="h-4 w-4 theme-accent" /> Live Scanner Controls</div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-[1.2fr_1fr_auto]">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 theme-muted" />
            <span className="sr-only">Search</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search symbol / underlying" className="min-h-11 w-full rounded-xl border theme-border theme-surface px-3 pl-9 text-sm theme-text" />
          </label>
          <select value={control} onChange={(e) => void changeControl(e.target.value)} className="min-h-11 w-full rounded-xl border theme-border theme-surface px-3 text-sm theme-text">
            {c.controls.map((x) => <option key={x} value={x}>{slug === "synthetic-arbitrage" && x === "TOP15" ? "NIFTY TOP 15 LIQUID STOCKS (15)" : x}</option>)}
          </select>
          <button type="button" onClick={refresh} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border theme-border theme-surface px-4 text-sm font-semibold theme-text hover:theme-border">
            <RefreshCw className={"h-4 w-4 " + (refreshing ? "animate-spin" : "")} /> Refresh
          </button>
        </div>
        {slug === "synthetic-arbitrage" && (
          <div className="mt-3 rounded-xl border theme-border theme-surface-2 p-3 text-xs">
            <p className="font-semibold theme-text">
              {control === "TOP15" ? "NIFTY TOP 15 LIQUID STOCKS — stock-only scan" :
               control === "ALL" ? "ALL — supported indices + these 15 liquid F&O stocks" :
               "INDEX-ONLY SCAN — " + control}
            </p>
            {control === "TOP15" || control === "ALL" ? (
              <>
                <p className="mt-2 leading-6 theme-muted">{top15StockSymbols.join(" • ")}</p>
                <p className="mt-1 theme-muted">These names define the basket only; stocks are not separate dropdown choices.</p>
              </>
            ) : (
              <p className="mt-1 theme-muted">Only the selected index is scanned. No fallback to stocks or other indices.</p>
            )}
            {control === "TOP15" && <p className="mt-1 theme-success">TOP15 scans the configured stock basket automatically. No individual stock selection is required; only stocks with eligible current futures and matching option expiry can be scanned.</p>}
            {(control === "TOP15" || control === "ALL") && universeAvailability && (
              <div className="mt-2 space-y-1 border-t theme-border pt-2">
                <p className="theme-text">Eligible futures ({universeAvailability.eligible_future_symbols?.length ?? 0}/15): {(universeAvailability.eligible_future_symbols ?? []).join(" • ") || "Not yet checked"}</p>
                <p className="theme-muted">Missing futures ({universeAvailability.missing_future_symbols?.length ?? 0}): {(universeAvailability.missing_future_symbols ?? []).join(" • ") || "None reported"}</p>
                <p className="theme-text">Scan-ready targets ({universeAvailability.eligible_target_symbols?.filter((symbol) => top15StockSymbols.includes(symbol)).length ?? 0}/15): {(universeAvailability.eligible_target_symbols ?? []).filter((symbol) => top15StockSymbols.includes(symbol)).join(" • ") || "Not yet checked"}</p>
                <p className="theme-muted">Missing matching option expiry: {(universeAvailability.missing_option_expiry_symbols ?? []).filter((symbol) => top15StockSymbols.includes(symbol)).join(" • ") || "None reported"}</p>
                {control === "ALL" && <>
                  <p className="theme-text">Eligible index futures ({universeAvailability.eligible_index_symbols?.length ?? 0}/{universeAvailability.configured_index_symbols?.length ?? 6}): {(universeAvailability.eligible_index_symbols ?? []).join(" • ") || "Not yet checked"}</p>
                  <p className="theme-muted">Index symbols skipped (unresolvable token): {(universeAvailability.missing_index_underlyings ?? []).join(" • ") || "None reported"}</p>
                  <p className="theme-muted">Indices missing matching option expiry: {(universeAvailability.missing_option_expiry_symbols ?? []).filter((symbol) => (universeAvailability.configured_index_symbols ?? []).includes(symbol)).join(" • ") || "None reported"}</p>
                </>}
                <p className="theme-muted">Availability is from the last backend universe check, not a guarantee of live quotes.</p>
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {c.metrics.map((metric) => (
          <Card key={metric} className="theme-border theme-surface p-4">
            <p className="text-xs theme-muted">{metric}</p>
            <p className="mt-2 text-xl font-semibold theme-text">{metricValue(metric)}</p>
          </Card>
        ))}
      </div>

      <Card className="theme-border theme-surface p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <div><p className="text-xs theme-muted">Common Feed Ticks</p><p className="mt-1 text-lg font-semibold theme-text">{feedTicks == null ? "—" : feedTicks.toLocaleString("en-IN")}</p><p className="text-[11px] theme-muted">Actual cumulative feed counter</p></div>
          <div><p className="text-xs theme-muted">Feed Age (NSE/NFO)</p><p className="mt-1 text-lg font-semibold theme-text">{feedAge == null ? "—" : feedAge.toFixed(1) + "s"}</p><p className="text-[11px] theme-muted">Oldest required exchange tick</p></div>
          <div><p className="text-xs theme-muted">NSE / NFO Tick Status</p><p className="mt-1 text-sm font-semibold theme-text">NSE: {String(feedExchangeFreshness?.NSE?.status ?? "—")} {typeof feedExchangeFreshness?.NSE?.age_seconds === "number" ? "(" + feedExchangeFreshness.NSE.age_seconds.toFixed(1) + "s)" : ""}</p><p className="text-sm font-semibold theme-text">NFO: {String(feedExchangeFreshness?.NFO?.status ?? "—")} {typeof feedExchangeFreshness?.NFO?.age_seconds === "number" ? "(" + feedExchangeFreshness.NFO.age_seconds.toFixed(1) + "s)" : ""}</p></div>
          <div><p className="text-xs theme-muted">Active Instruments</p><p className="mt-1 text-lg font-semibold theme-text">{feedInstruments == null ? "—" : feedInstruments.toLocaleString("en-IN")}</p></div>
          <div><p className="text-xs theme-muted">Runner</p><p className={"mt-1 text-lg font-semibold " + (runner?.running ? "theme-success" : "theme-warning")}>{runner?.running ? "RUNNING" : "STOPPED"}</p></div>
          <div><p className="text-xs theme-muted">Feed Delivery Errors</p><p className="mt-1 text-lg font-semibold theme-text">{feedErrors?.delivery ?? "—"}</p></div>
          <div><p className="text-xs theme-muted">Normalizer Errors</p><p className="mt-1 text-lg font-semibold theme-text">{feedErrors?.normalizer ?? "—"}</p></div>
        </div>
      </Card>

      {slug === "synthetic-arbitrage" && (
        <Card className="theme-border theme-surface p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold theme-text">Synthetic Scanner Health</h2>
              <p className="mt-1 text-xs theme-muted">Scanner observations and completed calculations are separate from common-feed tick totals.</p>
            </div>
            <span className={"rounded-lg border px-3 py-1.5 text-xs font-semibold " + (String(scannerDiagnostics?.status ?? "UNKNOWN") === "SCANNING" ? "theme-success" : "theme-warning")}>{String(scannerDiagnostics?.status ?? "UNKNOWN")}</span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <div><p className="text-xs theme-muted">Observations</p><p className="mt-1 text-base font-semibold theme-text">{String(((scannerDiagnostics?.counters as Record<string, unknown> | undefined)?.observations_received) ?? "—")}</p></div>
            <div><p className="text-xs theme-muted">Completed scans</p><p className="mt-1 text-base font-semibold theme-text">{String(((scannerDiagnostics?.counters as Record<string, unknown> | undefined)?.scan_evaluations) ?? "—")}</p></div>
            <div><p className="text-xs theme-muted">Empty scans</p><p className="mt-1 text-base font-semibold theme-text">{String(((scannerDiagnostics?.counters as Record<string, unknown> | undefined)?.empty_calculations) ?? "—")}</p></div>
            <div><p className="text-xs theme-muted">Missing CE/PE events</p><p className="mt-1 text-base font-semibold theme-text">{String(((scannerDiagnostics?.counters as Record<string, unknown> | undefined)?.missing_ce_or_pe_leg) ?? "—")}</p></div>
            <div><p className="text-xs theme-muted">Missing CALL</p><p className="mt-1 text-base font-semibold theme-text">{String(((scannerDiagnostics?.counters as Record<string, unknown> | undefined)?.missing_call_leg) ?? "—")}</p></div>
            <div><p className="text-xs theme-muted">Missing PUT</p><p className="mt-1 text-base font-semibold theme-text">{String(((scannerDiagnostics?.counters as Record<string, unknown> | undefined)?.missing_put_leg) ?? "—")}</p></div>
            <div><p className="text-xs theme-muted">Last scan age</p><p className="mt-1 text-base font-semibold theme-text">{typeof scannerDiagnostics?.last_scan_age_seconds === "number" ? scannerDiagnostics.last_scan_age_seconds.toFixed(1) + "s" : "—"}</p></div>
          </div>
          {typeof scannerDiagnostics?.phase_reason === "string" && scannerDiagnostics.phase_reason && <p className="mt-2 rounded-lg border theme-border theme-surface-2 p-2 text-xs theme-muted">Runner phase: {String(scannerDiagnostics.phase ?? scannerDiagnostics.status ?? "UNKNOWN")} · {String(scannerDiagnostics.phase_reason).replaceAll("_", " ")}</p>}
          {(() => {
            const counters = (scannerDiagnostics?.counters && typeof scannerDiagnostics.counters === "object" ? scannerDiagnostics.counters : {}) as Record<string, unknown>;
            const symbols = Object.keys(counters)
              .filter((key) => /^(scan_evaluations|successful_pairs|opportunities_emitted|empty_calculations)_by_symbol:/.test(key))
              .reduce((map, key) => {
                const match = key.match(/^(scan_evaluations|successful_pairs|opportunities_emitted|empty_calculations)_by_symbol:(.+)$/);
                if (!match) return map;
                const symbol = match[2];
                const row = map.get(symbol) ?? { underlying: symbol };
                row[match[1]] = counters[key];
                map.set(symbol, row);
                return map;
              }, new Map<string, Record<string, unknown>>());
            const rows = Array.from(symbols.values()).sort((a, b) => String(a.underlying).localeCompare(String(b.underlying)));
            return <div className="mt-4 border-t theme-border pt-3">
              <div><h3 className="text-sm font-semibold theme-text">Per-symbol scan outcomes</h3><p className="mt-1 text-xs theme-muted">Calculation and emitted-result counts; these are not trading recommendations.</p></div>
              {rows.length === 0 ? <p className="mt-2 rounded-lg border theme-border p-3 text-xs theme-muted">Per-symbol scan counters have not arrived yet. Refresh after the backend deployment completes.</p> : <div className="mt-2 overflow-x-auto rounded-lg border theme-border"><table className="w-full min-w-[560px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Symbol", "Calculations", "Pairs evaluated", "Results emitted", "Empty calculations"].map((label) => <th key={label} className="px-3 py-2 font-semibold">{label}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={String(row.underlying)} className="border-t theme-border"><td className="px-3 py-2 font-semibold theme-text">{String(row.underlying)}</td><td className="px-3 py-2 theme-muted">{String(row.scan_evaluations ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(row.successful_pairs ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(row.opportunities_emitted ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(row.empty_calculations ?? "—")}</td></tr>)}</tbody></table></div>}
            </div>;
          })()}
          <p className="mt-3 text-xs theme-muted">Zero signals can be a valid market result. A stale or incomplete scanner is shown as a health problem, not as a successful empty scan. Broker orders remain OFF.</p>
        </Card>
      )}

      {error && <Card className="theme-border theme-danger-bg p-4 text-sm theme-danger">{error}</Card>}

      <Card className="overflow-hidden theme-border theme-surface">
        <div className="flex flex-col gap-2 border-b theme-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Zap className="h-4 w-4 theme-accent" /> Live Opportunities</h2><p className="mt-1 text-xs theme-muted">Real backend rows only. No fake scanner values are generated in the UI.</p>{rowsStale && slug === "synthetic-arbitrage" && rows.length > 0 && <p className="mt-2 rounded-lg border theme-border theme-warning-bg px-3 py-2 text-xs theme-warning">Showing last observed opportunities with their original Time values. Data is STALE — not confirmed live; waiting for a fresh scanner response.</p>}</div>
          <span className="text-xs theme-muted">{control}{search ? " • " + search : ""} • 1.5s refresh</span>
        </div>
        <div className="grid gap-2 p-3 md:hidden">
          {loading ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">Loading live data…</div> : filteredRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">No live opportunity data</div> : filteredRows.map((row, index) => <div key={String(row.id ?? row.event_id ?? row.timestamp_ns ?? slug + "-mobile-live-" + index)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="mb-2 text-sm font-bold theme-text">{formatCell(slug, c.columns[0], row)}</div><div className="grid grid-cols-2 gap-x-3 gap-y-2">{c.columns.slice(1).map((column) => <div key={column} className="min-w-0"><div className="text-[10px] uppercase tracking-wide theme-subtle">{column}</div><div className="mt-0.5 break-words text-xs font-medium theme-muted">{formatCell(slug, column, row)}</div></div>)}</div></div>)}
        </div>
        <div className="hidden overflow-x-auto md:block">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{c.columns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={c.columns.length} className="px-4 py-14 text-center text-sm theme-muted">Loading live data…</td></tr> :
              filteredRows.length === 0 ? <tr><td colSpan={c.columns.length} className="px-4 py-14 text-center text-sm theme-muted">No live opportunity data</td></tr> :
              filteredRows.map((row, index) => <tr key={String(row.id ?? row.event_id ?? row.timestamp_ns ?? slug + "-" + index)} className="border-b theme-border last:border-0">
                {c.columns.map((column, i) => <td key={column} className={"px-4 py-3 " + (i === 0 ? "font-semibold theme-text" : "theme-muted")}>{formatCell(slug, column, row)}</td>)}
              </tr>)}
            </tbody>
          </table>
        </div>
      </Card>

      {slug === "synthetic-arbitrage" && (
        <Card className="theme-border theme-surface p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold theme-text">Synthetic Scanner Diagnostics</h2>
              <p className="mt-1 text-xs theme-muted">Calculation health and actual backend blockers • refreshes every 1.5 seconds</p>
            </div>
            <span className={"rounded-lg border px-2.5 py-1 text-xs font-semibold " + (diagnosticStatus === "SCANNING" ? "theme-border theme-success-bg theme-success" : "theme-border theme-warning-bg theme-warning")}>{diagnosticStatus}</span>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
            {([
              ["Evaluations", diagnosticSummary.evaluations],
              ["Successful pairs", diagnosticSummary.successful_pairs],
              ["Opportunities emitted", diagnosticSummary.opportunities_emitted],
              ["Empty calculations", diagnosticSummary.empty_calculations],
              ["Last result count", diagnosticSummary.last_result_count],
            ] as Array<[string, unknown]>).map(([label, raw]) => (
              <div key={String(label)} className="rounded-xl border theme-border theme-surface-2 p-3">
                <p className="text-xs theme-muted">{label}</p>
                <p className="mt-1 text-lg font-semibold theme-text">{typeof raw === "number" ? raw.toLocaleString("en-IN") : "—"}</p>
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            <div className="rounded-xl border theme-border theme-surface-2 p-3">
              <p className="text-xs theme-muted">Last observation age</p>
              <p className="mt-1 text-sm font-semibold theme-text">{typeof scannerDiagnostics?.observation_age_seconds === "number" ? scannerDiagnostics.observation_age_seconds.toFixed(1) + "s" : "—"}</p>
            </div>
            <div className="rounded-xl border theme-border theme-surface-2 p-3">
              <p className="text-xs theme-muted">Last successful calculation</p>
              <p className="mt-1 text-sm font-semibold theme-text">{typeof scannerDiagnostics?.last_scan_age_seconds === "number" ? scannerDiagnostics.last_scan_age_seconds.toFixed(1) + "s ago" : "Not yet completed"}</p>
              <p className="mt-1 text-[11px] theme-muted">This is calculation age, not merely the latest pair snapshot.</p>
            </div>
          </div>
          <div className="mt-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-semibold theme-text">Top blockers</p>
              <p className="text-[10px] theme-muted">Cumulative events; categories may overlap</p>
            </div>
            {diagnosticBlockers.length === 0 ? (
              <p className="mt-2 rounded-lg border theme-border p-3 text-xs theme-muted">{scannerDiagnostics?.status === "DIAGNOSTICS_UNAVAILABLE" || scannerDiagnostics?.status === "unavailable" ? "Diagnostics endpoint/provider unavailable; backend deployment may be pending." : "No reported blocker counters yet."}</p>
            ) : (
              <div className="mt-2 space-y-2">
                {diagnosticBlockers.map((item, index) => (
                  <div key={String(item.reason ?? index)} className="flex items-center justify-between gap-3 rounded-lg border theme-border px-3 py-2">
                    <span className="min-w-0 break-words text-xs theme-muted">{String(item.reason ?? "Unknown blocker").replaceAll("_", " ")}</span>
                    <span className="shrink-0 text-sm font-semibold theme-text">{typeof item.count === "number" ? item.count.toLocaleString("en-IN") : "—"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {(() => {
            const readiness = (scannerDiagnostics?.atm_readiness_summary && typeof scannerDiagnostics.atm_readiness_summary === "object" ? scannerDiagnostics.atm_readiness_summary : {}) as Row;
            const atmTracker = (scannerDiagnostics?.atm_tracker_diagnostics && typeof scannerDiagnostics.atm_tracker_diagnostics === "object" ? scannerDiagnostics.atm_tracker_diagnostics : {}) as Row;
            const subscriptions = (scannerDiagnostics?.subscription_diagnostics && typeof scannerDiagnostics.subscription_diagnostics === "object" ? scannerDiagnostics.subscription_diagnostics : {}) as Row;
            const atmSymbols = (Array.isArray(atmTracker.symbols) ? atmTracker.symbols : Array.isArray(readiness.not_ready_symbols) ? readiness.not_ready_symbols : []) as Row[];
            const subscriptionSymbols = (Array.isArray(subscriptions.symbols) ? subscriptions.symbols : []) as Row[];
            const underlyingFeed = (scannerDiagnostics?.underlying_feed && typeof scannerDiagnostics.underlying_feed === "object" ? scannerDiagnostics.underlying_feed : {}) as Row;
            const rawFeedSymbols = underlyingFeed.symbols;
            const feedSymbols: Row[] = Array.isArray(rawFeedSymbols)
              ? rawFeedSymbols as Row[]
              : rawFeedSymbols && typeof rawFeedSymbols === "object"
                ? Object.entries(rawFeedSymbols as Record<string, unknown>).map(([symbol, item]) => ({
                    ...(item && typeof item === "object" ? item as Row : {}),
                    underlying: (item && typeof item === "object" ? (item as Row).underlying ?? (item as Row).symbol : undefined) ?? symbol,
                  }))
                : [];
            const symbolRows = new Map<string, Row>();
            for (const item of atmSymbols) symbolRows.set(String(item.underlying ?? item.symbol ?? "UNKNOWN").toUpperCase(), { ...item, diagnostic_source: "ATM" });
            for (const item of subscriptionSymbols) {
              const key = String(item.underlying ?? item.symbol ?? "UNKNOWN").toUpperCase();
              const previous = symbolRows.get(key) ?? {};
              symbolRows.set(key, {
                ...previous,
                ...item,
                underlying: key,
                // Subscription snapshots may omit ATM fields or provide nulls.
                // Keep the real ATM tracker values so stock/index readiness is not hidden.
                atm_ready: typeof item.atm_ready === "boolean" ? item.atm_ready : previous.atm_ready,
                has_underlying_price: typeof item.has_underlying_price === "boolean" ? item.has_underlying_price : previous.has_underlying_price,
                last_price: item.last_price ?? item.last_underlying_price ?? previous.last_price ?? previous.last_underlying_price,
                last_underlying_price: item.last_underlying_price ?? item.last_price ?? previous.last_underlying_price ?? previous.last_price,
                source_age_seconds: item.source_age_seconds ?? previous.source_age_seconds,
                // Diagnostic endpoints can emit partial snapshots. Nulls must not erase concrete subscription counts.
                future_count: item.future_count ?? previous.future_count,
                call_count: item.call_count ?? previous.call_count,
                put_count: item.put_count ?? previous.put_count,
                complete_pair_count: item.complete_pair_count ?? previous.complete_pair_count,
                subscription_count: item.subscription_count ?? previous.subscription_count,
                expiry: item.expiry ?? previous.expiry,
                atm_strike: item.atm_strike ?? previous.atm_strike,
                feed_state: item.feed_state ?? previous.feed_state,
                feed_reason: item.feed_reason ?? previous.feed_reason,
                concrete_strike_count: typeof previous.concrete_strike_count === "number" ? previous.concrete_strike_count : item.concrete_strike_count,
                reason: item.reason || previous.reason,
              });
            }
            for (const item of feedSymbols) {
              const key = String(item.underlying ?? item.symbol ?? item.name ?? "").toUpperCase();
              if (!key) continue;
              const previous = symbolRows.get(key) ?? {};
              const tickAge = item.last_tick_age_seconds ?? item.receive_age_seconds ?? item.source_timestamp_age_seconds;
              const feedState = item.state ?? item.status ?? item.feed_state
                ?? (item.token_resolved === false ? "TOKEN_UNRESOLVED"
                  : typeof tickAge === "number" ? (tickAge <= 5 ? "LIVE" : "STALE")
                  : item.token_resolved === true ? "WAITING_FOR_TICK"
                  : previous.feed_state ?? underlyingFeed.state);
              const feedReason = feedState === "TOKEN_UNRESOLVED" ? "underlying_token_unresolved"
                : feedState === "WAITING_FOR_TICK" ? "token_resolved_waiting_for_first_tick"
                : feedState === "STALE" ? "underlying_tick_stale"
                : undefined;
              symbolRows.set(key, {
                ...previous,
                ...item,
                underlying: key,
                feed_state: feedState,
                feed_reason: item.state_reason || item.reason || feedReason || previous.feed_reason,
                token_resolved: typeof item.token_resolved === "boolean" ? item.token_resolved : previous.token_resolved,
                // Normalize the feed's canonical last_price while preserving a previously observed real price if this diagnostic omits it.
                last_price: item.last_price ?? item.last_underlying_price ?? previous.last_price ?? previous.last_underlying_price,
                last_underlying_price: item.last_underlying_price ?? item.last_price ?? previous.last_underlying_price ?? previous.last_price,
                last_tick_age_seconds: typeof tickAge === "number" ? tickAge : previous.last_tick_age_seconds,
                // Prefer the actual underlying-feed tick age over an older ATM/subscription age.
                // Otherwise the merged row can show stale feed age even after a fresh tick arrives.
                source_age_seconds: typeof tickAge === "number" ? tickAge : previous.source_age_seconds,
                // Feed-only snapshots must not blank subscription/ATM readiness values.
                future_count: item.future_count ?? previous.future_count,
                call_count: item.call_count ?? previous.call_count,
                put_count: item.put_count ?? previous.put_count,
                complete_pair_count: item.complete_pair_count ?? previous.complete_pair_count,
                subscription_count: item.subscription_count ?? previous.subscription_count,
                expiry: item.expiry ?? previous.expiry,
                atm_strike: item.atm_strike ?? previous.atm_strike,
                concrete_strike_count: item.concrete_strike_count ?? previous.concrete_strike_count,
                reason: previous.reason || item.state_reason || item.reason || feedReason,
              });
            }
            const counters = (scannerDiagnostics?.counters && typeof scannerDiagnostics.counters === "object" ? scannerDiagnostics.counters : {}) as Record<string, unknown>;
            for (const [counterKey, value] of Object.entries(counters)) {
              const match = counterKey.match(/^(scan_evaluations|successful_pairs|opportunities_emitted|empty_calculations|missing_atm_price|atm_provider_errors|config_provider_errors|scan_calculation_errors|latest_scan_result_count|latest_scan_at_ns|latest_scan_status)_by_symbol:(.+)$/);
              if (!match) continue;
              const key = match[2].toUpperCase();
              const previous = symbolRows.get(key) ?? { underlying: key };
              symbolRows.set(key, { ...previous, [match[1]]: value });
            }
            const rows = Array.from(symbolRows.values()).sort((a, b) => String(a.underlying ?? "").localeCompare(String(b.underlying ?? "")));
            return <div className="mt-4 border-t theme-border pt-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div><h3 className="text-sm font-semibold theme-text">Per-symbol readiness</h3><p className="mt-1 text-xs theme-muted">Live diagnostics only; scan counters are cumulative since scanner start, and missing fields are shown as —.</p></div>
                <span className="rounded-lg border theme-border px-2 py-1 text-xs theme-muted">{typeof readiness.ready_symbols === "number" ? readiness.ready_symbols : "—"} ready / {typeof readiness.tracked_symbols === "number" ? readiness.tracked_symbols : "—"} tracked</span>
              </div>
              {rows.length === 0 ? <p className="mt-2 rounded-lg border theme-border p-3 text-xs theme-muted">Per-symbol readiness has not been supplied by the backend yet. Check universe/ATM diagnostics endpoint.</p> : <><div className="mt-3 grid gap-2 md:hidden">{rows.map((item, index) => <div key={String(item.underlying ?? index)} className="rounded-lg border theme-border theme-surface-2 p-3"><div className="flex items-start justify-between gap-2"><div className="font-semibold theme-text">{String(item.underlying ?? item.symbol ?? "—")}</div><span className="rounded-md border theme-border px-2 py-1 text-[10px] font-semibold theme-muted">{String(item.feed_state ?? "FEED UNKNOWN").replaceAll("_", " ")}</span></div><div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 text-xs"><div><div className="theme-subtle">Scan outcome</div><div className="mt-0.5 theme-muted">{Number(item.opportunities_emitted ?? 0) > 0 ? "OPPORTUNITY EMITTED" : Number(item.scan_calculation_errors ?? 0) > 0 ? "CALCULATION ERROR" : Number(item.config_provider_errors ?? 0) > 0 ? "CONFIG ERROR" : Number(item.atm_provider_errors ?? 0) > 0 ? "ATM LOOKUP ERROR" : Number(item.missing_atm_price ?? 0) > 0 ? "ATM PRICE MISSING" : Number(item.scan_evaluations ?? 0) > 0 ? "CALCULATED · NO EDGE" : "NOT YET EVALUATED"}</div></div><div><div className="theme-subtle">Latest scan</div><div className="mt-0.5 theme-muted">{item.latest_scan_at_ns ? (Math.max(0, Date.now() - Number(item.latest_scan_at_ns) / 1_000_000) <= 5000 ? "FRESH" : "STALE") + " · " + (Number(item.latest_scan_result_count ?? 0) > 0 ? "RESULTS " + String(item.latest_scan_result_count) : String(item.latest_scan_status ?? "NO EDGE").replaceAll("_", " ")) + " · " + Math.max(0, Math.floor((Date.now() - Number(item.latest_scan_at_ns) / 1_000_000) / 1000)) + "s ago" : "NOT SCANNED"}</div></div><div><div className="theme-subtle">Feed age</div><div className="mt-0.5 theme-muted">{typeof (item.last_tick_age_seconds ?? item.source_age_seconds) === "number" ? Number(item.last_tick_age_seconds ?? item.source_age_seconds).toFixed(1) + "s" : "—"}</div></div><div><div className="theme-subtle">Token resolved</div><div className="mt-0.5 theme-muted">{item.token_resolved === true ? "YES" : item.token_resolved === false ? "NO" : "UNKNOWN"}</div></div><div><div className="theme-subtle">Underlying price</div><div className="mt-0.5 theme-muted">{String(item.last_price ?? item.last_underlying_price ?? item.underlying_price ?? item.price ?? "—")}</div></div><div><div className="theme-subtle">Expiry / ATM</div><div className="mt-0.5 theme-muted">{formatExpiry(item.expiry)} · ATM {String(item.atm_strike ?? "—")}</div></div><div><div className="theme-subtle">Futures / Calls / Puts</div><div className="mt-0.5 theme-muted">{String(item.future_count ?? "—")} / {String(item.call_count ?? "—")} / {String(item.put_count ?? "—")}</div></div><div className="col-span-2"><div className="theme-subtle">Feed blocker</div><div className="mt-0.5 break-words theme-muted">{String(item.feed_reason ?? item.state_reason ?? "—").replaceAll("_", " ")}</div></div><div className="col-span-2"><div className="theme-subtle">Reason / blocker</div><div className="mt-0.5 break-words theme-muted">{String(item.reason ?? item.blocker ?? "—").replaceAll("_", " ")}</div></div></div></div>)}</div><div className="mt-3 overflow-x-auto rounded-lg border theme-border"><table className="w-full min-w-[720px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Symbol","Class","Status","Latest scan","Scan outcome (cumulative)","Feed state","Token","Expiry","Futures","Calls","Puts","CE/PE pairs","ATM strikes","Selected ATM","ATM misses","ATM errors","Config errors","Calculation errors","Calculations","Pairs evaluated","Results emitted","Empty scans","Underlying price","Feed age","Feed blocker","Reason"].map(label => <th key={label} className="px-3 py-2 font-semibold">{label}</th>)}</tr></thead><tbody>{rows.map((item, index) => <tr key={String(item.underlying ?? index)} className="border-t theme-border"><td className="px-3 py-2 font-semibold theme-text">{String(item.underlying ?? item.symbol ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.instrument_class ?? item.asset_class ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.status ?? (item.ready === true || item.atm_ready === true ? "READY" : item.ready === false || item.atm_ready === false ? "WAITING_FOR_ATM" : "—")).replaceAll("_", " ")}</td><td className="px-3 py-2 theme-muted">{item.latest_scan_at_ns ? (Math.max(0, Date.now() - Number(item.latest_scan_at_ns) / 1_000_000) <= 5000 ? "FRESH" : "STALE") + " · " + (item.latest_scan_status && item.latest_scan_status !== "COMPLETED" && item.latest_scan_status !== "RESULTS" && item.latest_scan_status !== "NO_EDGE" ? String(item.latest_scan_status).replaceAll("_", " ") : Number(item.latest_scan_result_count ?? 0) > 0 ? "RESULTS: " + String(item.latest_scan_result_count) : "NO EDGE") + " · " + quoteTimestamp({timestamp_ns: item.latest_scan_at_ns}) + " · " + Math.max(0, Math.floor((Date.now() - Number(item.latest_scan_at_ns) / 1_000_000) / 1000)) + "s ago" : "NOT SCANNED"}</td><td className="px-3 py-2 theme-muted">{Number(item.opportunities_emitted ?? 0) > 0 ? "OPPORTUNITY EMITTED" : Number(item.scan_calculation_errors ?? 0) > 0 ? "CALCULATION ERROR" : Number(item.config_provider_errors ?? 0) > 0 ? "CONFIG ERROR" : Number(item.atm_provider_errors ?? 0) > 0 ? "ATM LOOKUP ERROR" : Number(item.missing_atm_price ?? 0) > 0 ? "ATM PRICE MISSING" : Number(item.scan_evaluations ?? 0) > 0 ? "CALCULATED · NO EDGE" : Number(item.successful_pairs ?? 0) > 0 ? "PAIRS SEEN · NOT EVALUATED" : item.feed_state === "LIVE" && Number(item.complete_pair_count ?? 0) === 0 ? "WAITING FOR CE/PE PAIR" : "NOT YET EVALUATED"}</td><td className="px-3 py-2 theme-muted">{String(item.feed_state ?? "—").replaceAll("_", " ")}</td><td className="px-3 py-2 theme-muted">{item.token_resolved === true ? "YES" : item.token_resolved === false ? "NO" : "—"}</td><td className="px-3 py-2 theme-muted">{formatExpiry(item.expiry)}</td><td className="px-3 py-2 theme-muted">{String(item.future_count ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.call_count ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.put_count ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.complete_pair_count ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.concrete_strike_count ?? item.strike_count ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.atm_strike ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.missing_atm_price ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.atm_provider_errors ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.config_provider_errors ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.scan_calculation_errors ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.scan_evaluations ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.successful_pairs ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.opportunities_emitted ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.empty_calculations ?? "—")}</td><td className="px-3 py-2 theme-muted">{String(item.last_price ?? item.underlying_price ?? item.last_underlying_price ?? item.price ?? (item.underlying_price_present === true || item.has_underlying_price === true ? "present" : "—"))}</td><td className="px-3 py-2 theme-muted">{typeof (item.last_tick_age_seconds ?? item.source_age_seconds) === "number" ? Number(item.last_tick_age_seconds ?? item.source_age_seconds).toFixed(1) + "s" : "—"}</td><td className="px-3 py-2 theme-muted">{String(item.feed_reason ?? "—").replaceAll("_", " ")}</td><td className="px-3 py-2 theme-muted">{String(item.reason ?? item.blocker ?? "—").replaceAll("_", " ")}</td></tr>)}</tbody></table></div></>}
            </div>;
          })()}
        </Card>
      )}

      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Activity className="h-4 w-4 theme-accent" /> Live {c.pairTitle}</h2><p className="mt-1 text-xs theme-muted">Real backend pairs stay visible during brief empty/error snapshots; retained rows are clearly marked STALE.</p></div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void copyTableRows("pairs")} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">Copy rows</button>
            <span className="text-xs font-semibold theme-muted">{monitorRows.length.toLocaleString("en-IN")} live pairs • {slug === "synthetic-arbitrage" ? "pair feed " + pairMonitorStatus.replaceAll("_", " ") + " • " : ""}1.5s refresh</span>
          </div>
        </div>
        {slug === "synthetic-arbitrage" && pairMonitorStatus === "STALE" && <div role="status" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm theme-text">STALE SNAPSHOT — backend pair data is temporarily empty or unavailable. Showing the last real rows with their original timestamps; these are not confirmed live opportunities.</div>}
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Live pairs</p><p className="mt-1 text-lg font-semibold theme-text">{monitorRows.length.toLocaleString("en-IN")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Signals / edges</p><p className="mt-1 text-lg font-semibold theme-text">{signals.toLocaleString("en-IN")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Feed status</p><p className="mt-1 text-lg font-semibold theme-text">{feedStatus}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Feed age</p><p className="mt-1 text-lg font-semibold theme-text">{feedAge == null ? "—" : feedAge.toFixed(1) + "s"}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Last completed scan</p><p className="mt-1 text-lg font-semibold theme-text">{slug === "synthetic-arbitrage" ? (typeof scannerDiagnostics?.last_scan_age_seconds === "number" ? scannerDiagnostics.last_scan_age_seconds.toFixed(1) + "s ago" : "Not yet") : (lastScan ? "LIVE" : "—")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Broker execution</p><p className="mt-1 text-lg font-semibold theme-warning">OFF</p></div>
        </div>
        <div className="mt-4 grid gap-2 md:hidden">
          {loading ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">Loading live pair monitor…</div> : filteredPairRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">{slug === "synthetic-arbitrage" && pairMonitorStatus === "UNAVAILABLE" ? "Pair monitor endpoint unavailable — check backend/API connectivity." : slug === "synthetic-arbitrage" && pairMonitorStatus === "WAITING" ? "Backend pair monitor is waiting for scanner observations." : "No live pair/combo observations"}</div> : filteredPairRows.map((row, index) => <div key={String(row.id ?? row.timestamp_ns ?? slug + "-mobile-pair-" + index)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="mb-2 text-sm font-bold theme-text">{pairCell(slug, c.pairColumns[1], row)}</div><div className="grid grid-cols-2 gap-x-3 gap-y-2">{c.pairColumns.filter((column) => column !== c.pairColumns[1]).map((column) => <div key={column} className="min-w-0"><div className="text-[10px] uppercase tracking-wide theme-subtle">{column}</div><div className="mt-0.5 break-words text-xs font-medium theme-muted">{pairCell(slug, column, row)}</div></div>)}</div></div>)}
        </div>
        <div className="mt-4 hidden overflow-x-auto md:block">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{c.pairColumns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={c.pairColumns.length} className="px-4 py-10 text-center theme-muted">Loading live pair monitor…</td></tr> :
              filteredPairRows.length === 0 ? <tr><td colSpan={c.pairColumns.length} className="px-4 py-10 text-center theme-muted">{slug === "synthetic-arbitrage" && pairMonitorStatus === "UNAVAILABLE" ? "Pair monitor endpoint unavailable; check backend/API connectivity." : slug === "synthetic-arbitrage" && pairMonitorStatus === "WAITING" ? "Backend pair monitor is waiting for scanner observations." : "No live pair/combo observations"}</td></tr> :
              filteredPairRows.map((row, index) => <tr key={String(row.id ?? row.timestamp_ns ?? slug + "-pair-" + index)} className="border-b theme-border last:border-0">
                {c.pairColumns.map((column, i) => <td key={column} className={"px-4 py-3 " + (i === 1 ? "font-semibold theme-text" : "theme-muted")}>{pairCell(slug, column, row)}</td>)}
              </tr>)}
            </tbody>
          </table>
        </div>
        <div className="mt-3 rounded-xl border theme-border theme-surface-2 p-3 text-xs theme-muted"><span className="font-semibold theme-text">Common feed:</span> {feedTicks == null ? "—" : feedTicks.toLocaleString("en-IN") + " ticks"} • {feedInstruments == null ? "—" : feedInstruments.toLocaleString("en-IN") + " instruments"} • runner {runner?.running ? "RUNNING" : "STOPPED"}.</div>
      </Card>

      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Activity className="h-4 w-4 theme-accent" /> Post-Market {c.title} Pair History</h2><p className="mt-1 text-xs theme-muted">Real backend history/alert records for today's session. No synthetic rows are created in the UI.</p></div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void copyTableRows("history")} disabled={historyRows.length === 0} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-semibold theme-text disabled:opacity-50">Copy history</button>
            <span className="text-xs font-semibold theme-muted">{historyLoading ? "Loading…" : historyRows.length.toLocaleString("en-IN") + " saved"}</span>
          </div>
        </div>
        {slug === "synthetic-arbitrage" && <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <label className="flex min-h-10 flex-1 items-center gap-2 rounded-lg border theme-border theme-surface-2 px-3">
            <span className="text-xs theme-muted">Symbol</span>
            <input value={historyFilter} onChange={(event) => { setHistoryFilter(event.target.value); setHistoryPage(1); }} placeholder="All stocks / indices" className="min-w-0 flex-1 bg-transparent text-sm theme-text outline-none" aria-label="Filter pair history by underlying symbol" />
            {historyFilter && <button type="button" onClick={() => { setHistoryFilter(""); setHistoryPage(1); }} className="text-xs theme-muted">Clear</button>}
          </label>
          <span className="text-xs theme-muted">Showing {filteredHistoryRows.length === 0 ? 0 : (safeHistoryPage - 1) * historyPageSize + 1}–{Math.min(safeHistoryPage * historyPageSize, filteredHistoryRows.length)} of {filteredHistoryRows.length} matches</span>
        </div>}
        {historyError && <div role="status" className="mt-3 rounded-lg border theme-border theme-warning-bg p-3 text-xs theme-warning">History refresh failed: {historyError}. Previously loaded rows are retained.</div>}
        {slug === "synthetic-arbitrage" && historyHealth && <p className="mt-2 text-xs theme-muted">Storage: {historyHealth.storage || "UNKNOWN"} · Last saved: {historyHealth.last_save_at || "not reported"} · Last batch: {historyHealth.last_saved_count ?? 0} rows{historyHealth.last_error ? " · Save error: " + historyHealth.last_error : ""}</p>}
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Saved observations</p><p className="mt-1 text-lg font-semibold theme-text">{historyRows.length.toLocaleString("en-IN")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Live monitor</p><p className={"mt-1 text-lg font-semibold " + (pairMonitorStatus === "LIVE" ? "theme-success" : "theme-muted")}>{pairMonitorStatus}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">History source</p><p className="mt-1 text-sm font-semibold theme-text">{slug === "synthetic-arbitrage" ? "Saved live pair snapshots (5s sampling)" : slug === "calendar-spread" ? "Scanner history" : "Alert history"}</p></div>
        </div>
        <div className="mt-4 grid gap-2 md:hidden">
          {filteredHistoryRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">{historyRows.length === 0 ? "No saved pair snapshots yet. Keep this page open while the live pair monitor runs." : "No history rows match this symbol filter."}</div> : visibleHistoryRows.map((row, index) => {
            const cols = c.pairColumns;
            return <div key={String(row.id ?? row.timestamp_ns ?? slug + "-mobile-history-" + index)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="mb-2 text-sm font-bold theme-text">{historyCell(slug, cols[1], row)}</div><div className="grid grid-cols-2 gap-x-3 gap-y-2">{cols.filter((column) => column !== cols[1]).map((column) => <div key={column} className="min-w-0"><div className="text-[10px] uppercase tracking-wide theme-subtle">{column}</div><div className="mt-0.5 break-words text-xs font-medium theme-muted">{historyCell(slug, column, row)}</div></div>)}</div></div>;
          })}
        </div>
        <div className="mt-4 hidden overflow-x-auto md:block">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{c.pairColumns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {filteredHistoryRows.length === 0 ? <tr><td colSpan={c.pairColumns.length} className="px-4 py-10 text-center theme-muted">{historyRows.length === 0 ? "No saved pair snapshots yet. History begins collecting when the live pair endpoint is polled." : "No history rows match this symbol filter."}</td></tr> :
              visibleHistoryRows.map((row, index) => {
                const cols = c.pairColumns;
                return <tr key={String(row.id ?? row.timestamp_ns ?? slug + "-history-" + index)} className="border-b theme-border last:border-0">
                  {cols.map((column, i) => <td key={column} className={"px-4 py-3 " + (i === 1 ? "font-semibold theme-text" : "theme-muted")}>{historyCell(slug, column, row)}</td>)}
                </tr>;
              })}
            </tbody>
          </table>
        </div>
        {slug === "synthetic-arbitrage" && historyPageCount > 1 && <div className="mt-4 flex items-center justify-between gap-3">
          <button type="button" disabled={safeHistoryPage <= 1} onClick={() => setHistoryPage((page) => Math.max(1, page - 1))} className="min-h-10 rounded-lg border theme-border px-4 text-xs font-semibold theme-text disabled:opacity-40">Previous</button>
          <span className="text-xs theme-muted">Page {safeHistoryPage} of {historyPageCount} · 25 rows/page</span>
          <button type="button" disabled={safeHistoryPage >= historyPageCount} onClick={() => setHistoryPage((page) => Math.min(historyPageCount, page + 1))} className="min-h-10 rounded-lg border theme-border px-4 text-xs font-semibold theme-text disabled:opacity-40">Next</button>
        </div>}
      </Card>

      {copyStatus && <div role="status" aria-live="polite" className="rounded-xl border theme-border theme-surface p-3 text-sm theme-text">{copyStatus}</div>}
      {copyFallbackText && <div className="space-y-2 rounded-xl border theme-border theme-surface p-3">
        <label htmlFor="copy-fallback-text" className="text-xs font-semibold theme-text">Manual copy fallback</label>
        <textarea id="copy-fallback-text" readOnly value={copyFallbackText} onFocus={(event) => event.currentTarget.select()} className="min-h-28 w-full rounded-lg border theme-border theme-surface-2 p-2 text-xs theme-text" />
        <p className="text-xs theme-muted">Tap the text, choose Select all, then Copy. Rows are tab-separated for pasting into a spreadsheet.</p>
        <button type="button" onClick={() => { setCopyFallbackText(""); setCopyStatus(""); }} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">Close</button>
      </div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="theme-border theme-surface p-4"><div className="flex items-center gap-2 text-sm font-semibold theme-text"><Bell className="h-4 w-4 theme-accent" /> Strategy Alerts</div><p className="mt-2 text-xs leading-5 theme-muted">Scanner-wide alerts remain separate from live opportunity rendering.</p><Link href="/custom-alert" className="mt-3 inline-flex text-xs font-semibold theme-accent">Open alerts →</Link></Card>
        <Card className="theme-border theme-surface p-4"><div className="flex items-center gap-2 text-sm font-semibold theme-text"><ShieldCheck className="h-4 w-4 theme-success" /> Scanner Safety Boundary</div><p className="mt-2 text-xs leading-5 theme-muted">This workspace reads live scanner output only. Broker orders remain OFF and no paper execution logic is changed.</p></Card>
        {slug === "calendar-spread" && <Card className="theme-border theme-surface p-4">
          <div className="text-sm font-semibold theme-text">Exchange Coverage Diagnostics</div>
          <p className="mt-2 text-xs leading-5 theme-muted">This is the runner's actual contract/session/tick report. A closed exchange can have subscribed contracts but no valid live ticks; no historical quote is relabelled as live.</p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {[
              ["NFO contracts", (calendarRuntime?.contracts_by_exchange as Record<string, unknown> | undefined)?.NFO],
              ["BFO contracts", (calendarRuntime?.contracts_by_exchange as Record<string, unknown> | undefined)?.BFO],
              ["MCX contracts", (calendarRuntime?.contracts_by_exchange as Record<string, unknown> | undefined)?.MCX],
              ["Stock futures", (calendarRuntime?.contracts_by_family as Record<string, unknown> | undefined)?.STOCK_FUTURE],
              ["Index futures", (calendarRuntime?.contracts_by_family as Record<string, unknown> | undefined)?.INDEX_FUTURE],
              ["Commodity futures", (calendarRuntime?.contracts_by_family as Record<string, unknown> | undefined)?.COMMODITY_FUTURE],
            ].map(([label, value]) => <div key={String(label)} className="rounded-lg border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">{String(label)}</p><p className="mt-1 text-base font-semibold theme-text">{value == null ? "—" : String(value)}</p></div>)}
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-3">
            {(["NFO", "BFO", "MCX"] as const).map((exchange) => {
              const sessions = (calendarRuntime?.session_by_exchange ?? {}) as Record<string, { open?: boolean }>;
              const received = (calendarRuntime?.diagnostics as Record<string, unknown> | undefined)?.records_received_by_exchange as Record<string, unknown> | undefined;
              const emitted = (calendarRuntime?.diagnostics as Record<string, unknown> | undefined)?.records_emitted_by_exchange as Record<string, unknown> | undefined;
              const sourceDropped = (calendarRuntime?.diagnostics as Record<string, unknown> | undefined)?.source_timestamp_dropped_by_exchange as Record<string, unknown> | undefined;
              const sessionDropped = (calendarRuntime?.diagnostics as Record<string, unknown> | undefined)?.out_of_session_dropped_by_exchange as Record<string, unknown> | undefined;
              const isOpen = sessions[exchange]?.open;
              return <div key={exchange} className="rounded-lg border theme-border p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold theme-text">{exchange}</span><span className={"text-xs font-semibold " + (isOpen === true ? "theme-success" : isOpen === false ? "theme-warning" : "theme-muted")}>{isOpen === true ? "SESSION OPEN" : isOpen === false ? "SESSION CLOSED" : "SESSION UNKNOWN"}</span></div><p className="mt-2 text-xs theme-muted">Ticks received: {String(received?.[exchange] ?? 0)} · emitted: {String(emitted?.[exchange] ?? 0)}</p><p className="mt-1 text-xs theme-muted">Missing source time: {String(sourceDropped?.[exchange] ?? 0)} · out-of-session: {String(sessionDropped?.[exchange] ?? 0)}</p></div>;
            })}
          </div>
          {!calendarRuntime && <p className="mt-3 rounded-lg border theme-border p-3 text-xs theme-muted">Calendar runner diagnostics are not available yet. Check backend deployment and runner health.</p>}
        </Card>}
        <Card className="theme-border theme-surface p-4"><div className="text-sm font-semibold theme-text">Session Diagnostics</div><p className="mt-2 text-xs leading-5 theme-muted">MCX can remain live after NSE closes. Calendar/commodity output therefore follows the common feed and actual exchange session rather than the NSE-only status.</p></Card>
      </div>
    </div>
  );
}
