export const appConfig = {
  name: "Algo Trading",
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "https://82.41.67.22",
  wsUrl: process.env.NEXT_PUBLIC_WS_URL ?? "wss://82.41.67.22",
  liveOrdersEnabled: false,
} as const;


/**
 * Keep the API host configured separately from the frontend host, but derive
 * the WebSocket scheme from the page scheme to prevent mixed-content sockets.
 */
export function getDashboardWsUrl(): string {
  if (typeof window === "undefined") {
    return appConfig.wsUrl.replace(/\/$/, "");
  }
  const url = new URL(appConfig.wsUrl, window.location.href);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString().replace(/\/$/, "");
}
