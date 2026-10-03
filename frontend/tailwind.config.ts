import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        algo: {
          bg: "#FFFFFF",
          surface: "#FFFFFF",
          card: "#FFFFFF",
          primary: "#00B0FF",
          profit: "#00A95C",
          loss: "#E11D48",
          warning: "#D97706",
          border: "#D7E0E8",
          muted: "#64748B",
        },
      },
      boxShadow: {
        panel: "0 10px 30px rgba(15,23,42,.08)",
      },
    },
  },
  plugins: [],
};

export default config;
