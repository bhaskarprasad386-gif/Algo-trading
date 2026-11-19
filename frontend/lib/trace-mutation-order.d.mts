export function trackTraceSave(registry: Map<string, Promise<Response>>, traceId: string, request: () => Promise<Response>): Promise<Response>;
export function waitForTraceSave(registry: Map<string, Promise<Response>>, traceId: string): Promise<void>;
