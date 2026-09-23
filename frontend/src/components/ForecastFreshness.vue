<script setup lang="ts">
import { computed } from "vue";
import { symSharpWarning } from "@quasar/extras/material-symbols-sharp";

/**
 * How current the forecast on screen is, while it is being renewed or the renewal failed.
 *
 * A refreshing job hands the page its previous result at once (flagged stale by the server),
 * so the page shows that instead of a spinner. It must not pass for the current forecast,
 * so this says when it was computed and what is happening to it.
 */
const props = defineProps<{
    computedAt?: string | null;
    refreshing: boolean;
    failed: boolean;
    percent?: number;
}>();

const stand = computed(() => {
    if (!props.computedAt) return "";
    const at = new Date(props.computedAt);
    if (Number.isNaN(at.getTime())) return "";
    const zone = { timeZone: "Europe/Zurich" } as const;
    const sameDay =
        at.toLocaleDateString("de-CH", zone) === new Date().toLocaleDateString("de-CH", zone);
    const clock = at.toLocaleTimeString("de-CH", { ...zone, hour: "2-digit", minute: "2-digit" });
    return sameDay ? clock : `${at.toLocaleDateString("de-CH", { ...zone, day: "2-digit", month: "2-digit" })} ${clock}`;
});
</script>

<template>
    <div
        v-if="failed || refreshing"
        class="row items-center no-wrap q-gutter-x-xs text-caption"
        :class="failed ? 'text-negative' : 'text-muted'"
        role="status"
    >
        <q-icon v-if="failed" :name="symSharpWarning" size="1rem" />
        <q-spinner-dots v-else size="1rem" color="primary" />
        <span v-if="failed">Aktualisierung fehlgeschlagen</span>
        <span v-else>
            Wird aktualisiert<template v-if="percent !== undefined"> ({{ percent }} %)</template>…
        </span>
        <span v-if="stand">· Stand {{ stand }}</span>
    </div>
</template>
