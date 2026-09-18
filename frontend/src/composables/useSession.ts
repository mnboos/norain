import { computed, ref } from "vue";

import { identifyUser } from "@/services/telemetry";
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
            identifyUser(session.value.user?.id);
            sessionLoaded.value = true;
        }
        return session.value;
    }

    function setSession(value: SessionState) {
        session.value = value;
        identifyUser(value.user?.id);
        sessionLoaded.value = true;
    }

    return { session, sessionLoaded, isAuthenticated, refreshSession, setSession };
}
