<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { authApi, pendingFlows, signedIn } from "@/services/auth";
import { ApiError } from "@/services/http";

import { samePassword } from "./passwordRules";

/**
 * Everything a signed-out visitor can do: sign in (password or emailed code), start a
 * sign-up, reset a password. Also follows the links in allauth's mails
 * (`?verify_key=`, `?reset_key=`). Emits `signed-in` once allauth reports a session;
 * the page then decides whether step 2 of sign-up is still due.
 */
const emit = defineEmits<{ "signed-in": [] }>();

const route = useRoute();
const router = useRouter();

type Mode = "login" | "signup" | "code" | "reset";
const initialMode = route.query.mode;
const mode = ref<Mode>(initialMode === "signup" || initialMode === "reset" ? initialMode : "login");
/** Login takes an email *or* a username; every other form takes an email. */
const identifier = ref("");
const password = ref("");
const passwordRepeat = ref("");
const repeatRules = [samePassword(password)];
const code = ref("");
const codeSent = ref(false);
const message = ref("");
const error = ref("");
const submitting = ref(false);

const resetKey = computed(() => (typeof route.query.reset_key === "string" ? route.query.reset_key : null));

const title = computed(() => {
    if (resetKey.value) return "Neues Passwort";
    return { login: "Anmelden", signup: "Konto erstellen", code: "Mit Code anmelden", reset: "Passwort zurücksetzen" }[
        mode.value
    ];
});

function switchTo(next: Mode) {
    mode.value = next;
    codeSent.value = false;
    message.value = "";
    error.value = "";
}

async function run(action: () => Promise<void>, failure: string) {
    message.value = "";
    error.value = "";
    submitting.value = true;
    try {
        await action();
    } catch (err) {
        error.value = err instanceof Error ? err.message : failure;
    } finally {
        submitting.value = false;
    }
}

const signIn = () =>
    run(async () => {
        const reply = await authApi.login(identifier.value, password.value);
        if (signedIn(reply)) {
            emit("signed-in");
        } else if (pendingFlows(reply).includes("verify_email")) {
            message.value = "Bitte bestätige zuerst deine E-Mail-Adresse. Wir haben dir einen neuen Link geschickt.";
        } else {
            error.value = "Anmeldung fehlgeschlagen.";
        }
    }, "Anmeldung fehlgeschlagen.");

const signUp = () =>
    run(async () => {
        const reply = await authApi.signup(identifier.value);
        if (signedIn(reply)) {
            emit("signed-in");
            return;
        }
        // The reply is the same whether or not the address already has an account.
        message.value = `Wir haben dir eine E-Mail an ${identifier.value} geschickt. Öffne den Link darin, um weiterzumachen.`;
    }, "Registrierung fehlgeschlagen.");

const sendCode = () =>
    run(async () => {
        await authApi.requestLoginCode(identifier.value);
        codeSent.value = true;
        message.value = `Falls es ein Konto für ${identifier.value} gibt, ist ein Code unterwegs.`;
    }, "Der Code konnte nicht gesendet werden.");

const confirmCode = () =>
    run(async () => {
        const reply = await authApi.confirmLoginCode(code.value.trim());
        if (signedIn(reply)) emit("signed-in");
        else error.value = "Der Code ist ungültig oder abgelaufen.";
    }, "Der Code ist ungültig oder abgelaufen.");

const requestReset = () =>
    run(async () => {
        await authApi.requestPasswordReset(identifier.value);
        message.value = "Falls es ein Konto für diese Adresse gibt, haben wir dir einen Link geschickt.";
    }, "Anfrage fehlgeschlagen.");

const confirmReset = () =>
    run(async () => {
        const key = resetKey.value;
        if (!key) return;
        let reply;
        try {
            reply = await authApi.resetPassword(key, password.value);
        } catch (err) {
            // A weak password is shown as allauth words it. A bad or used link needs a new
            // one, and allauth's text for that is English.
            if (err instanceof ApiError && err.code === "invalid_password_reset") {
                await router.replace({ query: {} });
                switchTo("reset");
                error.value = "Der Link ist ungültig oder abgelaufen. Fordere einen neuen an.";
                return;
            }
            throw err;
        }
        await router.replace({ query: {} });
        password.value = "";
        passwordRepeat.value = "";
        if (signedIn(reply)) {
            emit("signed-in");
            return;
        }
        switchTo("login");
        message.value = "Passwort geändert. Du kannst dich jetzt anmelden.";
    }, "Passwort konnte nicht geändert werden.");

/**
 * The link from step 1. Opened in the browser that started the sign-up, allauth signs the
 * user in. Opened anywhere else, it only verifies the address, and the way in is a code.
 */
const confirmEmail = (key: string) =>
    run(async () => {
        const email = await authApi.verifyEmailInfo(key);
        const reply = await authApi.verifyEmail(key);
        await router.replace({ query: {} });
        if (signedIn(reply)) {
            emit("signed-in");
            return;
        }
        switchTo("code");
        identifier.value = email ?? "";
        message.value = "E-Mail bestätigt. Melde dich mit einem Code an, den wir dir per E-Mail schicken.";
    }, "E-Mail konnte nicht bestätigt werden.");

function submit() {
    if (mode.value === "login") void signIn();
    else if (mode.value === "signup") void signUp();
    else if (mode.value === "code") void (codeSent.value ? confirmCode() : sendCode());
    else void requestReset();
}

const submitLabel = computed(() => {
    if (mode.value === "code") return codeSent.value ? "Anmelden" : "Code senden";
    return { login: "Anmelden", signup: "Weiter", reset: "Link anfordern" }[mode.value];
});

onMounted(() => {
    if (typeof route.query.verify_key === "string") void confirmEmail(route.query.verify_key);
});
</script>

<template>
    <div class="text-h6 q-mb-md">{{ title }}</div>
    <q-banner v-if="message" class="bg-positive text-white q-mb-md" dense>{{ message }}</q-banner>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md" dense>{{ error }}</q-banner>

    <q-form v-if="resetKey" class="q-gutter-md" @submit.prevent="confirmReset">
        <q-input
            v-model="password"
            type="password"
            label="Neues Passwort"
            autocomplete="new-password"
            outlined
            :rules="[v => !!v || 'Pflichtfeld']"
        />
        <q-input
            v-model="passwordRepeat"
            type="password"
            label="Passwort wiederholen"
            autocomplete="new-password"
            outlined
            :rules="repeatRules"
        />
        <q-btn type="submit" color="primary" label="Passwort speichern" :loading="submitting" />
    </q-form>

    <template v-else>
        <q-form class="q-gutter-md" @submit.prevent="submit">
            <q-input
                v-model="identifier"
                :type="mode === 'login' ? 'text' : 'email'"
                :label="mode === 'login' ? 'E-Mail oder Benutzername' : 'E-Mail-Adresse'"
                :hint="mode === 'signup' ? 'Benutzername und Passwort wählst du nach der Bestätigung.' : undefined"
                :readonly="mode === 'code' && codeSent"
                autocomplete="username"
                outlined
                :rules="[v => !!v || 'Pflichtfeld']"
            />
            <q-input
                v-if="mode === 'login'"
                v-model="password"
                type="password"
                label="Passwort"
                autocomplete="current-password"
                outlined
                :rules="[v => !!v || 'Pflichtfeld']"
            />
            <q-input
                v-if="mode === 'code' && codeSent"
                v-model="code"
                label="Code aus der E-Mail"
                autocomplete="one-time-code"
                outlined
                :rules="[v => !!v || 'Pflichtfeld']"
            />
            <q-btn type="submit" color="primary" :label="submitLabel" :loading="submitting" />
        </q-form>

        <div class="q-mt-md q-gutter-sm">
            <q-btn v-if="mode !== 'login'" flat no-caps label="Zur Anmeldung" @click="switchTo('login')" />
            <q-btn v-if="mode !== 'code'" flat no-caps label="Mit Code per E-Mail anmelden" @click="switchTo('code')" />
            <q-btn v-if="mode !== 'signup'" flat no-caps label="Konto erstellen" @click="switchTo('signup')" />
            <q-btn v-if="mode !== 'reset'" flat no-caps label="Passwort vergessen?" @click="switchTo('reset')" />
        </div>
    </template>
</template>
