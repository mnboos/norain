import { computed, ref } from "vue";

import { authApi, type SessionState } from "@/services/auth";

const session = ref<SessionState>({ authenticated: false, user: null });
const sessionLoaded = ref(false);

export function useSession() {
    const isAuthenticated = computed(() => session.value.authenticated);

    async function refreshSession() {
        try {
            session.value = await authApi.session();
        } catch {
            session.value = { authenticated: false, user: null };
        } finally {
            sessionLoaded.value = true;
        }
        return session.value;
    }

    function setSession(value: SessionState) {
        session.value = value;
        sessionLoaded.value = true;
    }

    return { session, sessionLoaded, isAuthenticated, refreshSession, setSession };
}
