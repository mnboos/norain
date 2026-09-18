<script setup lang="ts">
import { ref } from "vue";

import { authApi, type SessionState } from "@/services/auth";

import { samePassword } from "./passwordRules";

/**
 * Step 2 of sign-up. The email is verified and the user is signed in, but the account
 * still has the username allauth generated and no password. The app keeps the user here
 * until this form succeeds.
 */
const props = defineProps<{ suggestedUsername: string }>();
const emit = defineEmits<{ completed: [session: SessionState] }>();

const username = ref(props.suggestedUsername);
const password = ref("");
const passwordRepeat = ref("");
const repeatRules = [samePassword(password)];
const error = ref("");
const submitting = ref(false);

async function submit() {
    error.value = "";
    submitting.value = true;
    try {
        emit("completed", await authApi.completeSignup(username.value, password.value));
    } catch (err) {
        error.value = err instanceof Error ? err.message : "Das Konto konnte nicht eingerichtet werden.";
    } finally {
        submitting.value = false;
    }
}
</script>

<template>
    <div class="text-h6 q-mb-sm">Fast fertig</div>
    <p class="text-body2 q-mb-md">
        Deine E-Mail-Adresse ist bestätigt. Wähle jetzt einen Benutzernamen und ein Passwort.
    </p>
    <q-banner v-if="error" class="bg-negative text-white q-mb-md" dense>{{ error }}</q-banner>

    <q-form class="q-gutter-md" @submit.prevent="submit">
        <q-input
            v-model="username"
            label="Benutzername"
            hint="Damit kannst du dich auch anmelden."
            autocomplete="username"
            outlined
            :rules="[v => !!v || 'Pflichtfeld']"
        />
        <q-input
            v-model="password"
            type="password"
            label="Passwort"
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
        <q-btn type="submit" color="primary" label="Konto einrichten" :loading="submitting" />
    </q-form>
</template>
