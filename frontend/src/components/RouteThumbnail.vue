<script setup lang="ts">
/**
 * The tiny route-shape glyph in the route list: the shape of the ride, and nothing else.
 *
 * It deliberately carries **no quality colour**. It is a glyph, not a chart - no axes, no
 * legend, no hover layer - so a colour ramp here would be the only channel, at 40 px, for
 * a reader who has to tell "fine" from "soaked" at a glance. The quality is text instead:
 * the caption beside the glyph (`RouteListPanel.qualityLabel`) and the `aria-label` here.
 *
 * The one thing the stroke still distinguishes is *data presence*: neutral grey means no
 * usable forecast for that stretch, which is a fact about the data rather than a reading
 * of the weather. The map route line keeps the YlOrRd ramp, where a legend, the popup's
 * Fahrqualität line and the section list back it up.
 */
import { computed } from "vue";
import type { RecurringRouteOut } from "@norain/api/models";

import { NO_DATA_COLOR, rideScore, rideScoreLabel } from "@/utils/rideQuality";
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

const points = computed(() => projectPath(thumbnail.value?.path ?? [], props.size));

/** One `<polyline>` per span between consecutive samples: theme ink, or grey where unknown. */
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
        // The score decides only whether this stretch is *known*, not what colour it gets.
        // Grey the moment either end is unknown, rather than implying weather we don't have.
        const known = !stale.value && s0 != null && s1 != null;
        const color = known ? "currentColor" : NO_DATA_COLOR;

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
    /* The known-forecast spans stroke `currentColor`, so the ink follows the Quasar theme
       var and flips with dark mode on its own - no second JS path watching $q.dark. */
    color: var(--q-primary);
}
</style>
