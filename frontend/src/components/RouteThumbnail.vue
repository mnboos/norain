<script setup lang="ts">
/**
 * The tiny route-shape glyph in the route list: the shape of the ride in a single colour,
 * the rain-severity colour of the server's `rainLevel`. The overall ride score remains in the
 * caption; wind, temperature and frost do not recolour this rain marker.
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

import {
    CASING_DARK,
    CASING_LIGHT,
    CASING_OPACITY,
    NO_DATA_COLOR,
    rainLevelColor,
} from "@/utils/rideQuality";
import { liveThumbnail, pointsAttr, projectPath } from "@/utils/routeThumbnail";
import type { ThumbnailRoute } from "@/utils/routeThumbnail";

export type { ThumbnailRoute };

const props = withDefaults(defineProps<{ route: ThumbnailRoute; size?: number }>(), { size: 40 });

/** The thumbnail when it still describes the ride that is coming; see `liveThumbnail`. */
const thumbnail = computed(() => liveThumbnail(props.route));

/** `"x,y x,y ..."` of the projected path, or `null` when there is no line to draw. A stale
 * thumbnail still draws its shape - the route has not moved, only its weather has expired. */
const line = computed(() => {
    const pts = projectPath(props.route.thumbnail?.path ?? [], props.size);
    return pts.length < 2 ? null : pointsAttr(pts);
});

/**
 * One colour for the whole line: the server's score for the worst sample, the one the caption
 * names. Grey when nothing is known - stale, or no sample the server could score.
 */
// This glyph is the dashboard's rain marker: wind, temperature and frost do not tint it.
const color = computed(() => rainLevelColor(thumbnail.value?.rainLevel));

const label = computed(() => {
    if (!props.route.hasGeometry) return "Route wird noch berechnet";
    if (!thumbnail.value) return "Fahrqualität: Noch keine Prognose";
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
