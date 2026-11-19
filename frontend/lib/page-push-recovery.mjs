/**
 * Guard REST recovery responses against a newer WebSocket page update.
 * A pushed state increments the latest request sequence so late HTTP replies
 * cannot rewind the page.
 */
export function isCurrentRecoveryResponse({ active, requestSequence, latestSequence, responseValid }) {
  return active === true && Number.isSafeInteger(requestSequence) &&
    requestSequence === latestSequence && responseValid === true;
}

/** Reject delayed snapshots that would rewind an established page sequence. */
export function shouldAcceptPageSnapshot({ hasSnapshot, currentSequence, incomingSequence }) {
  return Number.isSafeInteger(incomingSequence) &&
    (hasSnapshot !== true || incomingSequence >= currentSequence);
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

/** Ignore callbacks delivered by a superseded WebSocket transport. */
export function isCurrentPagePushSocket(currentSocket, eventSocket) {
  return currentSocket !== null && currentSocket === eventSocket;
}

/** Reload durable history when its push revision is first observed or changes. */
export function shouldReloadPairHistory(currentRevision, nextRevision) {
  if (nextRevision === undefined || nextRevision === null) return false;
  const next = String(nextRevision);
  return currentRevision === null || currentRevision !== next;
}
