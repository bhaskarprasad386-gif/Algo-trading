export const appConfig = {
  name: "Algo Trading",
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "",
  wsUrl: process.env.NEXT_PUBLIC_WS_URL ?? "",
  liveOrdersEnabled: false,
} as const;
