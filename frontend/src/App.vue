<script setup lang="ts">
import { useQuasar } from "quasar";
import {
    symSharpDarkMode,
    symSharpLightMode,
    symSharpPedalBike,
    symSharpSettings,
} from "@quasar/extras/material-symbols-sharp";
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
            <q-toolbar class="q-px-xs-none q-px-md-sm q-py-none">
                <q-toolbar-title class="text-subtitle1 text-weight-bold">
                    <q-icon :name="symSharpPedalBike" size="sm" class="q-mr-sm" />
                    Brisavia
                </q-toolbar-title>
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
