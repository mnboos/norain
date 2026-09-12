import "./assets/main.css";

import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";
import { Quasar, Dialog, Dark, LocalStorage } from "quasar";
import quasarLang from "quasar/lang/de-CH";
import quasarIconSet from "quasar/icon-set/material-symbols-sharp";
import { VueQueryPlugin } from "@tanstack/vue-query";
import { Configuration, DefaultConfig, type Middleware, type RequestContext } from "@norain/api/runtime";

// Import icon libraries
import "@quasar/extras/material-symbols-sharp/material-symbols-sharp.css";

// Import Quasar css
import "quasar/dist/quasar.css";
import { getCookie, useBackendHost } from "@/utils";
import { useSession } from "@/composables/useSession";
import { initialDarkConfig } from "@/utils/theme";

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

app.use(router);
app.use(VueQueryPlugin);
app.use(Quasar, {
    plugins: { Dialog, Dark, LocalStorage },
    lang: quasarLang,
    iconSet: quasarIconSet,
    config: { dark: initialDarkConfig() },
});

async function bootstrap() {
    await useSession().refreshSession();
    app.mount("#app");
}

void bootstrap();
