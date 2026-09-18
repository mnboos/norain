import { fileURLToPath, URL } from "node:url";

import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import vueDevTools from "vite-plugin-vue-devtools";
import { quasar, transformAssetUrls } from "@quasar/vite-plugin";
import VueRouter from "vue-router/vite";
import { sentryVitePlugin } from "@sentry/vite-plugin";

// https://vite.dev/config/
export default defineConfig(({ command, mode }) => {
    // Ports come from the repository-root .env (FRONTEND_PORT, BACKEND_PORT), the same file
    // Django and Compose read. Unit tests ignore it, so their expected URLs don't depend on
    // whoever's machine they run on.
    const env =
        mode === "test" ? {} : { ...loadEnv(mode, fileURLToPath(new URL("..", import.meta.url)), ""), ...process.env };
    const frontendPort = Number(env.FRONTEND_PORT ?? 3000);
    const backendPort = Number(env.BACKEND_PORT ?? 8000);
    const backend = `http://127.0.0.1:${backendPort}`;
    // Source maps exist only to be uploaded to Sentry: without a token none are written, and
    // with one the plugin deletes them after the upload, so dist/ never ships a map.
    const uploadSourceMaps = command === "build" && Boolean(env.SENTRY_AUTH_TOKEN);

    return {
        define: {
            // The values of .env the client bundle gets: useBackendHost needs the port on
            // localhost; an empty DSN leaves Sentry off.
            "import.meta.env.VITE_BACKEND_PORT": JSON.stringify(backendPort),
            "import.meta.env.VITE_SENTRY_DSN_FRONTEND": JSON.stringify(env.SENTRY_DSN_FRONTEND ?? ""),
            "import.meta.env.VITE_VUE_APP_VERSION": JSON.stringify(env.SENTRY_RELEASE ?? ""),
        },
        build: {
            // "hidden": the maps are written but the bundles carry no sourceMappingURL.
            sourcemap: uploadSourceMaps ? "hidden" : false,
        },
        optimizeDeps: {
            include: ["plotly.js/lib/core"],
        },
        server: {
            host: "127.0.0.1",
            port: frontendPort,
            // A taken port must fail, not drift to the next one: CORS only allows FRONTEND_PORT.
            strictPort: true,
            proxy: {
                "/api": backend,
                "/admin": backend,
                "/accounts": backend,
                "/media": backend,
                "/static": backend,
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
                          launchEditor: "pycharm",
                      }),
                  ]
                : []),
            quasar({
                // A custom variables file is what makes the plugin compile Quasar's Sass sources;
                // without one it swaps them for the prebuilt dist/quasar.css.
                sassVariables: fileURLToPath(new URL("./src/assets/quasar-variables.scss", import.meta.url)),
            }),
            // Last, so it sees the final bundles.
            ...(uploadSourceMaps
                ? [
                      sentryVitePlugin({
                          org: env.SENTRY_ORG,
                          project: env.SENTRY_PROJECT_FRONTEND,
                          authToken: env.SENTRY_AUTH_TOKEN,
                          release: { name: env.SENTRY_RELEASE ?? undefined },
                          sourcemaps: { filesToDeleteAfterUpload: ["./dist/**/*.map"] },
                          telemetry: false,
                      }),
                  ]
                : []),
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
    };
});
