<route lang="json5">
{
    name: "account",
    meta: { title: "Konto" },
}
</route>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { authApi } from "@/services/auth";
import { billingApi } from "@/services/billing";
import { useSession } from "@/composables/useSession";
import { useEntitlements } from "@/composables/useEntitlements";

const route = useRoute();
const router = useRouter();
const { isAuthenticated, session, setSession } = useSession();
const { entitlements, isPro, maxRoutes } = useEntitlements();
const billingBusy = ref(false);

const mode = ref<"login" | "signup" | "reset">("login");
/** Login takes an email *or* a username; signup and reset take an email only. */
const identifier = ref("");
const username = ref("");
const password = ref("");
const message = ref("");
const error = ref("");
const submitting = ref(false);

function clearFeedback() {
    message.value = "";
    error.value = "";
}

function nextPath() {
    const next = route.query.next;
    return typeof next === "string" && next.startsWith("/") ? next : "/";
}

async function signIn() {
    clearFeedback();
    submitting.value = true;
    try {
        setSession(await authApi.login(identifier.value, password.value));
        await router.push(nextPath());
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Anmeldung fehlgeschlagen.";
    } finally {
        submitting.value = false;
    }
}

async function signUp() {
    clearFeedback();
    submitting.value = true;
    try {
        message.value = (await authApi.signup(identifier.value, username.value, password.value)).detail;
        mode.value = "login";
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Registrierung fehlgeschlagen.";
    } finally {
        submitting.value = false;
    }
}

async function requestReset() {
    clearFeedback();
    submitting.value = true;
    try {
        message.value = (await authApi.passwordReset(identifier.value)).detail;
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Anfrage fehlgeschlagen.";
    } finally {
        submitting.value = false;
    }
}

async function confirmReset() {
    const uid = route.query.reset_uid;
    const token = route.query.reset_token;
    if (typeof uid !== "string" || typeof token !== "string") return;

    clearFeedback();
    submitting.value = true;
    try {
        message.value = (await authApi.passwordResetConfirm(uid, token, password.value)).detail;
        await router.replace({ query: {} });
        mode.value = "login";
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Passwort konnte nicht geändert werden.";
    } finally {
        submitting.value = false;
    }
}

async function confirmEmail() {
    const uid = route.query.uid;
    const token = route.query.token;
    if (typeof uid !== "string" || typeof token !== "string") return;

    clearFeedback();
    submitting.value = true;
    try {
        message.value = (await authApi.verifyEmail(uid, token)).detail;
        await router.replace({ query: {} });
    } catch (err) {
        error.value = err instanceof Error ? err.message : "E-Mail konnte nicht bestätigt werden.";
    } finally {
        submitting.value = false;
    }
}

/** Both Stripe flows hand back a hosted URL; the browser leaves the SPA to reach it. */
async function openBilling(flow: "checkout" | "portal") {
    clearFeedback();
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
    await authApi.logout();
    setSession({ authenticated: false, user: null });
    await router.push("/account");
}

onMounted(() => {
    if (route.query.uid && route.query.token) void confirmEmail();
});
</script>

<template>
    <q-page class="row justify-center q-pa-md">
        <q-card class="col-12 q-pa-lg" style="max-width: 430px">
            <template v-if="isAuthenticated">
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
                <div class="text-h6 q-mb-md">
                    {{ route.query.reset_uid ? "Neues Passwort" : mode === "login" ? "Anmelden" : mode === "signup" ? "Konto erstellen" : "Passwort zurücksetzen" }}
                </div>
                <q-banner v-if="message" class="bg-positive text-white q-mb-md" dense>{{ message }}</q-banner>
                <q-banner v-if="error" class="bg-negative text-white q-mb-md" dense>{{ error }}</q-banner>

                <q-form
                    v-if="route.query.reset_uid"
                    class="q-gutter-md"
                    @submit.prevent="confirmReset"
                >
                    <q-input v-model="password" type="password" label="Neues Passwort" outlined :rules="[v => !!v || 'Pflichtfeld']" />
                    <q-btn type="submit" color="primary" label="Passwort speichern" :loading="submitting" />
                </q-form>

                <q-form v-else class="q-gutter-md" @submit.prevent="mode === 'login' ? signIn() : mode === 'signup' ? signUp() : requestReset()">
                    <q-input
                        v-model="identifier"
                        :type="mode === 'login' ? 'text' : 'email'"
                        :label="mode === 'login' ? 'E-Mail oder Benutzername' : 'E-Mail-Adresse'"
                        autocomplete="username"
                        outlined
                        :rules="[v => !!v || 'Pflichtfeld']"
                    />
                    <q-input
                        v-if="mode === 'signup'"
                        v-model="username"
                        type="text"
                        label="Benutzername"
                        hint="Damit kannst du dich auch anmelden."
                        outlined
                        :rules="[v => !!v || 'Pflichtfeld']"
                    />
                    <q-input
                        v-if="mode !== 'reset'"
                        v-model="password"
                        type="password"
                        label="Passwort"
                        outlined
                        :rules="[v => !!v || 'Pflichtfeld']"
                    />
                    <q-btn
                        type="submit"
                        color="primary"
                        :label="mode === 'login' ? 'Anmelden' : mode === 'signup' ? 'Konto erstellen' : 'Link anfordern'"
                        :loading="submitting"
                    />
                </q-form>

                <div v-if="!route.query.reset_uid" class="q-mt-md q-gutter-sm">
                    <q-btn
                        v-if="mode !== 'login'"
                        flat
                        no-caps
                        label="Zur Anmeldung"
                        @click="mode = 'login'; clearFeedback()"
                    />
                    <q-btn
                        v-if="mode !== 'signup'"
                        flat
                        no-caps
                        label="Konto erstellen"
                        @click="mode = 'signup'; clearFeedback()"
                    />
                    <q-btn
                        v-if="mode !== 'reset'"
                        flat
                        no-caps
                        label="Passwort vergessen?"
                        @click="mode = 'reset'; clearFeedback()"
                    />
                </div>
            </template>
        </q-card>
    </q-page>
</template>
