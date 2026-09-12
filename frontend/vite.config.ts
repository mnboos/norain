import { fileURLToPath, URL } from "node:url";

import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import vueDevTools from "vite-plugin-vue-devtools";
import { quasar, transformAssetUrls } from "@quasar/vite-plugin";
import VueRouter from "vue-router/vite";

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
    optimizeDeps: {
        include: ["plotly.js/lib/core", "plotly.js/lib/bar"],
    },
    server: {
        host: "127.0.0.1",
        port: 3000,
        proxy: {
            "/api": "http://127.0.0.1:8000",
            "/admin": "http://127.0.0.1:8000",
            "/accounts": "http://127.0.0.1:8000",
            "/media": "http://127.0.0.1:8000",
            "/static": "http://127.0.0.1:8000",
        },
        warmup: {
            clientFiles: ["./src/pages/**/*.vue", "./src/components/**/*.vue"],
        },
    },
    plugins: [
        VueRouter({
            routesFolder: "src/pages",
            extensions: [".vue"],
            importMode: "async",
            // The plugin watches src/pages unless CI is set, which a one-shot build has no
            // use for — and on a machine whose fs.inotify.max_user_instances is already
            // exhausted (containers, IDEs) creating the watcher fails the build outright
            // with EMFILE.
            watch: command === "serve",
        }),
        vue({
            template: { transformAssetUrls },
        }),
        // Dev-only tooling; it has no place in a production bundle.
        ...(command === "serve"
            ? [
                  vueDevTools({
                      launchEditor: "C:\\Program Files\\JetBrains\\PyCharm 2025.2.4\\bin\\pycharm64.exe",
                  }),
              ]
            : []),
        quasar(),
    ],
    resolve: {
        alias: [
            { find: "@", replacement: fileURLToPath(new URL("./src", import.meta.url)) },
            {
                find: /^@norain\/api$/,
                replacement: fileURLToPath(new URL("../packages/api/index.ts", import.meta.url)),
            },
            {
                find: /^@norain\/api\/(.*)$/,
                replacement: fileURLToPath(new URL("../packages/api/$1", import.meta.url)),
            },
        ],
    },
}));
