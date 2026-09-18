<route lang="json5">
{
    name: "account",
    meta: { title: "Konto" },
}
</route>

<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import CompleteSignupForm from "@/components/account/CompleteSignupForm.vue";
import SignInForms from "@/components/account/SignInForms.vue";
import { authApi, type SessionState } from "@/services/auth";
import PlanPanel from "@/components/account/PlanPanel.vue";
import { useSession } from "@/composables/useSession";

const route = useRoute();
const router = useRouter();
const { isAuthenticated, session, setSession, refreshSession } = useSession();

const error = ref("");

function nextPath() {
    const next = route.query.next;
    return typeof next === "string" && next.startsWith("/") ? next : "/";
}

/** allauth has signed the user in; step 2 of sign-up may still be due (the page shows it). */
async function onSignedIn() {
    const state = await refreshSession();
    if (state.user?.signupComplete) await router.push(nextPath());
}

async function onSignupCompleted(state: SessionState) {
    setSession(state);
    await router.push(nextPath());
}

async function signOut() {
    error.value = "";
    try {
        await authApi.logout();
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Abmelden fehlgeschlagen.";
        return;
    }
    setSession({ authenticated: false, user: null });
    await router.push("/account");
}
</script>

<template>
    <q-page class="row justify-center q-pa-md">
        <q-card class="col-12 q-pa-lg" style="max-width: 620px">
            <template v-if="isAuthenticated && session.user?.signupComplete === false">
                <CompleteSignupForm :suggested-username="session.user.username" @completed="onSignupCompleted" />
                <q-banner v-if="error" class="bg-negative text-white q-mt-md" dense>{{ error }}</q-banner>
                <q-btn class="q-mt-md" color="primary" label="Abmelden" flat @click="signOut" />
            </template>

            <template v-else-if="isAuthenticated">
                <div class="text-h6 q-mb-md">Angemeldet</div>
                <p class="q-mb-none">{{ session.user?.username }}</p>
                <p class="text-caption text-grey q-mb-md">{{ session.user?.email }}</p>

                <q-banner v-if="error" class="bg-negative text-white q-mb-md" dense>{{ error }}</q-banner>

                <q-separator class="q-my-md" />

                <PlanPanel class="q-mb-lg" />

                <q-btn color="primary" label="Abmelden" flat @click="signOut" />
            </template>

            <template v-else>
                <SignInForms @signed-in="onSignedIn" />
            </template>
        </q-card>
    </q-page>
</template>
