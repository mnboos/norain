<route lang="json5">
{
    name: "account",
    meta: { titleKey: "pages.account" },
}
</route>

<script setup lang="ts">
import { ref } from "vue";
import GarminPanel from "@/components/account/GarminPanel.vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";

import CompleteSignupForm from "@/components/account/CompleteSignupForm.vue";
import SignInForms from "@/components/account/SignInForms.vue";
import { authApi, type BikeProfile, type SessionState } from "@/services/auth";
import { BIKE_PROFILE_OPTIONS } from "@/utils/bikeProfiles";
import PlanPanel from "@/components/account/PlanPanel.vue";
import LanguageSwitcher from "@/components/LanguageSwitcher.vue";
import { useSession } from "@/composables/useSession";

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const { isAuthenticated, session, setSession, refreshSession } = useSession();

const error = ref("");

function nextPath() {
    const next = route.query.next;
    return typeof next === "string" && next.startsWith("/") ? next : "/routes";
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

const savingProfile = ref(false);

async function changeProfile(value: BikeProfile) {
    error.value = "";
    savingProfile.value = true;
    try {
        setSession(await authApi.updateProfile({ defaultProfile: value }));
    } catch (err) {
        error.value = err instanceof Error ? err.message : t("account.saveFailed");
    } finally {
        savingProfile.value = false;
    }
}

async function signOut() {
    error.value = "";
    try {
        await authApi.logout();
    } catch (err) {
        error.value = err instanceof Error ? err.message : t("account.signOutFailed");
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
                <CompleteSignupForm
                    :email="session.user.email"
                    :default-profile="session.user.defaultProfile"
                    @completed="onSignupCompleted"
                />
                <q-banner v-if="error" class="bg-negative text-white q-mt-md" dense>{{ error }}</q-banner>
                <q-btn class="q-mt-md" color="primary" :label="t('account.signOut')" flat @click="signOut" />
            </template>

            <template v-else-if="isAuthenticated">
                <div class="text-h6 q-mb-md">{{ t("account.signedIn") }}</div>
                <p class="q-mb-none">{{ session.user?.username }}</p>
                <p class="text-caption text-grey q-mb-md">{{ session.user?.email }}</p>
                <p v-if="session.user?.hasPassword === false" class="text-caption q-mb-md">
                    {{ t("account.codeOnly") }}
                </p>

                <div v-if="session.user" class="q-mb-md">
                    <div class="text-body2 q-mb-xs">{{ t("account.defaultProfile") }}</div>
                    <q-btn-toggle
                        :model-value="session.user.defaultProfile"
                        :options="BIKE_PROFILE_OPTIONS"
                        :disable="savingProfile"
                        toggle-color="primary"
                        spread
                        no-caps
                        :aria-label="t('account.defaultProfile')"
                        @update:model-value="changeProfile"
                    />
                </div>

                <div class="q-mb-md">
                    <div class="text-body2 q-mb-xs">{{ t("language.label") }}</div>
                    <LanguageSwitcher />
                </div>

                <q-banner v-if="error" class="bg-negative text-white q-mb-md" dense>{{ error }}</q-banner>

                <q-separator class="q-my-md" />

                <PlanPanel class="q-mb-lg" />
                <GarminPanel />

                <q-btn color="primary" :label="t('account.signOut')" flat @click="signOut" />
            </template>

            <template v-else>
                <SignInForms @signed-in="onSignedIn" />
            </template>
        </q-card>
    </q-page>
</template>
