/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#f5fbfa",
          100: "#d8f0eb",
          200: "#afe1d7",
          300: "#7ccabf",
          400: "#45ab9d",
          500: "#268d82",
          600: "#1d7068",
          700: "#1a5a55",
          800: "#184947",
          900: "#173d3b"
        }
      },
      boxShadow: {
        soft: "0 20px 50px rgba(18, 38, 34, 0.12)"
      }
    },
  },
  plugins: [],
};
