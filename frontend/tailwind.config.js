/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#06090d",
          900: "#0a0f15",
          850: "#0d131b",
          800: "#111925",
          700: "#182231",
        },
        line: "#1c2633",
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      keyframes: {
        flash: {
          "0%": { backgroundColor: "rgba(56, 189, 248, 0.18)" },
          "100%": { backgroundColor: "transparent" },
        },
      },
      animation: {
        flash: "flash 1.4s ease-out",
      },
    },
  },
  plugins: [],
};
