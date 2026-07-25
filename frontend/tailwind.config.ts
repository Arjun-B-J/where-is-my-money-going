import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Warm amber accent. Named `brand` rather than after the product so a
        // future rename does not mean touching every className.
        brand: {
          50: "#FFF7ED",
          100: "#FFEDD5",
          200: "#FED7AA",
          300: "#FDBA74",
          400: "#FB923C",
          500: "#F97316",
          600: "#EA580C",
          700: "#C2410C",
          800: "#9A3412",
          900: "#7C2D12",
        },
        ink: {
          DEFAULT: "#0F172A",
          soft: "#1E293B",
          muted: "#475569",
        },
        cream: "#FFFBF5",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "hsl(var(--primary-foreground))" },
        muted: { DEFAULT: "hsl(var(--muted))", foreground: "hsl(var(--muted-foreground))" },
        accent: { DEFAULT: "hsl(var(--accent))", foreground: "hsl(var(--accent-foreground))" },
        border: "hsl(var(--border))",
        ring: "hsl(var(--ring))",
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
      },
      keyframes: {
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.65" },
        },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        shimmer: "shimmer 2s linear infinite",
        "pulse-soft": "pulse-soft 2.4s ease-in-out infinite",
        "rise-in": "rise-in 0.5s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
      backgroundImage: {
        "warm-gradient": "linear-gradient(135deg, #FFFBF5 0%, #FFEDD5 100%)",
        "ink-gradient": "linear-gradient(150deg, #0F172A 0%, #1E293B 55%, #7C2D12 140%)",
      },
      boxShadow: {
        // Used by the tilting cards. Two layers so the lift reads as depth
        // rather than a flat drop shadow.
        depth: "0 1px 2px rgba(15,23,42,0.06), 0 12px 32px -12px rgba(15,23,42,0.18)",
        "depth-lg": "0 2px 4px rgba(15,23,42,0.06), 0 28px 60px -20px rgba(15,23,42,0.28)",
      },
    },
  },
  plugins: [],
};

export default config;
