import { watch } from "vue";
import { createRouter, createWebHistory, type RouteLocationNormalized } from "vue-router";
import { routes } from "vue-router/auto-routes";

import { useSession } from "@/composables/useSession";
import { i18n, t, te } from "@/i18n";

const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    routes,
});

router.beforeEach(async to => {
    const { isAuthenticated, session, sessionLoaded, refreshSession } = useSession();
    if (to.meta.requiresAuth && !sessionLoaded.value) {
        await refreshSession();
    }
    if (to.meta.requiresAuth && !isAuthenticated.value) {
        return { path: "/account", query: { next: to.fullPath } };
    }
    if (to.meta.requiresSystem && !session.value.system) return { path: "/account" };
    // Between the two sign-up steps the account has a generated username and no password
    // of its own; nothing else opens until step 2 on /account is done.
    if (isAuthenticated.value && session.value.user?.signupComplete === false && to.name !== "account") {
        return { path: "/account", query: { next: to.fullPath } };
    }
    return true;
});

/** The tab title: the page's name, in the current language. A page may set a finer one itself. */
function applyTitle(to: RouteLocationNormalized) {
    const key = to.meta.titleKey;
    document.title = key && te(key) ? t("app.title", { page: t(key) }) : t("app.defaultTitle");
}

router.afterEach(to => {
    applyTitle(to);
});
watch(i18n.global.locale, () => {
    applyTitle(router.currentRoute.value);
});

export default router;
