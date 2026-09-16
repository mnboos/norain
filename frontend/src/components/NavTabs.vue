<script setup lang="ts">
import { symSharpList, symSharpChevronLeft, symSharpChevronRight } from "@quasar/extras/material-symbols-sharp";

// compact: the phone row under the toolbar (arrows, icons, no caps) instead of the toolbar tabs.
defineProps<{ compact?: boolean }>();

const tabs = [
    { to: "/", label: "Dashboard", icon: symSharpList },
    { to: "/map", label: "Karte" },
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
            :label="tab.label"
            :icon="compact ? tab.icon : undefined"
        />
    </q-tabs>
</template>
