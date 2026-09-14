<script setup lang="ts">
import { computed, ref } from "vue";
import { symSharpInfo } from "@quasar/extras/material-symbols-sharp";
import type { RouteForecastOut } from "@norain/api/models";
import { forecastHeadline, swissTime } from "@/utils/forecastDetails";
import WeatherSections from "@/components/WeatherSections.vue";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
const props = defineProps<{ forecast: RouteForecastOut }>();
const width = ref(0);
const showExplanation = ref(false);
const wide = computed(() => width.value >= 1024);
const metricColumns = computed(() => (wide.value ? 6 : width.value >= 480 ? 3 : 2));
const headline = computed(() => forecastHeadline(props.forecast.summary, props.forecast.samples));
const rainTime = computed(() =>
    props.forecast.samples.length &&
    props.forecast.summary.firstRainEta &&
    (props.forecast.summary.rainProbability == null || props.forecast.summary.rainProbability > 0)
        ? swissTime(props.forecast.summary.firstRainEta)
        : null,
);
const status = computed(() =>
    rainTime.value
        ? props.forecast.summary.rainProbability == null
            ? "Regen in der Einzelprognose"
            : "Regen möglich"
        : "Vorhersage",
);
const explanation = computed(() => {
    if (!props.forecast.samples.length) return "Für diese Fahrt liegen noch keine Wetterdaten vor.";
    const p = props.forecast.summary.rainProbability;
    if (p == null) return "Keine Wahrscheinlichkeitsdaten verfügbar. Die Einschätzung basiert auf der Einzelprognose.";
    return `Am riskantesten verfügbaren Punkt beträgt das Regenrisiko ${Math.round(p * 100)} %. Die Einzelprognose kann davon abweichen.`;
});
const peakRate = computed(() => {
    const rates = props.forecast.samples.flatMap(s => (s.rainRateMmH == null ? [] : [s.rainRateMmH]));
    return rates.length ? Math.max(...rates).toFixed(1) : null;
});
// One compact row of key figures; the fine print lives in the info tooltip to keep the card short.
const stats = computed(() => [
    {
        label: "Regenrisiko",
        value:
            props.forecast.summary.rainProbability == null
                ? null
                : Math.round(props.forecast.summary.rainProbability * 100),
        unit: "%",
    },
    { label: "Intensität max.", value: peakRate.value, unit: "mm/h" },
    { label: "Gegenwind max.", value: props.forecast.summary.maxHeadwind, unit: "km/h" },
    {
        label: "Windaufwand max.",
        value:
            props.forecast.summary.maxWindPowerW == null
                ? null
                : props.forecast.summary.maxWindPowerW > 0
                  ? `+${Math.round(props.forecast.summary.maxWindPowerW)}`
                  : "0",
        unit: "W",
    },
    { label: "Dauer", value: Math.round(props.forecast.totalSeconds / 60), unit: "min" },
    { label: "Distanz", value: (props.forecast.totalDistanceM / 1000).toFixed(1), unit: "km" },
]);
const note =
    "Das Regenrisiko gilt am jeweils riskantesten verfügbaren Punkt und dessen Vorhersagestunde. Es ist keine " +
    "Wahrscheinlichkeit für Regen irgendwo auf der gesamten Fahrt. Die Intensität stammt aus der Einzelprognose. " +
    "Max. Gegenwind ist der höchste mittlere Wert eines Wetterabschnitts. Gefühlter Wind nutzt das geschätzte Fahrtempo; örtlicher Windschutz wird nicht berücksichtigt. " +
    "Der Windaufwand schätzt die zusätzliche Leistung, um das geplante Tempo des Routenprofils gegen den Wind zu halten " +
    "(aufrechte Sitzposition, CdA 0,5 m²). Je schneller man fährt, desto mehr kostet derselbe Wind.";
</script>

<template>
    <q-card flat>
        <q-resize-observer @resize="size => (width = size.width)" />
        <div class="row">
            <q-card-section class="q-pa-lg" :class="wide ? 'col-3' : 'col-12'">
                <div
                    class="text-overline"
                    :class="
                        forecast.summary.rainProbability || forecast.summary.willRain
                            ? 'text-warning'
                            : 'text-secondary'
                    "
                >
                    <q-badge
                        rounded
                        :color="forecast.summary.rainProbability || forecast.summary.willRain ? 'warning' : 'secondary'"
                        class="q-mr-sm"
                    />
                    {{ status }}
                </div>
                <h2 v-if="rainTime" class="text-h5 text-weight-medium q-my-sm">
                    ab ca.
                    <span class="text-primary text-weight-medium">{{ rainTime }}</span>
                    Uhr
                </h2>
                <h2 v-else class="text-h5 text-weight-medium q-my-sm">{{ headline }}</h2>
                <p class="text-caption q-mb-md" :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'">
                    {{ explanation }}
                </p>
                <WeatherSections v-if="forecast.sections?.length" :sections="forecast.sections" />
            </q-card-section>
            <q-separator :vertical="wide" :class="wide ? '' : 'full-width'" />
            <q-card-section class="q-pa-lg" :class="wide ? 'col' : 'col-12'" aria-label="Kennzahlen der Fahrt">
                <dl class="row q-col-gutter-sm q-mb-lg">
                    <div
                        v-for="(stat, index) in stats"
                        :key="stat.label"
                        :class="`col-${12 / metricColumns}`"
                        class="row no-wrap"
                    >
                        <q-separator v-if="index % metricColumns !== 0" vertical class="q-mr-sm" />
                        <div class="col">
                            <dt
                                class="text-caption text-uppercase"
                                :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                            >
                                {{ stat.label }}
                            </dt>
                            <dd v-if="stat.value != null" class="q-ma-none text-h6 text-weight-medium">
                                {{ stat.value }}
                                <small
                                    class="text-caption q-ml-xs"
                                    :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                                >
                                    {{ stat.unit }}
                                </small>
                            </dd>
                            <dd v-else class="q-ma-none text-caption">Nicht verfügbar</dd>
                        </div>
                    </div>
                </dl>
                <WindDistributionBar
                    v-if="forecast.summary.windDistribution"
                    :distribution="forecast.summary.windDistribution"
                />
                <div
                    class="row items-center q-gutter-x-xs text-caption q-mt-sm"
                    :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                >
                    <q-btn
                        flat
                        dense
                        round
                        size="sm"
                        :icon="symSharpInfo"
                        aria-label="Kennzahlen erklärt"
                        @click="showExplanation = true"
                    />
                    <q-dialog v-model="showExplanation">
                        <q-card>
                            <q-card-section class="text-body2">{{ note }}</q-card-section>
                            <q-card-actions align="right">
                                <q-btn v-close-popup flat label="Schliessen" color="primary" />
                            </q-card-actions>
                        </q-card>
                    </q-dialog>
                    <span>Maximalwerte · Windaufwand geschätzt</span>
                </div>
                <p
                    v-if="forecast.samples.some(s => s.pop == null)"
                    class="text-caption"
                    :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                >
                    Wahrscheinlichkeitsdaten teilweise nicht verfügbar.
                </p>
            </q-card-section>
        </div>
    </q-card>
</template>
