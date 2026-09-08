import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies /api to FastAPI on 8000.
//
// CORS middleware on the API was the alternative and was declined: it
// adds a PRODUCTION surface to solve a DEVELOPMENT problem. At M6 the
// frontend is served from the same origin as the API, so the middleware
// would exist only to be reasoned about in a security review, for no
// benefit. A proxy is dev-only config and disappears in the build.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: false,
      },
    },
  },
});
