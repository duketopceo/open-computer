import type { Config } from "tailwindcss";

// open-computer console: Tallyline Silver Lining tokens (KTD11). Colour names map to the
// --sl-* variables from services/opsview/static/silver-lining.css (imported in src/index.css),
// so server-rendered pages and the SPA share one palette in dark, light and system modes.
// Tailwind opacity modifiers do not work on these; use the -soft and -hover variants.
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "var(--sl-canvas)",
        "surface-1": "var(--sl-surface-1)",
        "surface-2": "var(--sl-surface-2)",
        "surface-3": "var(--sl-surface-3)",
        "surface-4": "var(--sl-surface-4)",
        ink: "var(--sl-ink)",
        "ink-muted": "var(--sl-ink-muted)",
        muted: "var(--sl-ink-subtle)",
        "ink-tertiary": "var(--sl-ink-tertiary)",
        line: "var(--sl-lining)",
        "line-faint": "var(--sl-lining-faint)",
        "line-strong": "var(--sl-lining-strong)",
        accent: "var(--sl-accent)",
        "accent-hover": "var(--sl-accent-hover)",
        "accent-soft": "var(--sl-accent-soft)",
        "on-accent": "var(--sl-on-accent)",
        urgent: "var(--sl-status-urgent)",
        "urgent-soft": "var(--sl-status-urgent-soft)",
        attention: "var(--sl-status-attention)",
        "attention-soft": "var(--sl-status-attention-soft)",
        healthy: "var(--sl-status-healthy)",
        "healthy-soft": "var(--sl-status-healthy-soft)",
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
        card: "0 1px 2px var(--sl-scrim), 0 8px 24px -12px var(--sl-overlay)",
        composer:
          "0 1px 2px var(--sl-scrim), 0 12px 40px -16px var(--sl-overlay)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "strip-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "run-line": {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up .2s ease-out",
        "strip-in": "strip-in 160ms ease-out",
        "run-line": "run-line 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;