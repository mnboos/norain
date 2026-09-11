<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import type { RouteWeatherOut } from "@norain/api";
import { metricLabels, modelLabel, rangeText, swissTime } from "@/utils/forecastDetails";

const props = defineProps<{ forecast: RouteWeatherOut; selectedSample: number }>();
const emit = defineEmits<{ "update:selectedSample": [index: number] }>();
const expanded = ref(false);
const sample = computed(() => props.forecast.samples[props.selectedSample]);
const uncertainty = computed(() => sample.value?.uncertainty);
const now = ref(Date.now());
const timer = setInterval(() => {
    now.value = Date.now();
}, 60_000);
onBeforeUnmount(() => {
    clearInterval(timer);
});
const age = computed(() =>
    uncertainty.value
        ? Math.max(0, Math.floor((now.value - new Date(uncertainty.value.fetchedAt).getTime()) / 60000))
        : null,
);
const partial = computed(() =>
    props.forecast.samples.some(
        s =>
            !s.uncertainty ||
            s.uncertainty.requestedModels.some(name => !s.uncertainty?.models.some(m => m.model === name)) ||
            metricLabels.some(metric => s.uncertainty?.metrics[metric.key]?.median == null),
    ),
);
const modelRows = computed(() => {
    const u = uncertainty.value;
    return u
        ? [...new Set([...u.requestedModels, ...u.models.map(m => m.model)])].map(name => ({
              name,
              data: u.models.find(m => m.model === name),
          }))
        : [];
});
</script>

<template>
    <q-expansion-item
        v-model="expanded"
        label="Vorhersage-Details"
        dense
        class="forecast-details q-mb-sm"
        data-testid="forecast-details"
    >
        <div class="q-pa-md">
            <p class="text-caption">
                Die Bereiche zeigen das 10.–90. Perzentil der Ensemble-Mitglieder. Sie beschreiben die Modellstreuung,
                keine garantierten Grenzen. Alle verfügbaren Mitglieder zählen gleich; Modelle mit mehr Mitgliedern
                haben mehr Gewicht.
            </p>
            <q-banner v-if="partial" dense class="bg-tint-warn q-mb-md">
                Teilweise Ensemble-Abdeckung: Modelle oder Wettergrössen fehlen an einigen Punkten.
            </q-banner>
            <template v-if="sample">
                <label class="text-weight-medium">
                    Streckenpunkt · {{ swissTime(sample.eta) }} Uhr · {{ Math.round(sample.elapsedS / 60) }} min Fahrt
                    <input
                        class="sample-slider"
                        type="range"
                        min="0"
                        :max="forecast.samples.length - 1"
                        step="1"
                        :value="selectedSample"
                        aria-label="Streckenpunkt"
                        :aria-valuetext="`${swissTime(sample.eta)} Uhr`"
                        @input="emit('update:selectedSample', Number(($event.target as HTMLInputElement).value))"
                    />
                </label>
                <p class="text-caption">
                    Regenrisiko am Punkt:
                    {{ sample.pop == null ? "Nicht verfügbar" : `${Math.round(sample.pop * 100)}%` }} · Quelle:
                    {{ sample.probabilitySource ?? "Keine Wahrscheinlichkeitsdaten" }}
                </p>
                <p v-if="uncertainty" class="text-caption">
                    Ensemble-Zeitpunkt: {{ swissTime(uncertainty.forecastTime) }} Uhr · Abgerufen vor {{ age }} min.
                    Niederschlag gilt für die vorhergehende Stunde. Ein nasses Mitglied meldet mindestens 0.1 mm.
                </p>
                <p v-else>Keine Ensemble-Bereiche für diesen Punkt verfügbar.</p>
                <p v-if="sample.rainRateMmH != null" class="text-caption">
                    Einzelprognose: {{ sample.rainRateMmH.toFixed(1) }} mm/h ({{ sample.rainMm.toFixed(1) }} mm in
                    {{ (sample.precipitationIntervalS ?? 3600) / 60 }} min).
                </p>
                <p v-if="uncertainty?.rainIfWet != null" class="text-caption">
                    {{
                        uncertainty.pop === 0
                            ? "Keine nassen Mitglieder."
                            : `Wenn nass: im Mittel ${uncertainty.rainIfWet.toFixed(1)} mm/h.`
                    }}
                </p>
                <dl class="metric-grid">
                    <template v-for="metric in metricLabels" :key="metric.key">
                        <dt>{{ metric.label }}</dt>
                        <dd>
                            {{ rangeText(uncertainty?.metrics[metric.key], metric.unit) }}
                            <small>· {{ uncertainty?.metrics[metric.key]?.memberCount ?? 0 }} Mitglieder</small>
                        </dd>
                    </template>
                </dl>
                <p class="text-caption">Positiver Gegenwind bremst, negative Werte bedeuten Rückenwind.</p>
                <div
                    v-if="uncertainty"
                    class="model-table"
                    tabindex="0"
                    aria-label="Modellvergleich, horizontal scrollbar"
                >
                    <table>
                        <caption>Modellvergleich am ausgewählten Punkt · Bereiche und Median</caption>
                        <thead>
                            <tr>
                                <th scope="col">Modell</th>
                                <th scope="col">Regenrisiko</th>
                                <th v-for="metric in metricLabels" :key="metric.key" scope="col">{{ metric.label }}</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr v-for="row in modelRows" :key="row.name">
                                <th scope="row">{{ modelLabel(row.name) }}</th>
                                <td>
                                    {{
                                        row.data?.pop == null ? "Nicht verfügbar" : `${Math.round(row.data.pop * 100)}%`
                                    }}
                                </td>
                                <td v-for="metric in metricLabels" :key="metric.key">
                                    {{ rangeText(row.data?.metrics[metric.key], metric.unit) }}
                                    <small>{{ row.data?.metrics[metric.key]?.memberCount ?? 0 }} Mitglieder</small>
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </template>
            <p v-else>Keine Wetterdaten für diese Strecke verfügbar.</p>
        </div>
    </q-expansion-item>
</template>

<style scoped>
.forecast-details {
    min-width: 0;
    width: 100%;
    max-width: 100%;
}
.sample-slider {
    display: block;
    width: 100%;
    min-height: 44px;
    accent-color: var(--q-primary);
}
.metric-grid {
    display: grid;
    grid-template-columns: minmax(100px, 1fr) 2fr;
    gap: 8px;
}
dd {
    margin: 0;
}
.model-table {
    overflow-x: auto;
    max-width: 100%;
}
table {
    border-collapse: collapse;
    font-size: 12px;
    width: 100%;
}
caption {
    text-align: left;
    padding: 8px 0;
}
th,
td {
    text-align: left;
    padding: 8px;
    border-bottom: 1px solid #ddd;
    min-width: 130px;
}
td small {
    display: block;
}
</style>
