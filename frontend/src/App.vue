<script setup lang="ts">
import { useQuasar } from "quasar";
import { symSharpDarkMode, symSharpLightMode } from "@quasar/extras/material-symbols-sharp";
import { VueQueryDevtools } from "@tanstack/vue-query-devtools";
import { toggleDark } from "@/utils/theme";
import { useSession } from "@/composables/useSession";

const $q = useQuasar();
const { isAuthenticated, session } = useSession();
</script>

<template>
    <q-layout view="hHh LpR lFf">
        <!-- Brand blue in light mode; Quasar's dark surface in dark mode, where white text on
             the lighter dark-mode primary would be hard to read. -->
        <q-header :class="$q.dark.isActive ? 'bg-dark' : 'bg-primary'">
            <q-toolbar class="q-px-lg">
                <q-toolbar-title class="text-subtitle1 text-weight-bold">NoRain</q-toolbar-title>
                <q-tabs v-if="!$q.screen.lt.sm" dense shrink>
                    <q-route-tab to="/" label="Dashboard" />
                    <q-route-tab to="/map" label="Karte" />
                </q-tabs>
                <q-btn
                    flat
                    dense
                    no-caps
                    size="sm"
                    to="/account"
                    :label="isAuthenticated ? session.user?.email : 'Anmelden'"
                    class="q-mx-sm"
                />
                <q-btn
                    flat
                    round
                    dense
                    :icon="$q.dark.isActive ? symSharpLightMode : symSharpDarkMode"
                    :aria-label="$q.dark.isActive ? 'Helles Design' : 'Dunkles Design'"
                    @click="toggleDark"
                >
                    <q-tooltip>{{ $q.dark.isActive ? "Helles Design" : "Dunkles Design" }}</q-tooltip>
                </q-btn>
            </q-toolbar>
            <q-tabs v-if="$q.screen.lt.sm" dense>
                <q-route-tab to="/" label="Dashboard" />
                <q-route-tab to="/map" label="Karte" />
            </q-tabs>
        </q-header>
        <q-page-container>
            <router-view />
            <VueQueryDevtools />
        </q-page-container>
    </q-layout>
</template>
