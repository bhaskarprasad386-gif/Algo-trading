"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeName = "white" | "dark";

export const themeOptions: Array<{ id: ThemeName; label: string; symbol: string }> = [
  { id: "dark", label: "Current Dark", symbol: "🌑" },
  { id: "white", label: "White", symbol: "⚪" },
];

const STORAGE_KEY = "algo-trading-theme";

const ThemeContext = createContext<{
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}>({
  theme: "dark",
  setTheme: () => undefined,
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeName>("dark");

  useEffect(() => {
    const saved = window.localStorage.getItem(STORAGE_KEY) as ThemeName | null;
    // Keep the current dark theme as the first-visit default, but respect either explicit choice.
    const next: ThemeName = themeOptions.some((item) => item.id === saved) ? saved! : "dark";
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
