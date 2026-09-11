<script setup lang="ts">
import { useQuasar } from "quasar";
import { symSharpDarkMode, symSharpLightMode } from "@quasar/extras/material-symbols-sharp";
import { VueQueryDevtools } from "@tanstack/vue-query-devtools";
import { toggleDark } from "@/utils/theme";

const $q = useQuasar();
</script>

<template>
    <q-layout view="hHh LpR lFf">
        <!-- Brand blue in light mode; Quasar's dark surface in dark mode, where white text on
             the lighter dark-mode primary would be hard to read. -->
        <q-header elevated :class="$q.dark.isActive ? 'bg-dark' : 'bg-primary'">
            <q-toolbar>
                <q-toolbar-title>NoRain</q-toolbar-title>
                <q-tabs>
                    <q-route-tab to="/" label="Dashboard" />
                    <q-route-tab to="/map" label="Karte" />
                </q-tabs>
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
        </q-header>
        <q-page-container>
            <router-view />
            <VueQueryDevtools />
        </q-page-container>
    </q-layout>
</template>
