<script setup lang="ts">
/**
 * The two readings that decide whether you ride, beside each route in the list: rain and
 * frost. Everything shown here is the server's verdict - `rainLevel` and `frostLevel` are
 * words computed from the ride-quality curves in `core/ride_quality.py`, and the numbers are
 * the worst point of the ride. There is no threshold in this file, and there must not be:
 * a breakpoint in the bundle gives the curves away.
 *
 * Colour is never the only channel. Each reading carries a German title that repeats the
 * level in words, so an icon's emphasis is a second cue and not the message.
 *
 * A reading is drawn only when the server gave it a level, so an icon always means the
 * forecast has something to say: no snowflake on a mild day, no umbrella on a dry one, and
 * nothing at all on a row whose forecast is missing or has expired - the quality caption
 * beside it already says "Noch keine Prognose". Never a confident "kein Frost" over data we
 * do not have; with nothing to say this claims nothing.
 */
import { computed } from "vue";
import { symSharpAcUnit, symSharpRainy } from "@quasar/extras/material-symbols-sharp";

import { liveThumbnail } from "@/utils/routeThumbnail";
import type { ThumbnailRoute } from "@/utils/routeThumbnail";

const props = defineProps<{ route: ThumbnailRoute }>();

const thumbnail = computed(() => liveThumbnail(props.route));

/** How strongly a reading is drawn. The steps come from the server's own wording. */
const EMPHASIS: Record<string, string> = {
    leicht: "text-primary",
    mässig: "text-warning",
    stark: "text-negative",
};

interface Reading {
    key: string;
    icon: string;
    /** What the number means, in the reading's own words. */
    name: string;
    value: string | null;
    level: string | null;
    color: string;
}

function reading(key: string, icon: string, name: string, value: string | null, level: string | null | undefined): Reading {
    return {
        key,
        icon,
        name,
        value,
        level: level ?? null,
        color: (level ? EMPHASIS[level] : null) ?? "text-muted",
    };
}

const rain = computed<Reading>(() => {
    const t = thumbnail.value;
    // The chance of rain is the headline; without an ensemble there is none, and the amount
    // the main run predicts is the honest second best.
    // A percentage is a risk and millimetres are an amount, so the wording follows the number
    // rather than calling both "Regen".
    const chance = t?.rainProbability;
    const amount = t?.maxRainRateMmH;
    if (t != null && chance != null) {
        return reading("rain", symSharpRainy, "Regenrisiko", `${Math.round(chance * 100)} %`, t.rainLevel);
    }
    const value = t != null && amount != null ? `${amount.toFixed(1)} mm/h` : null;
    return reading("rain", symSharpRainy, "Regen", value, t?.rainLevel);
});

const frost = computed<Reading>(() => {
    const t = thumbnail.value;
    // Minus U+2212, not a hyphen: it is the glyph a negative temperature is written with.
    const degrees = t?.tempMin == null ? null : Math.round(t.tempMin);
    const value = degrees == null ? null : `${degrees < 0 ? "\u2212" : ""}${Math.abs(degrees)}\u00a0°C`;
    return reading("frost", symSharpAcUnit, "Frost", value, t?.frostLevel);
});

/**
 * Only what the forecast has something to say about. A level of `null` is the server saying
 * there is nothing worth naming, and a row with no usable thumbnail has no level either, so
 * both end up showing nothing rather than an icon that means "ignore me".
 */
const readings = computed(() => [rain.value, frost.value].filter(r => r.level !== null && r.value !== null));

/** The whole reading in words, for the title and the screen reader. */
function describe(r: Reading): string {
    return r.level ? `${r.name} ${r.value}, ${r.level}` : `${r.name} ${r.value}`;
}

defineExpose({ describe, readings });
</script>

<template>
    <!-- The caption line belongs to this component: with nothing to report there must be no
         empty line and no margin left behind it. -->
    <q-item-label v-if="readings.length" caption class="q-mt-xs">
        <span class="row items-center q-gutter-x-md">
            <span
                v-for="r in readings"
                :key="r.key"
                class="row items-center no-wrap"
                :class="r.color"
                :title="describe(r)"
                :aria-label="describe(r)"
            >
                <q-icon :name="r.icon" size="16px" class="q-mr-xs" />
                <span>{{ r.value }}</span>
            </span>
        </span>
    </q-item-label>
</template>
