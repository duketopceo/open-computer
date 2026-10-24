import type { Config } from "tailwindcss";

// open-computer console — Perplexity Computer–shaped design tokens.
// Light "paper" canvas, teal accent, serif display for the ask, sans for
// the machinery. States are always text (never a spinner pretending).
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#faf9f6",
        ink: "#1a1a18",
        muted: "#6b6b64",
        line: "#e7e4dc",
        teal: "#0f766e",
        "teal-soft": "#f0fdfa",
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        display: [
          "Georgia",
          "ui-serif",
          "Times New Roman",
          "serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,.04), 0 8px 24px -12px rgba(16,24,40,.12)",
        composer:
          "0 1px 2px rgba(16,24,40,.05), 0 12px 40px -16px rgba(16,24,40,.18)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up .2s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;