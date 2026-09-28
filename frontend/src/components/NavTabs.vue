<script setup lang="ts">
import {
    symSharpList,
    symSharpChevronLeft,
    symSharpChevronRight,
    symSharpExplore,
} from "@quasar/extras/material-symbols-sharp";
import { useI18n } from "vue-i18n";

// compact: the phone row under the toolbar (arrows, icons, no caps) instead of the toolbar tabs.
defineProps<{ compact?: boolean }>();

const { t } = useI18n();
const tabs: { to: string; key: "nav.dashboard" | "nav.map" | "nav.explore"; icon?: string }[] = [
    { to: "/routes", key: "nav.dashboard", icon: symSharpList },
    { to: "/map", key: "nav.map" },
    { to: "/explore", key: "nav.explore", icon: symSharpExplore },
];
</script>

<template>
    <q-tabs
        dense
        shrink
        :narrow-indicator="compact"
        :outside-arrows="compact"
        :no-caps="compact"
        :left-icon="compact ? symSharpChevronLeft : undefined"
        :right-icon="compact ? symSharpChevronRight : undefined"
    >
        <q-route-tab
            v-for="tab in tabs"
            :key="tab.to"
            :to="tab.to"
            :label="t(tab.key)"
            :icon="compact ? tab.icon : undefined"
        />
    </q-tabs>
</template>
