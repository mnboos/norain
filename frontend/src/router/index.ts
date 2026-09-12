import { createRouter, createWebHistory } from "vue-router";
import { routes } from "vue-router/auto-routes";

import { useSession } from "@/composables/useSession";

const router = createRouter({
    history: createWebHistory(import.meta.env.BASE_URL),
    routes,
});

router.beforeEach(async to => {
    const { isAuthenticated, sessionLoaded, refreshSession } = useSession();
    if (to.meta.requiresAuth && !sessionLoaded.value) {
        await refreshSession();
    }
    if (to.meta.requiresAuth && !isAuthenticated.value) {
        return { path: "/account", query: { next: to.fullPath } };
    }
    return true;
});

export default router;
