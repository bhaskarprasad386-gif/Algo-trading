export const appConfig = {
  name: "Algo Trading",
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "https://82.41.67.22",
  wsUrl: process.env.NEXT_PUBLIC_WS_URL ?? "wss://82.41.67.22",
  liveOrdersEnabled: false,
} as const;
