/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0f172a",
        panel: "#111827",
        line: "#1f2937",
        brand: { DEFAULT: "#f59e0b", soft: "#fbbf24" },
      },
      fontFamily: { pixel: ['"Press Start 2P"', "monospace"] },
    },
  },
  plugins: [],
};
