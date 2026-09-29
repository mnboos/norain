import { defineConfig } from "vite";

// Serve the framework-independent library without app plugins or developer-tool instrumentation.
export default defineConfig({
    optimizeDeps: { noDiscovery: true, include: [] },
    server: {
        host: "127.0.0.1",
        port: 8128,
        strictPort: true,
        proxy: { "/api": "http://127.0.0.1:8127" },
    },
});
