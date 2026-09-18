import "./assets/main.css";

import { createApp } from "vue";
import App from "./App.vue";
import * as Sentry from "@sentry/vue";
import router from "./router";
import { Quasar, Dialog, Dark, LocalStorage, Notify } from "quasar";
import quasarLang from "quasar/lang/de-CH";
import { VueQueryPlugin } from "@tanstack/vue-query";
import { Configuration, DefaultConfig, type Middleware, type RequestContext } from "@norain/api/runtime";
import quasarIconSet from "quasar/icon-set/svg-material-symbols-sharp";
// Import icon libraries
// import "@quasar/extras/material-symbols-sharp/material-symbols-sharp.css";
// Self-hosted, so no request goes to Google Fonts
import "@fontsource-variable/lexend";

// Import Quasar css, built from its Sass sources so quasar-variables.scss applies
// import "quasar/src/css/index.sass";
// // The CSS addon: breakpoint variants of the flex, spacing and visibility classes
// import "quasar/src/css/flex-addon.sass";
import { getCookie, useBackendHost } from "@/utils";
import { useSession } from "@/composables/useSession";
import { initialDarkConfig } from "@/utils/theme";
import { backendTraceTargets } from "@/services/tracing";

const app = createApp(App);

/**
 * Small middleware to add appropriate headers depending on the HTTP method
 * before starting the API call.
 */
export class AppropriateOptionsMiddleware implements Middleware {
    /**
     * Called before executing the request.
     * @param context
     */
    pre(context: RequestContext) {
        const init = context.init;

        const currentHeaders =
            Array.isArray(init.headers) || init.headers instanceof Headers
                ? Object.fromEntries(init.headers)
                : init.headers;

        context.init = {
            ...init,
            headers: {
                ...currentHeaders,
                "X-CSRFToken": getCookie("csrftoken") ?? "",
                "Content-Type": "application/json",
            },
        };
        return Promise.resolve({ url: context.url, init: context.init });
    }
}

const backendHost = useBackendHost();
DefaultConfig.config = new Configuration({
    basePath: backendHost,
    credentials: "include",
    middleware: [new AppropriateOptionsMiddleware()],
});

Sentry.init({
    app,
    dsn: import.meta.env.VITE_SENTRY_DSN_FRONTEND,
    environment: import.meta.env.MODE,
    sendDefaultPii: true,
    enableLogs: true,
    enableMetrics: true,
    tracePropagationTargets: backendTraceTargets(backendHost),
    integrations: [
        Sentry.browserTracingIntegration({ router }),
        Sentry.replayIntegration(),
        Sentry.replayCanvasIntegration(),
        Sentry.vueIntegration({
            app,
            tracingOptions: { trackComponents: true, hooks: ["mount", "update", "unmount"] },
        }),
        Sentry.browserProfilingIntegration(),
        Sentry.browserSessionIntegration(),
        Sentry.captureConsoleIntegration({ levels: ["warn", "error", "assert"] }),
        Sentry.contextLinesIntegration(),
        Sentry.extraErrorDataIntegration(),
        // 503 is left out: the billing endpoints answer it on purpose when Stripe is not configured.
        Sentry.httpClientIntegration({
            failedRequestStatusCodes: [
                [500, 502],
                [504, 599],
            ],
        }),
        Sentry.reportingObserverIntegration(),
        Sentry.feedbackIntegration({
            colorScheme: "system",
        }),
    ],

    // Set tracesSampleRate to 1.0 to capture 100%
    // of transactions for performance monitoring.
    // We recommend adjusting this value in production
    // tracesSampleRate: import.meta.env.PROD ? 0.8 : 1.0,
    tracesSampleRate: 1.0,

    // Profile every traced session; needs Caddy's `Document-Policy: js-profiling` header.
    profileSessionSampleRate: 1.0,
    profileLifecycle: "trace",

    // Capture Replay for 10% of all sessions,
    // plus for 100% of sessions with an error
    replaysSessionSampleRate: 0.1,
    replaysOnErrorSampleRate: 1.0,

    release: import.meta.env.VITE_VUE_APP_VERSION ?? undefined,
});

// Install the router after tracing so its initial navigation is instrumented too.
app.use(router);
app.use(VueQueryPlugin);
app.use(Quasar, {
    plugins: { Dialog, Dark, LocalStorage, Notify },
    lang: quasarLang,
    iconSet: quasarIconSet,
    config: { dark: initialDarkConfig() },
});

async function bootstrap() {
    await useSession().refreshSession();
    app.mount("#app");
}

void bootstrap();
