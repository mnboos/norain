<script setup lang="ts">
/**
 * The tiny route-shape glyph in the route list: the shape of the ride in a single colour,
 * the YlOrRd ramp colour (`scoreColor`) of the server's `rideScore` for its worst sample -
 * the one the caption names. The scoring itself never reaches the browser.
 * At 40 px a per-stretch ramp is too small to read; one verdict per ride is what the list
 * is for, and the map shows where along the route it changes.
 *
 * The ramp's pale good end (`#ffeda0`) all but disappears at this size, so the line sits on
 * the same theme-flipping casing the map uses (`CASING_*`). The glyph has no legend and no
 * hover, so the colour is never the only channel: the caption beside it
 * (`RouteListPanel.qualityLabel`) and the `aria-label` here say the quality in words.
 *
 * Neutral grey means no usable forecast - a fact about the data, not a reading of the
 * weather - and it is deliberately off the warm ramp.
 */
import { computed } from "vue";
import type { RecurringRouteOut } from "@norain/api/models";

import {
    CASING_DARK,
    CASING_LIGHT,
    CASING_OPACITY,
    NO_DATA_COLOR,
    scoreColor,
} from "@/utils/rideQuality";
import { pointsAttr, projectPath } from "@/utils/routeThumbnail";

/** Only the three fields the glyph reads, so callers and tests need not build a whole route. */
export type ThumbnailRoute = Pick<RecurringRouteOut, "thumbnail" | "nextDeparture" | "hasGeometry">;

const props = withDefaults(defineProps<{ route: ThumbnailRoute; size?: number }>(), { size: 40 });

const thumbnail = computed(() => props.route.thumbnail ?? null);

/**
 * A thumbnail is computed for one departure and frozen; `nextDeparture` is recomputed on
 * every request. Once the ride it describes has passed, the row rolls on to the next
 * departure and the stored samples no longer describe it - so fall back to grey rather
 * than present yesterday's weather as today's.
 */
const stale = computed(() => {
    const t = thumbnail.value;
    // A route with no next departure at all also counts: whatever the glyph was computed
    // for is not a ride that is still coming.
    return !!t?.departure && t.departure !== props.route.nextDeparture;
});

/** `"x,y x,y ..."` of the projected path, or `null` when there is no line to draw. */
const line = computed(() => {
    const pts = projectPath(thumbnail.value?.path ?? [], props.size);
    return pts.length < 2 ? null : pointsAttr(pts);
});

/**
 * One colour for the whole line: the server's score for the worst sample, the one the caption
 * names. Grey when nothing is known - stale, or no sample the server could score.
 */
const color = computed(() => (stale.value ? NO_DATA_COLOR : scoreColor(thumbnail.value?.rideScore)));

const label = computed(() => {
    if (!props.route.hasGeometry) return "Route wird noch berechnet";
    if (!thumbnail.value || stale.value) return "Fahrqualität: Noch keine Prognose";
    return `Fahrqualität: ${thumbnail.value.rideLabel ?? "Nicht verfügbar"}`;
});

defineExpose({ label });
</script>

<template>
    <svg
        :width="size"
        :height="size"
        :viewBox="`0 0 ${size} ${size}`"
        role="img"
        :aria-label="label"
        class="route-thumbnail"
    >
        <title>{{ label }}</title>
        <template v-if="line">
            <!-- Casing under the line, so the pale good end stays visible. -->
            <polyline
                data-casing
                :points="line"
                stroke="currentColor"
                :stroke-opacity="CASING_OPACITY"
                fill="none"
                stroke-width="3"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
            <polyline
                :points="line"
                :stroke="color"
                fill="none"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
        </template>
        <!-- No geometry yet: a placeholder mark, so the row keeps its shape. -->
        <circle v-else :cx="size / 2" :cy="size / 2" r="3" :fill="NO_DATA_COLOR" opacity="0.4" />
    </svg>
</template>

<style scoped>
.route-thumbnail {
    display: block;
    overflow: visible;
    /* The casing strokes `currentColor`, so its ink flips with Quasar's dark mode here in CSS -
       no second JS path watching $q.dark. */
    color: v-bind(CASING_LIGHT);
}
:global(.body--dark) .route-thumbnail {
    color: v-bind(CASING_DARK);
}
</style>
