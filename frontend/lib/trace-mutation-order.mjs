/** Serialize trace POSTs by ID and keep the registry pointing at the newest queued save. */
export function trackTraceSave(registry, traceId, request) {
  const previous = registry.get(traceId);
  const promise = Promise.resolve(previous).catch(() => undefined).then(request);
  registry.set(traceId, promise);
  const clear = () => {
    if (registry.get(traceId) === promise) registry.delete(traceId);
  };
  promise.then(clear, clear);
  return promise;
}

/** Wait for all earlier saves for this ID; deletion proceeds even if save failed. */
export async function waitForTraceSave(registry, traceId) {
  const pending = registry.get(traceId);
  if (pending) await pending.catch(() => undefined);
}
