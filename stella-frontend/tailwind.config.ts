import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#EEEDFE",
          100: "#CECBF6",
          200: "#AFA9EC",
          400: "#7F77DD",
          600: "#534AB7",
          800: "#3C3489",
          900: "#26215C",
        },
        rose: {
          50: "#FBEAF0",
          100: "#F4C0D1",
          200: "#ED93B1",
          400: "#D4537E",
          600: "#993556",
          800: "#72243E",
        },
        mint: {
          50: "#E1F5EE",
          200: "#5DCAA5",
          400: "#1D9E75",
          600: "#0F6E56",
          800: "#085041",
        },
        ink: {
          400: "#888780",
          600: "#5F5E5A",
          800: "#444441",
          900: "#2C2C2A",
        },
      },
      borderRadius: {
        card: "16px",
      },
    },
  },
  plugins: [],
};
export default config;
