import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        algo: {
          bg: "#070A0D",
          surface: "#0F1419",
          card: "#12171E",
          primary: "#00B0FF",
          profit: "#00E676",
          loss: "#FF1744",
          warning: "#FFAB00",
          border: "#202830",
          muted: "#7D8A96",
        },
      },
      boxShadow: {
        panel: "0 10px 30px rgba(0,0,0,.20)",
      },
    },
  },
  plugins: [],
};

export default config;
