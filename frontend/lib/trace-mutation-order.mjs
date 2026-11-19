/** Track in-flight trace saves so a delete cannot overtake an earlier POST. */
export function trackTraceSave(registry, traceId, request) {
  const promise = Promise.resolve().then(request);
  registry.set(traceId, promise);
  const clear = () => {
    if (registry.get(traceId) === promise) registry.delete(traceId);
  };
  promise.then(clear, clear);
  return promise;
}

/** Wait for the save to settle; deletion still proceeds if saving failed. */
export async function waitForTraceSave(registry, traceId) {
  const pending = registry.get(traceId);
  if (pending) await pending.catch(() => undefined);
}
