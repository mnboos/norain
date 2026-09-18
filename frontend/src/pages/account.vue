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
import { billingApi } from "@/services/billing";
import { useSession } from "@/composables/useSession";
import { useEntitlements } from "@/composables/useEntitlements";

const route = useRoute();
const router = useRouter();
const { isAuthenticated, session, setSession, refreshSession } = useSession();
const { entitlements, isPro, maxRoutes } = useEntitlements();
const billingBusy = ref(false);

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

/** Both Stripe flows hand back a hosted URL; the browser leaves the SPA to reach it. */
async function openBilling(flow: "checkout" | "portal") {
    error.value = "";
    billingBusy.value = true;
    try {
        window.location.href = (await billingApi[flow]()).url;
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Die Zahlungsseite ist nicht erreichbar.";
        billingBusy.value = false;
    }
}

function planCaption(): string {
    const e = entitlements.value;
    if (!e) return "";
    const routes = maxRoutes.value == null ? "unbegrenzt viele Routen" : `${e.routeCount}/${maxRoutes.value} Routen`;
    const spread = e.ensembleUncertainty ? "mit Wetterbereich" : "ohne Wetterbereich";
    return `${routes} · ${spread}`;
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
        <q-card class="col-12 q-pa-lg" style="max-width: 430px">
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

                <div v-if="entitlements" class="q-mb-md">
                    <div class="row items-center justify-between">
                        <span class="text-subtitle2">Tarif</span>
                        <q-chip :color="isPro ? 'primary' : 'grey-6'" text-color="white" dense square>
                            {{ isPro ? "Pro" : "Free" }}
                        </q-chip>
                    </div>
                    <div class="text-caption text-grey">{{ planCaption() }}</div>
                    <div v-if="entitlements.cancelAtPeriodEnd" class="text-caption text-warning">
                        Wird zum Ende der Laufzeit gekündigt.
                    </div>
                </div>

                <div class="q-gutter-sm q-mb-md">
                    <q-btn
                        v-if="entitlements?.billingConfigured && !isPro"
                        color="primary"
                        label="Upgrade auf Pro"
                        :loading="billingBusy"
                        unelevated
                        @click="openBilling('checkout')"
                    />
                    <q-btn
                        v-if="entitlements?.billingConfigured && isPro"
                        color="primary"
                        label="Abo verwalten"
                        :loading="billingBusy"
                        outline
                        @click="openBilling('portal')"
                    />
                </div>

                <q-btn color="primary" label="Abmelden" flat @click="signOut" />
            </template>

            <template v-else>
                <SignInForms @signed-in="onSignedIn" />
            </template>
        </q-card>
    </q-page>
</template>
