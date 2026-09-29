<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";

import { authApi, pendingFlows, signedIn } from "@/services/auth";
import { prewarmRecognition } from "@/services/browserRecognition";
import { ApiError } from "@/services/http";

import { samePassword } from "./passwordRules";

/**
 * Everything a signed-out visitor can do: sign in (password or emailed code), start a
 * sign-up and confirm it with the mailed code, reset a password. Also follows the reset
 * link in allauth's mail (`?reset_key=`). Emits `signed-in` once allauth reports a session;
 * the page then decides whether step 2 of sign-up is still due.
 */
const emit = defineEmits<{ "signed-in": [] }>();

const { t } = useI18n();
const route = useRoute();
const router = useRouter();

/** `verify` is the second half of `signup`: the code from the sign-up mail. */
type Mode = "login" | "signup" | "verify" | "code" | "reset";
const initialMode = route.query.mode;
const mode = ref<Mode>(
    initialMode === "signup" || initialMode === "reset" || initialMode === "code" ? initialMode : "login",
);
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
    if (resetKey.value) return t("auth.title.newPassword");
    return {
        login: t("auth.title.login"),
        signup: t("auth.title.signup"),
        verify: t("auth.title.verify"),
        code: t("auth.title.code"),
        reset: t("auth.title.reset"),
    }[mode.value];
});

function switchTo(next: Mode) {
    mode.value = next;
    codeSent.value = false;
    code.value = "";
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
            awaitCode(t("auth.verifyFirst"));
        } else {
            error.value = t("auth.loginFailed");
        }
    }, t("auth.loginFailed"));

/** allauth has mailed a sign-up code and waits for it in this session. */
function awaitCode(intro: string) {
    const sent = identifier.value.includes("@")
        ? t("auth.codeSentTo", { email: identifier.value })
        : t("auth.codeSent");
    switchTo("verify");
    message.value = `${intro} ${sent}`;
}

const signUp = () =>
    run(async () => {
        const reply = await authApi.signup(identifier.value);
        if (signedIn(reply)) {
            emit("signed-in");
            return;
        }
        // The reply is the same whether or not the address already has an account; an
        // owner gets a mail saying so instead of a code.
        awaitCode(t("auth.almostThere"));
    }, t("auth.signupFailed"));

/** The code only counts in this browser: allauth keeps the pending sign-up in the session. */
const verifyCode = () =>
    run(async () => {
        let reply;
        try {
            reply = await authApi.verifyEmailCode(code.value.trim());
        } catch (err) {
            if (err instanceof ApiError && err.status === 409) {
                // Too many wrong codes, or the sign-up expired. The mailbox can still be
                // proven with a sign-in code, which also verifies the address.
                switchTo("code");
                error.value = t("auth.codeExpired");
                return;
            }
            if (err instanceof ApiError && err.status === 400) {
                error.value = t("auth.codeWrong");
                return;
            }
            throw err;
        }
        if (signedIn(reply)) emit("signed-in");
        else error.value = t("auth.codeWrong");
    }, t("auth.verifyFailed"));

const resendCode = () =>
    run(async () => {
        try {
            await authApi.resendEmailCode();
        } catch (err) {
            if (err instanceof ApiError && err.status === 429) {
                error.value = t("auth.waitBeforeResend");
                return;
            }
            if (err instanceof ApiError && err.status === 409) {
                switchTo("code");
                error.value = t("auth.noMoreCodes");
                return;
            }
            throw err;
        }
        code.value = "";
        message.value = t("auth.newCodeSent");
    }, t("auth.codeSendFailed"));

const sendCode = () =>
    run(async () => {
        if (signedIn(await authApi.requestLoginCode(identifier.value))) {
            emit("signed-in");
            return;
        }
        codeSent.value = true;
        message.value = t("auth.loginCodeSent", { email: identifier.value });
    }, t("auth.codeSendFailed"));

const confirmCode = () =>
    run(async () => {
        const reply = await authApi.confirmLoginCode(code.value.trim());
        if (signedIn(reply)) emit("signed-in");
        else error.value = t("auth.codeInvalid");
    }, t("auth.codeInvalid"));

const requestReset = () =>
    run(async () => {
        await authApi.requestPasswordReset(identifier.value);
        message.value = t("auth.resetSent");
    }, t("auth.requestFailed"));

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
                error.value = t("auth.resetLinkInvalid");
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
        message.value = t("auth.passwordChanged");
    }, t("auth.passwordChangeFailed"));

function submit() {
    if (mode.value === "login") void signIn();
    else if (mode.value === "signup") void signUp();
    else if (mode.value === "verify") void verifyCode();
    else if (mode.value === "code") void (codeSent.value ? confirmCode() : sendCode());
    else void requestReset();
}

const submitLabel = computed(() => {
    if (mode.value === "code") return codeSent.value ? t("auth.submit.login") : t("auth.submit.sendCode");
    return {
        login: t("auth.submit.login"),
        signup: t("auth.submit.next"),
        verify: t("auth.submit.confirm"),
        reset: t("auth.submit.requestLink"),
    }[mode.value];
});

onMounted(async () => {
    // Sign-up and sign-in codes are limited per browser; recognise it before the click.
    prewarmRecognition();
    // Sign-up mails used to carry a link. Those links no longer work; a sign-in code
    // verifies the address just as well.
    if (typeof route.query.verify_key === "string") {
        await router.replace({ query: {} });
        switchTo("code");
        message.value = t("auth.oldLink");
    }
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
            :label="t('auth.title.newPassword')"
            autocomplete="new-password"
            outlined
            :rules="[v => !!v || t('common.required')]"
        />
        <q-input
            v-model="passwordRepeat"
            type="password"
            :label="t('signup.passwordRepeat')"
            autocomplete="new-password"
            outlined
            :rules="repeatRules"
        />
        <q-btn type="submit" color="primary" :label="t('auth.savePassword')" :loading="submitting" />
    </q-form>

    <template v-else>
        <q-form class="" @submit.prevent="submit">
            <q-input
                v-if="mode !== 'verify'"
                v-model="identifier"
                :type="mode === 'login' ? 'text' : 'email'"
                :label="mode === 'login' ? t('auth.emailOrUsername') : t('auth.email')"
                :hint="mode === 'signup' ? t('auth.signupHint') : undefined"
                :readonly="mode === 'code' && codeSent"
                autocomplete="username"
                outlined
                :rules="[v => !!v || t('common.required')]"
            />
            <q-input
                v-if="mode === 'login'"
                v-model="password"
                type="password"
:label="t('auth.password')"
                autocomplete="current-password"
                outlined
                :rules="[v => !!v || t('common.required')]"
            />
            <q-input
                v-if="mode === 'verify' || (mode === 'code' && codeSent)"
                v-model="code"
:label="t('auth.codeFromMail')"
                autocomplete="one-time-code"
                outlined
                :rules="[v => !!v || t('common.required')]"
            />
            <q-btn type="submit" no-caps class="fit" color="primary" :label="submitLabel" :loading="submitting" />
        </q-form>

        <div class="q-mt-md q-gutter-sm">
            <q-btn
                v-if="mode === 'verify'"
                flat
                no-caps
                :label="t('auth.resend')"
                :disable="submitting"
                @click="resendCode"
            />
            <q-btn v-if="mode !== 'login'" flat no-caps :label="t('auth.toLogin')" @click="switchTo('login')" />
            <q-btn v-if="mode !== 'code'" flat no-caps :label="t('auth.withCode')" @click="switchTo('code')" />
            <q-btn
                v-if="mode !== 'signup' && mode !== 'verify'"
                flat
                no-caps
                :label="t('auth.title.signup')"
                @click="switchTo('signup')"
            />
            <q-btn v-if="mode !== 'reset'" flat no-caps :label="t('auth.forgot')" @click="switchTo('reset')" />
        </div>
    </template>
</template>
