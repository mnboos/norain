<script setup lang="ts">
import { computed } from "vue";
import { useNow } from "@vueuse/core";
import { useI18n } from "vue-i18n";
import type { DepartureCandidate, DepartureComparison } from "@norain/api/models";
import { intlLocale, te } from "@/i18n";
import { rideLabelText } from "@/utils/levels";
import { scoreColor } from "@/utils/rideQuality";

const props = defineProps<{
    comparison: DepartureComparison;
    selectedTime?: string | null;
    betterOnly?: boolean;
}>();
const emit = defineEmits<{ select: [time: string]; reset: [] }>();
const { t } = useI18n();
const now = useNow({ interval: 15_000 });
const selected = computed(() => props.selectedTime ?? props.comparison.requestedTime);
const recommended = computed(() =>
    candidates.value.find(c => c.departureTime === props.comparison.recommendedTime && available(c)),
);
const candidates = computed(() => {
    if (!props.betterOnly) return props.comparison.candidates;
    const baseline = props.comparison.candidates.find(c => sameTime(c.departureTime, props.comparison.requestedTime));
    if (!baseline?.available || baseline.rideScore == null) return [];
    const baselineScore = baseline.rideScore;
    return props.comparison.candidates.filter(
        c => available(c) && c.rideScore != null && c.rideScore < baselineScore,
    );
});
// Rebuilt for the current language, so a switch reformats every time here.
const formatter = computed(
    () =>
        new Intl.DateTimeFormat(intlLocale(), {
            timeZone: "Europe/Zurich",
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        }),
);
const timeFormatter = computed(
    () =>
        new Intl.DateTimeFormat(intlLocale(), {
            timeZone: "Europe/Zurich",
            hour: "2-digit",
            minute: "2-digit",
            timeZoneName: "shortOffset",
        }),
);
const format = (time: string) => formatter.value.format(new Date(time));
/** The server's reason for the recommendation is a code; a newer one we don't know says nothing. */
const explanation = computed(() => {
    const key = `departures.explanation.${props.comparison.explanation}`;
    return te(key) ? t(key) : "";
});
const sameTime = (a: string, b: string) => Date.parse(a) === Date.parse(b);
function available(candidate: DepartureCandidate) {
    return candidate.available && Date.parse(candidate.departureTime) >= now.value.getTime();
}
function select(candidate: DepartureCandidate) {
    // Check the actual clock again: a tab may have been suspended since its last tick.
    if (!candidate.available || Date.parse(candidate.departureTime) < Date.now()) return;
    if (sameTime(candidate.departureTime, props.comparison.requestedTime)) emit("reset");
    else emit("select", candidate.departureTime);
}
</script>

<template>
    <section
        v-if="!betterOnly || candidates.length || selectedTime"
        :aria-label="t('departures.compare')"
        class="q-pa-sm departure-comparison"
    >
        <div class="text-subtitle2">{{ t("departures.title") }}</div>
        <div class="text-caption">
            {{ t("departures.window", { from: format(comparison.windowStart), to: format(comparison.windowEnd) }) }}
        </div>
        <p v-if="recommended" class="q-my-sm">
            {{
                t("departures.recommended", {
                    departure: format(recommended.departureTime),
                    arrival: format(recommended.arrivalTime),
                })
            }}
            <br />
            {{ explanation }}
        </p>
        <p v-else-if="!betterOnly" class="q-my-sm">{{ t("departures.nothing") }}</p>
        <div class="departure-timeline" :aria-label="t('departures.compared')">
            <button
                v-for="candidate in candidates"
                :key="candidate.departureTime"
                type="button"
                class="departure-option"
                :disabled="!available(candidate)"
                :aria-pressed="sameTime(candidate.departureTime, selected)"
                :style="{ borderTopColor: scoreColor(available(candidate) ? candidate.rideScore : null) }"
                @click="select(candidate)"
            >
                <strong>{{ timeFormatter.format(new Date(candidate.departureTime)) }}</strong>
                <span>
                    {{
                        available(candidate) && candidate.rideLabel
                            ? rideLabelText(candidate.rideLabel)
                            : t("common.notAvailable")
                    }}
                </span>
                <span v-if="sameTime(candidate.departureTime, comparison.requestedTime)">
                    {{ t("departures.requested") }}
                </span>
                <span v-if="candidate.departureTime === recommended?.departureTime">
                    {{ t("departures.recommendedBadge") }}
                </span>
                <span v-if="sameTime(candidate.departureTime, selected)">{{ t("departures.shown") }}</span>
            </button>
        </div>
        <button v-if="selectedTime" type="button" class="reset-time q-mt-sm" @click="emit('reset')">
            {{ t("departures.backToRequested") }}
        </button>
        <div class="text-caption text-muted q-mt-sm">
            {{ t("departures.steps") }}
        </div>
    </section>
</template>

<style scoped>
.departure-timeline {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    padding: 4px;
}
.departure-option {
    display: flex;
    flex-direction: column;
    flex: 0 0 112px;
    gap: 3px;
    padding: 8px;
    border: 1px solid currentColor;
    border-top: 7px solid;
    border-radius: 6px;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
}
.departure-option[aria-pressed="true"] {
    outline: 2px solid currentColor;
    outline-offset: 1px;
}
.departure-option:focus-visible,
.reset-time:focus-visible {
    outline: 3px solid var(--q-primary);
    outline-offset: 2px;
}
.departure-option:disabled {
    opacity: 0.6;
    cursor: default;
}
.reset-time {
    border: 0;
    background: transparent;
    color: var(--q-primary);
    text-decoration: underline;
    cursor: pointer;
    font: inherit;
}
</style>
