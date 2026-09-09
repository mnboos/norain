<script setup lang="ts">
import { computed } from "vue";
import type { RouteForecastOut } from "@norain/api";

const props = defineProps<{
    forecast: RouteForecastOut;
}>();

const headline = computed(() => {
    const s = props.forecast.summary;
    if (!s.willRain) return "☀️ Kein Regen erwartet";
    if (s.firstRainEta) {
        const dt = new Date(s.firstRainEta);
        const time = dt.toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" });
        return `🌧️ Regen wahrscheinlich ab ca. ${time} Uhr`;
    }
    return "🌧️ Regen erwartet";
});

const distanceKm = computed(() => (props.forecast.totalDistanceM / 1000).toFixed(1));
const durationMin = computed(() => Math.round(props.forecast.totalSeconds / 60));
const rainProb = computed(() => {
    const p = props.forecast.summary.rainProbability;
    return p != null ? `${Math.round(p * 100)}%` : "—";
});
</script>

<template>
    <q-card :class="forecast.summary.willRain ? 'bg-blue-1' : 'bg-green-1'">
        <q-card-section>
            <div class="text-h6">{{ headline }}</div>
            <div class="row q-gutter-md q-mt-sm">
                <div class="col">
                    <div class="text-caption">Regenwahrscheinlichkeit</div>
                    <div class="text-h6">{{ rainProb }}</div>
                </div>
                <div class="col">
                    <div class="text-caption">Max. Niederschlag</div>
                    <div class="text-h6">{{ forecast.summary.maxRainMm }} mm</div>
                </div>
                <div class="col">
                    <div class="text-caption">Max. Gegenwind</div>
                    <div class="text-h6">{{ forecast.summary.maxHeadwind }} km/h</div>
                </div>
                <div class="col">
                    <div class="text-caption">Dauer</div>
                    <div class="text-h6">{{ durationMin }} min</div>
                </div>
                <div class="col">
                    <div class="text-caption">Distanz</div>
                    <div class="text-h6">{{ distanceKm }} km</div>
                </div>
            </div>
            <div class="text-caption q-mt-sm text-grey-7">Daten: {{ forecast.summary.source }}</div>
        </q-card-section>
    </q-card>
</template>
