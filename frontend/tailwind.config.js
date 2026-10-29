/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        navy: {
          950: "#070d18",
          900: "#0b1220",
          800: "#111b2e",
          700: "#17243c",
          600: "#21324f",
        },
        accent: {
          50: "#f2efff",
          100: "#e6e0ff",
          200: "#cfc3ff",
          300: "#b09bff",
          400: "#8f6dff",
          500: "#7c5cff",
          600: "#6a45f5",
          700: "#5936d6",
          800: "#492dae",
          900: "#3b278a",
        },
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 3px rgba(15, 23, 42, 0.06)",
        pop: "0 12px 32px rgba(15, 23, 42, 0.14)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 160ms ease-out",
      },
    },
  },
  plugins: [],
};
