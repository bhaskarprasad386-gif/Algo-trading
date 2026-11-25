/**
 * Guard REST recovery responses against a newer WebSocket page update.
 * A pushed state increments the latest request sequence so late HTTP replies
 * cannot rewind the page.
 */
export function isCurrentRecoveryResponse({ active, requestSequence, latestSequence, responseValid }) {
  return active === true && Number.isSafeInteger(requestSequence) && requestSequence >= 0 &&
    Number.isSafeInteger(latestSequence) && latestSequence >= 0 &&
    requestSequence === latestSequence && responseValid === true;
}

/** Guard page-owned REST responses against superseding requests or PagePush updates. */
export function isCurrentPagePushRequest({ active, requestSequence, latestSequence }) {
  return active === true && Number.isSafeInteger(requestSequence) && requestSequence >= 0 &&
    Number.isSafeInteger(latestSequence) && latestSequence >= 0 &&
    requestSequence === latestSequence;
}


/** Merge durable alert rows by ID, with incoming rows replacing older versions. */
export function mergeUniqueAlerts(current, incoming, compare, limit = 100) {
  const merged = new Map();
  for (const row of [...(Array.isArray(current) ? current : []), ...(Array.isArray(incoming) ? incoming : [])]) {
    if (row && Number.isSafeInteger(row.id)) merged.set(row.id, row);
  }
  const safeLimit = Number.isFinite(limit) ? Math.max(0, Math.floor(limit)) : merged.size;
  return [...merged.values()].sort(compare).slice(0, safeLimit);
}

/** Return new early-entry alerts only; this helper intentionally does not mutate the seen-ID set. */
export function freshEarlyEntryAlerts(alerts, seenIds) {
  if (!Array.isArray(alerts)) return [];
  return alerts.filter(row => row && Number.isSafeInteger(row.id) &&
    row.state === "EARLY_ENTRY" && !seenIds.has(row.id));
}

/** Manual REST refresh must not override authoritative live PagePush state. */
export function shouldAllowBoxSpreadEdgeTimingRestRefresh(pagePushStatus, eventDriven = false) {
  return pagePushStatus !== "LIVE" || eventDriven === true;
}


/** Read an index's spot price from the authoritative IV runtime snapshot. */
export function runtimeSpotPrice(runtime, underlying) {
  const symbol = String(underlying ?? "").trim().toUpperCase();
  if (symbol !== "NIFTY" && symbol !== "BANKNIFTY") return null;
  const spot = runtime?.spot_feed?.spot?.[symbol]?.price;
  return typeof spot === "number" && Number.isFinite(spot) ? spot : null;
}
