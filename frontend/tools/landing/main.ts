import "../../src/assets/main.css";
import "../../src/assets/theme.scss";
import "@fontsource-variable/lexend";
import "quasar/src/css/index.sass";
import "quasar/src/css/flex-addon.sass";
import { createApp } from "vue";
import { Quasar, Dialog, Notify } from "quasar";
import iconSet from "quasar/icon-set/svg-material-symbols-sharp";
import { VueQueryPlugin } from "@tanstack/vue-query";
import { i18n } from "@/i18n";
import "@/utils/theme";
import Studio from "./LandingStudio.vue";

i18n.global.locale.value = new URLSearchParams(location.search).get("lang") === "en" ? "en" : "de";
createApp(Studio)
    .use(i18n)
    .use(VueQueryPlugin)
    .use(Quasar, {
        iconSet,
        plugins: { Dialog, Notify },
        config: { dark: false },
    })
    .mount("#app");
