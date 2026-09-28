<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";
import { useI18n } from "vue-i18n";

import { authApi, type BikeProfile, type SessionState } from "@/services/auth";
import { currentLocale } from "@/i18n";
import { BIKE_PROFILE_OPTIONS } from "@/utils/bikeProfiles";

import { samePassword } from "./passwordRules";

/**
 * Step 2 of sign-up. The email is verified and the user is signed in, but the account
 * still has the placeholder username allauth generated. The app keeps the user here until
 * this form succeeds. The password is optional: without one, the account signs in by
 * emailed code.
 */
const props = defineProps<{ email: string; defaultProfile: BikeProfile }>();
const emit = defineEmits<{ completed: [session: SessionState] }>();
const { t } = useI18n();

/** How long typing has to pause before the username is checked. */
const CHECK_DELAY_MS = 400;

/** The email's local part, cut to what a username may hold. Only the owner sees it here. */
function suggestUsername(email: string): string {
    return (email.split("@")[0] ?? "").replace(/[^\w.+-]/g, "").slice(0, 150);
}

const username = ref(suggestUsername(props.email));
const profile = ref<BikeProfile>(props.defaultProfile);
const password = ref("");
const passwordRepeat = ref("");
const repeatRules = [samePassword(password)];
const error = ref("");
const submitting = ref(false);

/** The live check's verdict for the name in the field; empty while unknown or fine. */
const usernameProblem = ref("");
const checking = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let latest = 0;

async function check(name: string) {
    const ticket = ++latest;
    checking.value = true;
    try {
        const result = await authApi.usernameAvailable(name);
        if (ticket === latest) usernameProblem.value = result.available ? "" : result.detail;
    } catch {
        // The save checks again; a failed or throttled live check just says nothing.
        if (ticket === latest) usernameProblem.value = "";
    } finally {
        if (ticket === latest) checking.value = false;
    }
}

watch(
    username,
    name => {
        clearTimeout(timer);
        usernameProblem.value = "";
        const trimmed = name.trim();
        if (!trimmed) return;
        timer = setTimeout(() => void check(trimmed), CHECK_DELAY_MS);
    },
    { immediate: true },
);
onBeforeUnmount(() => {
    clearTimeout(timer);
});

async function submit() {
    error.value = "";
    submitting.value = true;
    try {
        emit("completed", await authApi.completeSignup(username.value, password.value, profile.value, currentLocale()));
    } catch (err) {
        error.value = err instanceof Error ? err.message : t("signup.completeFailed");
    } finally {
        submitting.value = false;
    }
}
</script>

<template>
    <div class="text-h6 q-mb-sm">{{ t("signup.almostDone") }}</div>
    <p class="text-body2 q-mb-md">{{ t("signup.verifiedIntro") }}</p>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md" dense>{{ error }}</q-banner>

    <q-form class="q-gutter-md" @submit.prevent="submit">
        <q-input
            v-model="username"
            :label="t('signup.username')"
            :hint="t('signup.usernameHint')"
            autocomplete="username"
            outlined
            :loading="checking"
            :error="!!usernameProblem"
            :error-message="usernameProblem"
            :rules="[v => !!v.trim() || t('common.required')]"
        />

        <div>
            <div class="text-body2 q-mb-xs">{{ t("signup.profileQuestion") }}</div>
            <q-btn-toggle
                v-model="profile"
                :options="BIKE_PROFILE_OPTIONS"
                toggle-color="primary"
                spread
                no-caps
                :aria-label="t('account.defaultProfile')"
            />
            <div class="text-caption text-grey q-mt-xs">
                {{ t("signup.profileHint") }}
            </div>
        </div>

        <q-input
            v-model="password"
            type="password"
            :label="t('signup.passwordOptional')"
            :hint="t('signup.passwordHint')"
            autocomplete="new-password"
            outlined
        />
        <q-input
            v-if="password"
            v-model="passwordRepeat"
            type="password"
            :label="t('signup.passwordRepeat')"
            autocomplete="new-password"
            outlined
            :rules="repeatRules"
        />
        <q-btn
            type="submit"
            color="primary"
            :label="t('signup.submit')"
            :loading="submitting"
            :disable="!!usernameProblem"
        />
    </q-form>
</template>
