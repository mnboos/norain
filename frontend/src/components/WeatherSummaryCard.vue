<script setup lang="ts">
import { computed } from "vue";
import { symSharpInfo } from "@quasar/extras/material-symbols-sharp";
import type { RouteForecastOut } from "@norain/api/models";
import { forecastHeadline, peakRisk } from "@/utils/forecastDetails";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
const props = defineProps<{ forecast: RouteForecastOut }>();
const headline = computed(() => forecastHeadline(props.forecast.summary, props.forecast.samples));
const peakRate = computed(() => {
    const rates = props.forecast.samples.flatMap(s => (s.rainRateMmH == null ? [] : [s.rainRateMmH]));
    return rates.length ? `${Math.max(...rates).toFixed(1)} mm/h` : "—";
});
// One compact row of key figures; the fine print lives in the info tooltip to keep the card short.
const stats = computed(() => [
    { label: "Max. Regenrisiko", value: peakRisk(props.forecast) },
    { label: "Max. Intensität", value: peakRate.value },
    { label: "Max. Gegenwind im Abschnitt", value: props.forecast.summary.maxHeadwind == null
        ? "Nicht verfügbar" : `${props.forecast.summary.maxHeadwind} km/h` },
    { label: "Max. Windaufwand (geschätzt)", value: props.forecast.summary.maxWindPowerW == null
        ? "Nicht verfügbar" : props.forecast.summary.maxWindPowerW > 0
            ? `+${Math.round(props.forecast.summary.maxWindPowerW)} W` : "Kein Mehraufwand" },
    { label: "Dauer", value: `${Math.round(props.forecast.totalSeconds / 60)} min` },
    { label: "Distanz", value: `${(props.forecast.totalDistanceM / 1000).toFixed(1)} km` },
]);
const note =
    "Das Regenrisiko gilt am jeweils riskantesten verfügbaren Punkt und dessen Vorhersagestunde. Es ist keine " +
    "Wahrscheinlichkeit für Regen irgendwo auf der gesamten Fahrt. Die Intensität stammt aus der Einzelprognose. " +
    "Max. Gegenwind ist der höchste mittlere Wert eines Wetterabschnitts. Gefühlter Wind nutzt das geschätzte Fahrtempo; örtlicher Windschutz wird nicht berücksichtigt. " +
    "Der Windaufwand schätzt die zusätzliche Leistung, um das geplante Tempo des Routenprofils gegen den Wind zu halten " +
    "(aufrechte Sitzposition, CdA 0,5 m²). Je schneller man fährt, desto mehr kostet derselbe Wind.";
</script>

<template>
    <q-card :class="forecast.summary.willRain ? 'bg-tint-wet' : 'bg-tint-dry'">
        <q-card-section class="q-py-sm">
            <div class="row items-center q-gutter-x-xs">
                <div class="text-subtitle1 text-weight-medium">{{ headline }}</div>
                <q-icon :name="symSharpInfo" size="18px" class="text-muted cursor-pointer" tabindex="0" :aria-label="note">
                    <q-tooltip max-width="320px">{{ note }}</q-tooltip>
                </q-icon>
                <q-space />
                <div class="text-caption text-muted">
                    Daten: {{ forecast.summary.source }}
                    <template v-if="forecast.summary.stationCorrected"> · kurzfristig mit Messstationen abgeglichen</template>
                </div>
            </div>
            <div class="row q-col-gutter-x-lg">
                <div v-for="stat in stats" :key="stat.label" class="col-auto">
                    <div class="text-caption text-muted">{{ stat.label }}</div>
                    <div class="text-subtitle1 text-weight-medium">{{ stat.value }}</div>
                </div>
            </div>
            <div v-if="forecast.samples.some(s => s.pop == null)" class="text-caption">
                Wahrscheinlichkeitsdaten teilweise nicht verfügbar.
            </div>
            <WindDistributionBar v-if="forecast.summary.windDistribution" :distribution="forecast.summary.windDistribution" />
        </q-card-section>
    </q-card>
</template>
