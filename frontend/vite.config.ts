import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately: python -m tender_tracker serve (port 8765).
// Override with TT_API_URL if you run it elsewhere.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: { "/api": process.env.TT_API_URL ?? "http://127.0.0.1:8765" },
  },
});
