import { applyCollectionMutations } from "./page-push-collections.mjs";

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

/** True when a REMOVE frame explicitly removes the named collection. */
export function isPagePushCollectionRemoved(event, removedKeys, collection) {
  return String(event ?? "").toUpperCase() === "REMOVE" &&
    Array.isArray(removedKeys) && removedKeys.includes(collection);
}


/** Apply REMOVE frames without allowing explicitly deleted collections to be reintroduced by the same stale payload. */
export function applyPagePushRemovePayload(previous, payload) {
  const source = payload && typeof payload === "object" && !Array.isArray(payload) ? payload : {};
  const removeKeys = Array.isArray(source.remove_keys)
    ? source.remove_keys.filter((key) => typeof key === "string")
    : Array.isArray(source.keys)
      ? source.keys.filter((key) => typeof key === "string")
      : [];
  const removed = new Set(removeKeys);
  const next = { ...(previous || {}), __event: "REMOVE" };
  for (const key of removed) delete next[key];
  for (const [key, value] of Object.entries(source)) {
    if (key !== "remove_keys" && key !== "keys" && !removed.has(key)) next[key] = value;
  }
  delete next.collection_changes;
  const mutations = Array.isArray(source.collection_changes)
    ? source.collection_changes.filter((mutation) =>
        mutation && typeof mutation === "object" && !Array.isArray(mutation) &&
        !removed.has(mutation.collection))
    : [];
  return applyCollectionMutations(next, mutations);
}

/** Normalize authoritative scanner SNAPSHOT: absent collections are empty, not retained stale state. */
export function scannerSnapshotState(snapshot) {
  const source = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
  const rows = Array.isArray(source.rows) ? source.rows.filter((item) => item && typeof item === "object" && !Array.isArray(item)) : [];
  const pairs = Array.isArray(source.pairs) ? source.pairs.filter((item) => item && typeof item === "object" && !Array.isArray(item)) : [];
  const stats = source.stats && typeof source.stats === "object" && !Array.isArray(source.stats) ? source.stats : {};
  const scanAt = typeof source.scan_at === "string" ? source.scan_at : typeof stats.last_observation_at === "string" ? stats.last_observation_at : null;
  return { rows, pairs, stats, scanAt };
}

/** True when an authoritative workspace snapshot omits a requested collection. */
export function workspaceSnapshotMissing(snapshot, collection) {
  const source = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
  if (collection === "live") return !(source.live && typeof source.live === "object" && Array.isArray(source.live.data));
  if (collection === "pairs") return !(source.pairs && typeof source.pairs === "object" && Array.isArray(source.pairs.data));
  if (collection === "diagnostics" || collection === "universe" || collection === "runtime_health") return !(source[collection] && typeof source[collection] === "object" && !Array.isArray(source[collection]));
  return false;
}

/** Missing Box Spread shadow diagnostics in a reconnect snapshot. */
export function boxShadowSnapshotMissing(snapshot) {
  const source = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
  return !(source.shadow && typeof source.shadow === "object" && !Array.isArray(source.shadow));
}

/** Missing live opportunity collection in a synthetic trace reconnect snapshot. */
export function syntheticTraceSnapshotMissing(snapshot, collection) {
  const source = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
  if (collection !== "live") return false;
  return !(source.live && typeof source.live === "object" && Array.isArray(source.live.data));
}

/** Missing state fields in an authoritative IV Skew Flip reconnect snapshot. */
export function ivSkewSnapshotMissing(snapshot, key) {
  const source = snapshot && typeof snapshot === "object" && !Array.isArray(snapshot) ? snapshot : {};
  return key === "runtime" || key === "basket_registry" ? !(source[key] && typeof source[key] === "object" && !Array.isArray(source[key])) : false;
}

/** Safely send a page-push control frame; callers must close/recover on false. */
export function sendPagePushFrame(socket, frame) {
  try {
    socket.send(JSON.stringify(frame));
    return true;
  } catch {
    return false;
  }
}
