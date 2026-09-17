<script setup lang="ts">
import { computed } from "vue";
import { useNow } from "@vueuse/core";
import type { DepartureCandidate, DepartureComparison } from "@norain/api/models";
import { scoreColor } from "@/utils/rideQuality";

const props = defineProps<{ comparison: DepartureComparison; selectedTime?: string | null }>();
const emit = defineEmits<{ select: [time: string]; reset: [] }>();
const now = useNow({ interval: 15_000 });
const selected = computed(() => props.selectedTime ?? props.comparison.requestedTime);
const recommended = computed(() =>
    props.comparison.candidates.find(c => c.departureTime === props.comparison.recommendedTime && available(c)),
);
const formatter = new Intl.DateTimeFormat("de-CH", {
    timeZone: "Europe/Zurich",
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
});
const timeFormatter = new Intl.DateTimeFormat("de-CH", {
    timeZone: "Europe/Zurich",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "shortOffset",
});
const format = (time: string) => formatter.format(new Date(time));
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
    <section aria-label="Abfahrtszeiten vergleichen" class="q-pa-sm departure-comparison">
        <div class="text-subtitle2">Beste Abfahrtszeit</div>
        <div class="text-caption">
            Zeitfenster: {{ format(comparison.windowStart) }} – {{ format(comparison.windowEnd) }}
        </div>
        <p v-if="recommended" class="q-my-sm">
            Abfahrt etwa {{ format(recommended.departureTime) }} · Ankunft {{ format(recommended.arrivalTime) }}
            <br />
            {{ comparison.explanation }}
        </p>
        <p v-else class="q-my-sm">Nicht genügend Wetterdaten oder keine zukünftige Abfahrt im Zeitfenster.</p>
        <div class="departure-timeline" aria-label="Verglichene Abfahrtszeiten">
            <button
                v-for="candidate in comparison.candidates"
                :key="candidate.departureTime"
                type="button"
                class="departure-option"
                :disabled="!available(candidate)"
                :aria-pressed="sameTime(candidate.departureTime, selected)"
                :style="{ borderTopColor: scoreColor(available(candidate) ? candidate.rideScore : null) }"
                @click="select(candidate)"
            >
                <strong>{{ timeFormatter.format(new Date(candidate.departureTime)) }}</strong>
                <span>{{ available(candidate) ? candidate.rideLabel : "Nicht verfügbar" }}</span>
                <span v-if="sameTime(candidate.departureTime, comparison.requestedTime)">Gewünscht</span>
                <span v-if="candidate.departureTime === recommended?.departureTime">Empfohlen</span>
                <span v-if="sameTime(candidate.departureTime, selected)">Angezeigt</span>
            </button>
        </div>
        <button v-if="selectedTime" type="button" class="reset-time q-mt-sm" @click="emit('reset')">
            Zur gewünschten Abfahrtszeit
        </button>
        <div class="text-caption text-muted q-mt-sm">
            Vergleich in 15-Minuten-Schritten. Die Wetterprognose kann gröber sein.
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
