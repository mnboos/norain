<script setup lang="ts">
/**
 * The tiny route-shape glyph in the route list: the path the ride takes, painted with the
 * same ride-quality ramp the full map uses.
 *
 * It is a glyph, not a chart - no axes, no legend, no hover layer. At 40 px there is room
 * for exactly one thing: the shape, coloured. Everything the colour says is repeated as
 * text by the caller (and in the `aria-label` here), because the Spectral ramp is
 * red-green and must never be the only channel - see the note at the top of
 * `rideQuality.ts`.
 */
import { computed } from "vue";
import type { RecurringRouteOut } from "@norain/api/models";

import { NO_DATA_COLOR, rideScore, rideScoreLabel, scoreColor } from "@/utils/rideQuality";
import { pointsAttr, projectPath } from "@/utils/routeThumbnail";

/** Only the three fields the glyph reads, so callers and tests need not build a whole route. */
export type ThumbnailRoute = Pick<RecurringRouteOut, "thumbnail" | "nextDeparture" | "hasGeometry">;

const props = withDefaults(defineProps<{ route: ThumbnailRoute; size?: number }>(), { size: 40 });

const thumbnail = computed(() => props.route.thumbnail ?? null);

/**
 * A thumbnail is computed for one departure and frozen; `nextDeparture` is recomputed on
 * every request. Once the ride it describes has passed, the row rolls on to the next
 * departure and the stored colours no longer describe it - so drop them rather than show
 * yesterday's weather as today's.
 */
const stale = computed(() => {
    const t = thumbnail.value;
    // A route with no next departure at all also counts: whatever the glyph was computed
    // for is not a ride that is still coming.
    return !!t?.departure && t.departure !== props.route.nextDeparture;
});

const points = computed(() => projectPath(thumbnail.value?.path ?? [], props.size));

/** One `<polyline>` per span between consecutive samples, with its colour. */
const spans = computed(() => {
    const t = thumbnail.value;
    const pts = points.value;
    const samples = t?.samples ?? [];
    if (pts.length < 2) return [];

    // No samples at all: still draw the shape, in the neutral "no data" grey.
    if (samples.length < 2) {
        return [{ points: pointsAttr(pts), color: NO_DATA_COLOR }];
    }

    const scores = samples.map(s => (s ? (rideScore(s)?.score ?? null) : null));
    const out: { points: string; color: string }[] = [];

    for (let k = 0; k < samples.length - 1; k++) {
        const a = samples[k];
        const b = samples[k + 1];
        const from = a?.i ?? 0;
        // A missing sample has no vertex of its own; span to the next known one.
        const to = b?.i ?? pts.length - 1;
        if (to <= from) continue;

        const s0 = scores[k] ?? null;
        const s1 = scores[k + 1] ?? null;
        // Worse-of-the-two, so a wet stretch is never hidden by the dry end of its span;
        // grey the moment either end is unknown, rather than blending into invented data.
        const color = stale.value || s0 == null || s1 == null ? NO_DATA_COLOR : scoreColor(Math.max(s0, s1));

        // Slice inclusive of both endpoints so consecutive spans share a vertex and the
        // line has no gaps at the joins.
        out.push({ points: pointsAttr(pts.slice(from, to + 1)), color });
    }
    return out;
});

/** The worst scoring sample - what the label names, since that is what spoils a ride. */
const worst = computed(() => {
    if (stale.value) return null;
    const scored = (thumbnail.value?.samples ?? []).flatMap(s => (s ? (rideScore(s) ?? []) : []));
    return scored.reduce<(typeof scored)[number] | null>((a, b) => (a == null || b.score > a.score ? b : a), null);
});

const label = computed(() => {
    if (!props.route.hasGeometry) return "Route wird noch berechnet";
    if (!thumbnail.value || stale.value) return "Fahrqualität: Noch keine Prognose";
    return `Fahrqualität: ${rideScoreLabel(worst.value)}`;
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
        <template v-if="spans.length">
            <polyline
                v-for="(span, i) in spans"
                :key="i"
                :points="span.points"
                :stroke="span.color"
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
}
</style>
