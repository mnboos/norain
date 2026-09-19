<script setup lang="ts">
import { computed, ref, toRefs } from "vue";
import { useQuasar } from "quasar";
import type { RouteForecastOut } from "@norain/api/models";
import { forecastHeadline } from "@/utils/forecastDetails";
import WeatherGlyph from "@/components/WeatherGlyph.vue";
import WeatherSections from "@/components/WeatherSections.vue";
import { symSharpInfo } from "@quasar/extras/material-symbols-sharp";

const props = defineProps<{ forecast: RouteForecastOut }>();
const { forecast } = toRefs(props);
// Beside the text on a wide card; above it on a narrow one, where both would be squeezed.
const $q = useQuasar();
const horizontal = computed(() => $q.screen.width >= 1280 || $q.screen.lt.md);

const headline = computed(() => forecastHeadline(forecast.value.summary, forecast.value.samples));
const explanation = computed(() => {
    if (!forecast.value.samples.length) return "Für diese Fahrt liegen noch keine Wetterdaten vor.";
    const p = forecast.value.summary.rainProbability;
    if (p == null) return "Das Regenrisiko ist derzeit nicht verfügbar.";
    if (p < 0.1) return "";
    return `Höchstes Regenrisiko entlang der Strecke: ${Math.round(p * 100)} %`;
});

// The sample that spoils the ride most (the server's highest ride score, as in the route
// list), so the big glyph shows the worst weather of the ride. Without scores, the start.
const worstSample = computed(() => {
    const samples = forecast.value.samples;
    let worst = samples[0];
    for (const s of samples) {
        if (s.rideScore != null && (worst?.rideScore == null || s.rideScore > worst.rideScore)) worst = s;
    }
    return worst;
});
const showExplanation = ref(false);
const note =
    "Das Regenrisiko zeigt den höchsten Wert an einem Streckenpunkt, nicht für die ganze Fahrt. " +
    "Regen und Gegenwind zeigen die höchsten erwarteten Werte. " +
    "Wird Regen erwartet, zeigt Regen die Menge, die es voraussichtlich regnet, falls es regnet. " +
    "Der Windaufwand zeigt als Stufe (niedrig bis sehr hoch), wie viel zusätzliche Kraft du für dein Tempo brauchst. Er ist geschätzt. " +
    "Frost zeigt als Stufe (leicht bis stark), wie glatt die Strasse an der kältesten Stelle werden dürfte — " +
    "aus Temperatur, Nässe und Wettercode zusammen.";
</script>

<template>
    <q-card class="column">
        <q-card-section class="q-pb-none row">
            <div class="text-subtitle2">Prognose</div>
            <q-space />
            <q-btn
                flat
                dense
                round
                size="sm"
                :icon="symSharpInfo"
                aria-label="Kennzahlen erklärt"
                @click="showExplanation = true"
            />
        </q-card-section>
        <q-card-section :horizontal="horizontal" class="q-my-auto">
            <q-card-section v-if="worstSample" class="col-auto flex flex-center" :class="{ 'q-pb-none': !horizontal }">
                <WeatherGlyph
                    :weather-code="worstSample.weatherCode"
                    :rain-mm="worstSample.rainMm"
                    :eta="worstSample.eta"
                    :label="headline"
                    size="88px"
                />
            </q-card-section>
            <q-card-section class="col">
                <h2 class="text-h5 text-weight-medium q-mt-none q-mb-xs">{{ headline }}</h2>
                <p v-if="explanation" class="text-body2 q-mb-sm">{{ explanation }}</p>
                <WeatherSections v-if="forecast.sections?.length" :sections="forecast.sections" />
            </q-card-section>
        </q-card-section>
        <q-dialog v-model="showExplanation">
            <q-card>
                <q-card-section class="text-body2">{{ note }}</q-card-section>
                <q-card-actions align="right">
                    <q-btn v-close-popup flat label="Schliessen" color="primary" />
                </q-card-actions>
            </q-card>
        </q-dialog>
    </q-card>
</template>
