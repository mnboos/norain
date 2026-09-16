<script setup lang="ts">
import { computed, ref, toRefs } from "vue";
import { useQuasar } from "quasar";
import { symSharpInfo } from "@quasar/extras/material-symbols-sharp";
import type { RouteForecastOut } from "@norain/api/models";
import { forecastHeadline, peakRain, swissTime } from "@/utils/forecastDetails";
import WeatherSections from "@/components/WeatherSections.vue";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
const props = defineProps<{ forecast: RouteForecastOut }>();
const { forecast } = toRefs(props);
const showExplanation = ref(false);
// Below md the two sections stack; smaller type, tighter padding and four figures per row keep
// the card from pushing the charts and the map off a phone screen.
const $q = useQuasar();
const compact = computed(() => $q.screen.lt.md);
const headline = computed(() => forecastHeadline(forecast.value.summary, forecast.value.samples));
const rainTime = computed(() =>
    forecast.value.samples.length &&
    forecast.value.summary.firstRainEta &&
    (forecast.value.summary.rainProbability == null || forecast.value.summary.rainProbability > 0)
        ? swissTime(forecast.value.summary.firstRainEta)
        : null,
);

const explanation = computed(() => {
    if (!forecast.value.samples.length) return "Für diese Fahrt liegen noch keine Wetterdaten vor.";
    const p = forecast.value.summary.rainProbability;
    if (p == null) return "Das Regenrisiko ist derzeit nicht verfügbar.";
    if (p < 0.1) return "";
    return `Höchstes Regenrisiko entlang der Strecke: ${Math.round(p * 100)} %`;
});
const peakRate = computed(() => peakRain(forecast.value));
// One compact row of key figures; the fine print lives in the info dialog to keep the card short.
const stats = computed(() => [
    {
        label: "Regenrisiko",
        value:
            forecast.value.summary.rainProbability == null
                ? null
                : Math.round(forecast.value.summary.rainProbability * 100),
        unit: "%",
    },
    { label: "Regen max.", value: peakRate.value, unit: "mm/h" },
    { label: "Gegenwind max.", value: forecast.value.summary.maxHeadwind, unit: "km/h" },
    { label: "Windaufwand max.", value: forecast.value.summary.maxWindEffortLevel ?? null, unit: "" },
    {
        // "kein" and "Nicht verfügbar" are different answers: the first is the forecast
        // saying the road is fine, the second is having no forecast to read.
        label: "Frost",
        value: forecast.value.samples.length ? (forecast.value.summary.maxFrostLevel ?? "kein") : null,
        unit: "",
    },
    { label: "Dauer", value: Math.round(forecast.value.totalSeconds / 60), unit: "min" },
    { label: "Distanz", value: (forecast.value.totalDistanceM / 1000).toFixed(1), unit: "km" },
]);
const note =
    "Das Regenrisiko zeigt den höchsten Wert an einem Streckenpunkt, nicht für die ganze Fahrt. " +
    "Regen und Gegenwind zeigen die höchsten erwarteten Werte. " +
    "Wird Regen erwartet, zeigt Regen die Menge, die es voraussichtlich regnet, falls es regnet. " +
    "Der Windaufwand zeigt als Stufe (niedrig bis sehr hoch), wie viel zusätzliche Kraft du für dein Tempo brauchst. Er ist geschätzt. " +
    "Frost zeigt als Stufe (leicht bis stark), wie glatt die Strasse an der kältesten Stelle werden dürfte — " +
    "aus Temperatur, Nässe und Wettercode zusammen.";
</script>

<template>
    <q-card flat class="row">
        <q-card-section class="col-12 col-md-3" :class="compact ? 'q-px-sm q-py-xs' : 'q-pb-none'">
            <!--            <q-item-label overline :class="rainy ? 'text-warning' : 'text-secondary'">-->
            <!--                <q-badge rounded :color="rainy ? 'warning' : 'secondary'" class="q-mr-sm" />-->
            <!--                {{ status }}-->
            <!--            </q-item-label>-->
            <div class="row items-center no-wrap">
                <h2 class="col text-weight-medium" :class="compact ? 'text-subtitle1 q-my-none' : 'text-h5 q-my-sm'">
                    <template v-if="rainTime">
                        ab ca.
                        <span class="text-primary">{{ rainTime }}</span>
                        Uhr
                    </template>
                    <template v-else>{{ headline }}</template>
                </h2>
                <!-- Compact: the info button moves up here instead of taking a line of its own. -->
                <q-btn
                    v-if="compact"
                    flat
                    dense
                    round
                    size="sm"
                    :icon="symSharpInfo"
                    aria-label="Kennzahlen erklärt"
                    @click="showExplanation = true"
                />
            </div>
            <p v-if="explanation" class="text-caption text-muted" :class="compact ? 'q-mb-xs' : 'q-mb-md'">
                {{ explanation }}
            </p>
            <WeatherSections v-if="forecast.sections?.length" :sections="forecast.sections" />
        </q-card-section>

        <q-card-section
            class="col-12 col-md"
            :class="compact ? 'q-px-sm q-py-xs' : 'q-pb-none'"
            aria-label="Kennzahlen der Fahrt"
        >
            <dl class="row q-mt-none" :class="compact ? 'q-col-gutter-xs q-mb-sm' : 'q-col-gutter-sm q-mb-lg'">
                <div v-for="stat in stats" :key="stat.label" class="col-3 col-md-2">
                    <dt class="text-caption text-uppercase text-muted" :class="{ 'stat-label--compact': compact }">
                        {{ stat.label }}
                    </dt>
                    <dd
                        v-if="stat.value != null"
                        class="q-ma-none text-weight-medium"
                        :class="compact ? 'text-subtitle2' : 'text-h6'"
                    >
                        {{ stat.value }}
                        <small v-if="stat.unit" class="text-caption text-muted q-ml-xs">{{ stat.unit }}</small>
                    </dd>
                    <dd v-else class="q-ma-none text-caption">Nicht verfügbar</dd>
                </div>
            </dl>
            <WindDistributionBar
                v-if="forecast.summary.windDistribution"
                :distribution="forecast.summary.windDistribution"
            />
            <div v-if="!compact" class="row items-center q-gutter-x-xs text-caption text-muted q-mt-sm">
                <q-btn
                    flat
                    dense
                    round
                    size="sm"
                    :icon="symSharpInfo"
                    aria-label="Kennzahlen erklärt"
                    @click="showExplanation = true"
                />
                <span>Maximalwerte · Windaufwand geschätzt</span>
            </div>
            <p v-if="forecast.samples.some(s => s.pop == null)" class="text-caption text-muted">
                Für Teile der Strecke fehlt das Regenrisiko.
            </p>
            <q-dialog v-model="showExplanation">
                <q-card>
                    <q-card-section class="text-body2">{{ note }}</q-card-section>
                    <q-card-actions align="right">
                        <q-btn v-close-popup flat label="Schliessen" color="primary" />
                    </q-card-actions>
                </q-card>
            </q-dialog>
        </q-card-section>
    </q-card>
</template>

<style scoped>
/* Four labels share a phone's width; "Windaufwand max." would otherwise wrap onto three lines. */
.stat-label--compact {
    font-size: 10px;
    line-height: 1.3;
}
</style>
