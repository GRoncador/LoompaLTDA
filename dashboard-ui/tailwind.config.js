/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // surfaces from dark to light (12/10 dark scheme): the page, a block on it, a card inside a
        // block, and that card under the pointer; each step reads apart from the one below it
        ink: "#0a0d12",
        panel: "#13171e",
        raised: { DEFAULT: "#1b2029", hover: "#242a34" },
        line: "#272d38",
        brand: { DEFAULT: "#f59e0b", soft: "#fbbf24" },
      },
      fontFamily: { pixel: ['"Press Start 2P"', "monospace"] },
    },
  },
  plugins: [],
};
