<script setup lang="ts">
import { useQuasar } from "quasar";
import { symSharpDarkMode, symSharpLightMode, symSharpSettings } from "@quasar/extras/material-symbols-sharp";
import { toggleDark } from "@/utils/theme";
import { useSession } from "@/composables/useSession";

const $q = useQuasar();
const { isAuthenticated, session } = useSession();
</script>

<template>
    <q-layout view="hHh LpR lFf">
        <!-- A navy gradient into the brand colour in light mode; Quasar's dark surface in dark
             mode, where white text on the lighter dark-mode primary would be hard to read. -->
        <q-header
            :class="{ 'bg-dark': $q.dark.isActive }"
            :style="$q.dark.isActive ? undefined : 'background: linear-gradient(90deg, #1b365d, #2d5a8e)'"
        >
            <q-toolbar class="q-px-xs-none row overflow-hidden" style="height: 50px">
                <router-link to="/" class="text-white q-ma-none q-pa-none full-height row" style="">
                    <img
                        src="/brand/mark-master%20-%20Copy.png"
                        alt="Brisavia Logo"
                        class="q-ma-none full-height"
                        style="translate: -10px 3px"
                    />
                    <span class="text-subtitle1 self-center text-weight-bold">Brisavia</span>
                </router-link>
                <q-space />
                <!--                <NavTabs v-if="!$q.screen.lt.sm" />-->
                <q-btn flat dense no-caps size="sm" to="/account" :icon="symSharpSettings">
                    <q-tooltip v-if="isAuthenticated">{{ session.user?.email }}</q-tooltip>
                </q-btn>
                <q-btn
                    flat
                    round
                    dense
                    size="sm"
                    class="q-ma-none q-pa-none"
                    :icon="$q.dark.isActive ? symSharpLightMode : symSharpDarkMode"
                    :aria-label="$q.dark.isActive ? 'Helles Design' : 'Dunkles Design'"
                    @click="toggleDark"
                >
                    <q-tooltip>{{ $q.dark.isActive ? "Helles Design" : "Dunkles Design" }}</q-tooltip>
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
