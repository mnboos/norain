<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { symSharpWarning } from "@quasar/extras/material-symbols-sharp";

import { intlLocale } from "@/i18n";

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

const { t } = useI18n();

const stand = computed(() => {
    if (!props.computedAt) return "";
    const at = new Date(props.computedAt);
    if (Number.isNaN(at.getTime())) return "";
    const zone = { timeZone: "Europe/Zurich" } as const;
    // intlLocale() reads the app locale, so this recomputes on a switch.
    const tag = intlLocale();
    const sameDay = at.toLocaleDateString(tag, zone) === new Date().toLocaleDateString(tag, zone);
    const clock = at.toLocaleTimeString(tag, { ...zone, hour: "2-digit", minute: "2-digit" });
    return sameDay ? clock : `${at.toLocaleDateString(tag, { ...zone, day: "2-digit", month: "2-digit" })} ${clock}`;
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
        <span v-if="failed">{{ t("freshness.failed") }}</span>
        <span v-else>
            {{ percent !== undefined ? t("freshness.refreshingPercent", { percent }) : t("freshness.refreshing") }}
        </span>
        <span v-if="stand">· {{ t("freshness.asOf", { time: stand }) }}</span>
    </div>
</template>
