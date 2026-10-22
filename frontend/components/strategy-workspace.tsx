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
    slug: "calendar-spread", title: "Calendar Spread", description: "Dedicated near-vs-far expiry spread scanner using the common live market-data feed.", universe: "F&O + INDEX + COMMODITIES", columns: ["Time", "Underlying", "Exchange", "Near", "Far", "Spread", "Gap / Edge", "Volume / OI", "Signal"], metrics: ["Eligible Contracts", "Spread Opportunities", "Signals", "Last Scan"], controls: ["All Eligible", "INDEX PRIORITY", "Near/Far", "Edge Threshold"], pairTitle: "Near / Far Pair Monitor", pairColumns: ["Time", "Underlying", "Exchange", "Near", "Far", "Spread", "Edge", "Liquidity", "State"], historyEndpoint: "/api/v1/scanner/calendar-spread/history?days=1&limit=100", pairEndpoint: "/api/v1/scanner/calendar-spread/pairs?limit=200",
  },
  "synthetic-arbitrage": {
    slug: "synthetic-arbitrage", title: "Synthetic Arbitrage", description: "Dedicated synthetic future/cash-carry scanner with the live option + future universe.", universe: "SUPPORTED INDEX + NIFTY TOP 15 LIQUID F&O STOCKS", columns: ["Time", "Underlying", "Expiry", "Strike", "Future", "Call (CE)", "Put (PE)", "Synthetic Future", "Edge", "Signal"], metrics: ["Eligible Combos", "Executable Edges", "Signals", "Last Scan"], controls: ["ALL", "TOP15", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"], pairTitle: "Option / Future Pair Monitor", pairColumns: ["Time", "Underlying", "Expiry", "Strike", "Future", "Call (CE)", "Put (PE)", "Synthetic Future", "Edge", "Direction", "State"], historyEndpoint: "/api/v1/scanner/synthetic-cash-carry/pair-history?days=2&limit=5000", pairEndpoint: "/api/v1/scanner/synthetic-cash-carry/pairs?limit=200",
  },
  "box-spread": {
    slug: "box-spread", title: "Box Spread", description: "Dedicated four-leg box scanner with executable edge and liquidity ranking.", universe: "INDEX + ELIGIBLE OPTIONS", columns: ["Underlying", "Expiry", "Low Strike", "High Strike", "Box Edge", "Liquidity", "Direction", "Signal"], metrics: ["Eligible Boxes", "Executable Edges", "Signals", "Last Scan"], controls: ["All Eligible", "Expiry", "Strike Range", "Edge Threshold"], pairTitle: "Low / High Strike Pair Monitor", pairColumns: ["Time", "Underlying", "Expiry", "Low Strike", "High Strike", "Box Edge", "Lot Size", "Direction", "State"], historyEndpoint: "/api/v1/scanner/box-spread/alerts?days=1&limit=100",
  },
  "custom-strategy": {
    slug: "custom-strategy", title: "Custom Strategy", description: "Independent strategy workspace.", universe: "CONFIGURATION ONLY", columns: ["Symbol", "Segment", "Contract", "Side", "Live Price", "Condition", "Action"], metrics: ["Eligible Symbols", "Conditions Met", "Alerts", "Last Scan"], controls: ["F&O Stocks", "Cash / Future / Option", "BUY / SELL", "Condition"], pairTitle: "Pair Monitor", pairColumns: ["Time", "Symbol", "State"], historyEndpoint: "",
  },
};

type Row = Record<string, unknown>;

function value(row: Row, ...keys: string[]) { for (const key of keys) { if (row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key]; } return undefined; }
function numberValue(row: Row, ...keys: string[]) { const raw = value(row, ...keys); const n = Number(raw); return Number.isFinite(n) ? n : 0; }
function nullableNumber(raw: unknown): number | null { if (raw === null || raw === undefined || raw === "") return null; const n = typeof raw === "number" ? raw : Number(raw); return Number.isFinite(n) ? n : null; }

function formatExpiry(raw: unknown) {
  if (raw === undefined || raw === null || raw === "") return "—";
  let text = String(raw).trim();
  if (/^\d{8}\.0+$/.test(text)) text = text.split(".")[0];
  if (/^\d{8}$/.test(text)) { const year = Number(text.slice(0, 4)), month = Number(text.slice(4, 6)), day = Number(text.slice(6, 8)); const date = new Date(Date.UTC(year, month - 1, day)); if (date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day) return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }); }
  const isoMatch = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (isoMatch) { const year = Number(isoMatch[1]), month = Number(isoMatch[2]), day = Number(isoMatch[3]); const date = new Date(Date.UTC(year, month - 1, day)); if (date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day) return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }); }
  const match = text.match(/^(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{2,4})$/);
  if (match) { const parsed = new Date(Date.parse(match[1] + " " + match[2] + " " + match[3] + " UTC")); if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }); }
  return text;
}

function syntheticFutureValue(row: Row) {
  const strike = nullableNumber(value(row, "strike")); const callBid = nullableNumber(value(row, "call_bid")); const callAsk = nullableNumber(value(row, "call_ask")); const putBid = nullableNumber(value(row, "put_bid")); const putAsk = nullableNumber(value(row, "put_ask"));
  if (strike === null) return "Bid — / Ask —";
  const computedBid = callBid !== null && putAsk !== null ? strike + callBid - putAsk : null;
  const computedAsk = callAsk !== null && putBid !== null ? strike + callAsk - putBid : null;
  const syntheticBid = nullableNumber(value(row, "synthetic_bid")) ?? computedBid;
  const syntheticAsk = nullableNumber(value(row, "synthetic_ask")) ?? computedAsk;
  return "Bid " + formatValue(syntheticBid) + " / Ask " + formatValue(syntheticAsk);
}

function syntheticRawEdges(row: Row) {
  const strike = nullableNumber(value(row, "strike")); const futureBid = nullableNumber(value(row, "future_bid")); const futureAsk = nullableNumber(value(row, "future_ask"));
  const callBid = nullableNumber(value(row, "call_bid")); const callAsk = nullableNumber(value(row, "call_ask")); const putBid = nullableNumber(value(row, "put_bid")); const putAsk = nullableNumber(value(row, "put_ask"));
  if ([strike, futureBid, futureAsk, callBid, callAsk, putBid, putAsk].some(v => v === null)) return null;
  const syntheticBid = strike! + callBid! - putAsk!;
  const syntheticAsk = strike! + callAsk! - putBid!;
  return { long: syntheticBid - futureAsk!, short: futureBid! - syntheticAsk!, syntheticBid, syntheticAsk };
}

function syntheticDisplayDirection(row: Row) {
  const edges = syntheticRawEdges(row); if (!edges) return String(value(row, "direction") ?? "").toUpperCase() || "NOT EVALUATED";
  const existing = String(value(row, "direction") ?? "").toUpperCase();
  const existingEdge = edges[existing === "SHORT" ? "short" : "long"];
  // The quote-derived direction is authoritative. This prevents stale persisted rows
  // from displaying LONG when the current executable side is SHORT.
  if (existing === "LONG" && edges.long > 0) return "LONG";
  if (existing === "SHORT" && edges.short > 0) return "SHORT";
  if (edges.long > 0 && edges.short <= 0) return "LONG";
  if (edges.short > 0 && edges.long <= 0) return "SHORT";
  if (edges.long > 0 && edges.short > 0) return edges.long >= edges.short ? "LONG" : "SHORT";
  return existing || "NO EDGE";
}

function syntheticDisplayEdge(row: Row, raw: unknown) {
  const edges = syntheticRawEdges(row); const direction = syntheticDisplayDirection(row);
  if (edges && (direction === "LONG" || direction === "SHORT")) return edges[direction === "LONG" ? "long" : "short"];
  return nullableNumber(raw);
}

function syntheticEdgeExplanation(row: Row, raw: unknown) {
  const edges = syntheticRawEdges(row); const direction = syntheticDisplayDirection(row);
  if (edges && (direction === "LONG" || direction === "SHORT")) {
    const edge = edges[direction === "LONG" ? "long" : "short"];
    const comparison = direction === "LONG"
      ? "Synthetic Bid " + formatValue(edges.syntheticBid) + " − Future Ask " + formatValue(value(row, "future_ask"))
      : "Future Bid " + formatValue(value(row, "future_bid")) + " − Synthetic Ask " + formatValue(edges.syntheticAsk);
    return formatValue(edge) + " pts (" + comparison + ")";
  }
  if (raw === undefined || raw === null || raw === "") return "NOT EVALUATED — executable quotes/config required";
  return formatValue(raw) + " pts (raw executable bid/ask difference; no carry or fees)";
}

function formatValue(raw: unknown) { if (raw === undefined || raw === null || raw === "") return "—"; if (typeof raw === "boolean") return raw ? "YES" : "NO"; if (typeof raw === "number") return Number.isFinite(raw) ? raw.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : "—"; return String(raw); }
function parseTimestamp(raw: unknown): Date | null { if (raw === undefined || raw === null || raw === "") return null; if (typeof raw === "number" || (typeof raw === "string" && /^\d+(?:\.\d+)?$/.test(raw.trim()))) { const n = Number(raw); if (!Number.isFinite(n)) return null; const millis = n >= 1e17 ? n / 1e6 : n >= 1e12 ? n : n >= 1e9 ? n * 1000 : NaN; if (!Number.isFinite(millis)) return null; const date = new Date(millis); return Number.isNaN(date.getTime()) ? null : date; } const date = new Date(String(raw)); return Number.isNaN(date.getTime()) ? null : date; }
function formatTimestamp(raw: unknown) { const date = parseTimestamp(raw); return date ? date.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "—"; }
function quoteTimestamp(row: Row) { const date = parseTimestamp(value(row, "timestamp_ns", "observed_at", "timestamp")); return date ? date.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : ""; }
function withQuoteTimestamp(text: string, row: Row) { const timestamp = quoteTimestamp(row); return timestamp && text !== "—" ? text + " · " + timestamp : text; }

function formatCell(slug: string, column: string, row: Row) {
  const keys: Record<string, Record<string, string[]>> = {
    "calendar-spread": { Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying"], Exchange: ["exchange"], Near: ["near_contract_month"], Far: ["far_contract_month"], Spread: ["gap_points"], "Gap / Edge": ["long_edge", "short_edge"], "Volume / OI": ["liquidity_qty"], Signal: ["direction"] },
    "synthetic-arbitrage": { Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying"], Expiry: ["expiry"], Strike: ["strike"], Future: ["future_bid", "future_ask", "future_summary"], "Call (CE)": ["call_bid", "call_ask"], "Put (PE)": ["put_bid", "put_ask"], "Synthetic Future": ["strike"], Edge: ["executable_edge"], "Executable Edge": ["executable_edge"], "Edge / Lot": ["edge_per_lot"], "Gross P&L": ["gross_pnl"], Liquidity: ["lot_size"], Signal: ["direction"] },
    "box-spread": { Underlying: ["symbol"], Expiry: ["expiry"], "Low Strike": ["low_strike"], "High Strike": ["high_strike"], "Box Edge": ["executable_edge"], Liquidity: ["liquidity_qty"], Direction: ["direction"], Signal: ["direction"] },
  };
  const raw = value(row, ...(keys[slug]?.[column] ?? []));
  if (column === "Expiry") return formatExpiry(raw); if (column === "Underlying") return formatValue(value(row, "underlying", "symbol")); if (slug === "synthetic-arbitrage" && column === "Time") return formatTimestamp(raw); if (slug === "synthetic-arbitrage" && column === "Synthetic Future") return withQuoteTimestamp(syntheticFutureValue(row), row);
  if (slug === "synthetic-arbitrage" && (column === "Edge" || column === "Executable Edge")) return syntheticEdgeExplanation(row, raw);
  if (slug === "synthetic-arbitrage" && ["Future", "Call (CE)", "Put (PE)"].includes(column)) { const bid = column === "Future" ? row.future_bid : column === "Call (CE)" ? row.call_bid : row.put_bid; const ask = column === "Future" ? row.future_ask : column === "Call (CE)" ? row.call_ask : row.put_ask; const summary = column === "Future" ? row.future_summary : undefined; const shownBid = bid !== undefined && bid !== null && bid !== "" ? formatValue(bid) : "—"; const shownAsk = ask !== undefined && ask !== null && ask !== "" ? formatValue(ask) : "—"; if ((bid === undefined || bid === null || bid === "") && (ask === undefined || ask === null || ask === "") && summary !== undefined && summary !== null && summary !== "") return withQuoteTimestamp(formatValue(summary), row); return withQuoteTimestamp("Bid " + shownBid + " / Ask " + shownAsk, row); }
  if (column === "Time") return formatTimestamp(raw); if (column === "Expiry") return formatExpiry(raw); if (slug === "synthetic-arbitrage" && column === "Edge") return syntheticEdgeExplanation(row, raw); if (slug === "synthetic-arbitrage" && column === "Direction") return syntheticDisplayDirection(row);
  if (column === "State") { const edgeRaw = value(row, "executable_edge", "long_edge", "short_edge"); const edge = numberValue(row, "executable_edge", "long_edge", "short_edge"); if (slug === "calendar-spread") return row.qualifies === true || edge > 0 ? "OPPORTUNITY" : "NO SIGNAL"; if (slug === "synthetic-arbitrage") { const displayEdge = syntheticDisplayEdge(row, edgeRaw); const state = typeof row.state === "string" && row.state ? row.state.replaceAll("_", " ") : displayEdge !== null && displayEdge > 0 ? "EDGE" : "NO EDGE"; const reason = typeof row.reason === "string" && row.reason ? " · " + row.reason.replaceAll("_", " ") : ""; return state + reason; } return edge > 0 ? "EDGE" : "NO EDGE"; }
  return formatValue(raw);
}

function pairCell(slug: string, column: string, row: Row) {
  const maps: Record<string, Record<string, string[]>> = {
    "calendar-spread": { Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying"], Exchange: ["exchange"], Near: ["near_contract_month"], Far: ["far_contract_month"], Spread: ["gap_points"], Edge: ["long_edge", "short_edge"], Liquidity: ["liquidity_qty"], State: ["direction", "qualifies"] },
    "synthetic-arbitrage": { Time: ["timestamp_ns", "observed_at", "timestamp"], Underlying: ["underlying", "symbol"], Expiry: ["expiry"], Strike: ["strike"], Future: ["future_bid", "future_ask", "future_summary"], "Call (CE)": ["call_bid", "call_ask"], "Put (PE)": ["put_bid", "put_ask"], "Synthetic Future": ["strike"], Edge: ["executable_edge"], "Edge / Lot": ["edge_per_lot"], "Gross P&L": ["gross_pnl"], Direction: ["direction"], State: ["executable_edge"] },
    "box-spread": { Time: ["timestamp_ns", "observed_at"], Underlying: ["symbol", "underlying"], Expiry: ["expiry"], "Low Strike": ["low_strike"], "High Strike": ["high_strike"], "Box Edge": ["executable_edge"], "Lot Size": ["lot_size"], Direction: ["direction"], State: ["executable_edge"] },
  };
  const raw = value(row, ...(maps[slug]?.[column] ?? [])); if (slug === "synthetic-arbitrage" && column === "Synthetic Future") return withQuoteTimestamp(syntheticFutureValue(row), row); if (slug === "synthetic-arbitrage" && ["Future", "Call (CE)", "Put (PE)"].includes(column)) { const bid = column === "Future" ? row.future_bid : column === "Call (CE)" ? row.call_bid : row.put_bid; const ask = column === "Future" ? row.future_ask : column === "Call (CE)" ? row.call_ask : row.put_ask; const summary = column === "Future" ? row.future_summary : undefined; const shownBid = bid !== undefined && bid !== null && bid !== "" ? formatValue(bid) : "—"; const shownAsk = ask !== undefined && ask !== null && ask !== "" ? formatValue(ask) : "—"; if ((bid === undefined || bid === null || bid === "") && (ask === undefined || ask === null || ask === "") && summary !== undefined && summary !== null && summary !== "") return withQuoteTimestamp(formatValue(summary), row); return withQuoteTimestamp("Bid " + shownBid + " / Ask " + shownAsk, row); }
  if (column === "Time") return formatTimestamp(raw); if (column === "Expiry") return formatExpiry(raw); if (slug === "synthetic-arbitrage" && column === "Edge") return syntheticEdgeExplanation(row, raw); if (slug === "synthetic-arbitrage" && column === "Direction") return syntheticDisplayDirection(row); if (column === "State") { const edgeRaw = value(row, "executable_edge", "long_edge", "short_edge"); const edge = numberValue(row, "executable_edge", "long_edge", "short_edge"); if (slug === "calendar-spread") return row.qualifies === true || edge > 0 ? "OPPORTUNITY" : "NO SIGNAL"; if (slug === "synthetic-arbitrage") { const displayEdge = syntheticDisplayEdge(row, edgeRaw); const state = typeof row.state === "string" && row.state ? row.state.replaceAll("_", " ") : displayEdge !== null && displayEdge > 0 ? "EDGE" : "NO EDGE"; const reason = typeof row.reason === "string" && row.reason ? " · " + row.reason.replaceAll("_", " ") : ""; return state + reason; } return edge > 0 ? "EDGE" : "NO EDGE"; } return formatValue(raw);
}

async function copyTextWithFallback(text: string): Promise<boolean> { if (!text) return false; try { if (navigator.clipboard?.writeText && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; } } catch {} try { const area = document.createElement("textarea"); area.value = text; area.setAttribute("readonly", ""); area.style.position = "fixed"; area.style.opacity = "0"; document.body.appendChild(area); area.select(); area.setSelectionRange(0, area.value.length); const ok = document.execCommand("copy"); area.remove(); return ok; } catch { return false; } }

function historyCell(slug: string, column: string, row: Row) { if (slug === "synthetic-arbitrage" && column === "Time") { const observed = row.observed_at; if (observed !== undefined && observed !== null && observed !== "") { const date = new Date(String(observed)); if (!Number.isNaN(date.getTime())) return date.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }); } } return pairCell(slug, column, row); }
