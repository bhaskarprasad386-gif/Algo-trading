"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeName = "white" | "black" | "blue" | "dark";

export const themeOptions: Array<{ id: ThemeName; label: string; symbol: string }> = [
  { id: "white", label: "White", symbol: "⚪" },
  { id: "black", label: "Black", symbol: "⚫" },
  { id: "blue", label: "Blue", symbol: "🔵" },
  { id: "dark", label: "Premium Dark", symbol: "🌑" },
];

const STORAGE_KEY = "algo-trading-theme";

const ThemeContext = createContext<{
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}>({
  theme: "white",
  setTheme: () => undefined,
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>("white");

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY) as ThemeName | null;
    const next = themeOptions.some((item) => item.id === saved) ? saved! : "white";
    setThemeState(next);
    document.documentElement.dataset.theme = next;
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ theme, setTheme: setThemeState }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
