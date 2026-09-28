<script setup lang="ts">
import { watch } from "vue";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { symSharpDarkMode, symSharpLightMode, symSharpSettings } from "@quasar/extras/material-symbols-sharp";
import { toggleDark } from "@/utils/theme";
import { useSession } from "@/composables/useSession";
import { applyLocale } from "@/composables/useLocale";
import { useRoute } from "vue-router";
import LanguageSwitcher from "@/components/LanguageSwitcher.vue";

const $q = useQuasar();
const { t, locale } = useI18n();
const route = useRoute();
const { isAuthenticated, session } = useSession();

// Signing in (or another tab changing it) brings the account's language along: it wins over
// the browser's and the last choice made while signed out.
watch(
    () => session.value.user?.language,
    language => {
        if (language && language !== locale.value) void applyLocale(language);
    },
);
</script>

<template>
    <q-layout view="hHh LpR lFf">
        <!-- A navy gradient into the brand colour in light mode; Quasar's dark surface in dark
             mode, where white text on the lighter dark-mode primary would be hard to read. -->
        <q-header v-if="!route.meta.bare" :class="$q.dark.isActive ? 'bg-dark' : 'bg-brand-gradient'">
            <q-toolbar class="q-px-xs-none row overflow-hidden" style="height: 50px">
                <router-link to="/routes" class="text-white q-ma-none q-pa-none full-height row" style="">
                    <img
                        src="/brand/mark-master%20-%20Copy.png"
                        :alt="t('app.logoAlt')"
                        class="q-ma-none full-height"
                        style="translate: -10px 3px"
                    />
                    <span class="text-subtitle1 self-center text-weight-bold">MeteoLane</span>
                </router-link>
                <q-space />
                <q-btn v-if="session.system" flat dense no-caps size="sm" to="/system" :label="t('app.system')" />
                <!--                <NavTabs v-if="!$q.screen.lt.sm" />-->
                <LanguageSwitcher dense class="q-mx-xs" />
                <q-btn
                    flat
                    dense
                    no-caps
                    size="sm"
                    to="/account"
                    :icon="symSharpSettings"
                    :aria-label="t('app.account')"
                >
                    <q-tooltip v-if="isAuthenticated">{{ session.user?.email }}</q-tooltip>
                </q-btn>
                <q-btn
                    flat
                    round
                    dense
                    size="sm"
                    class="q-ma-none q-pa-none"
                    :icon="$q.dark.isActive ? symSharpLightMode : symSharpDarkMode"
                    :aria-label="$q.dark.isActive ? t('app.lightTheme') : t('app.darkTheme')"
                    @click="toggleDark"
                >
                    <q-tooltip>{{ $q.dark.isActive ? t("app.lightTheme") : t("app.darkTheme") }}</q-tooltip>
                </q-btn>
            </q-toolbar>
            <!--            <NavTabs v-if="$q.screen.lt.sm" compact />-->
        </q-header>
        <q-page-container class="">
            <router-view />
        </q-page-container>
    </q-layout>
</template>

<style lang="scss">
@import "quasar/src/css/index.sass";
@import "quasar/src/css/flex-addon.sass";
</style>
