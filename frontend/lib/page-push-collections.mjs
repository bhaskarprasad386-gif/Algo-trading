/** Apply keyed collection deltas safely and idempotently. */
export function applyCollectionMutations(state, mutations) {
  if (!Array.isArray(mutations)) return state;
  const next = { ...state };
  for (const raw of mutations) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const mutation = raw;
    const collection = mutation.collection;
    const keyField = mutation.key_field;
    if (
      typeof collection !== "string" || !collection.trim() ||
      typeof keyField !== "string" || !keyField.trim() ||
      ["__proto__", "prototype", "constructor"].includes(collection) ||
      ["__proto__", "prototype", "constructor"].includes(keyField) ||
      !["ADD", "UPDATE", "DELETE"].includes(mutation.operation)
    ) continue;
    const current = next[collection];
    if (!Array.isArray(current)) continue;
    const item = mutation.item && typeof mutation.item === "object" && !Array.isArray(mutation.item)
      ? mutation.item
      : undefined;
    const key = mutation.key ?? item?.[keyField];
    if ((typeof key !== "string" && typeof key !== "number") || !String(key).trim()) continue;
    if (mutation.operation !== "DELETE" && !item) continue;
    if (mutation.operation !== "DELETE") {
      const itemKey = item?.[keyField];
      if ((typeof itemKey !== "string" && typeof itemKey !== "number") || !String(itemKey).trim()) continue;
      if (String(itemKey) !== String(key)) continue;
    }
    const matchesKey = (row) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) return false;
      const rowKey = row[keyField];
      if ((typeof rowKey !== "string" && typeof rowKey !== "number") || !String(rowKey).trim()) return false;
      return String(rowKey) === String(key);
    };
    const findIndex = current.findIndex(matchesKey);
    const rows = [...current];
    if (mutation.operation === "DELETE") {
      next[collection] = rows.filter((row) => !matchesKey(row));
    } else if (mutation.operation === "ADD") {
      if (findIndex >= 0) {
        rows[findIndex] = item;
        next[collection] = rows.filter((row, index) => !matchesKey(row) || index === findIndex);
      } else {
        rows.push(item);
        next[collection] = rows;
      }
    } else if (mutation.operation === "UPDATE" && findIndex >= 0) {
      rows[findIndex] = { ...rows[findIndex], ...item };
      next[collection] = rows.filter((row, index) => !matchesKey(row) || index === findIndex);
    } else {
      next[collection] = rows;
    }
  }
  return next;
}
