import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // Only generate manifest — our custom SW handles push notifications
      selfDestroying: true,
      injectRegister: false,
      manifest: {
        name: "PolyBot Trading",
        short_name: "PolyBot",
        description: "Polymarket Trading Bot Dashboard",
        theme_color: "#0f1117",
        background_color: "#0f1117",
        display: "standalone",
        start_url: "/",
        icons: [
          {
            src: "/polybot-icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/polybot-icon-512.png",
            sizes: "512x512",
            type: "image/png",
          },
        ],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
