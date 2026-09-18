<script setup lang="ts">
import { computed, ref } from "vue";
import type { RouteForecastOut } from "@norain/api/models";
import { metricLabels, rangeText, swissTime } from "@/utils/forecastDetails";
import { useEntitlements } from "@/composables/useEntitlements";

const props = defineProps<{ forecast: RouteForecastOut; selectedSample: number; showChartKey?: boolean }>();
const emit = defineEmits<{ "update:selectedSample": [index: number] }>();
const expanded = ref(false);
const sample = computed(() => props.forecast.samples[props.selectedSample]);
const uncertainty = computed(() => sample.value?.uncertainty);

const { entitlements } = useEntitlements();
/**
 * The spread is withheld from the free tier rather than missing.
 *
 * Those are different statements and the UI must not conflate them: "Nicht verfügbar"
 * claims we have no data, which would be a lie here. Only say this when the server
 * actually reports the tier — never infer a paywall from absent data.
 */
const uncertaintyLocked = computed(() => entitlements.value?.ensembleUncertainty === false);
const partial = computed(() => props.forecast.uncertaintyPartial);
</script>

<template>
    <q-expansion-item v-model="expanded" dense class="forecast-details q-mb-sm" data-testid="forecast-details">
        <template #header>
            <q-item-section side class="">
                <q-item-label overline class="">Details</q-item-label>
                <!--                <div class="row items-center q-gutter-x-sm">-->
                <!--                    <span class="text-caption text-primary">Details</span>-->
                <!--                    <span v-if="showChartKey" class="text-caption text-muted">-->
                <!--                        Fläche: erwarteter Bereich. Linie auswählen für Details.-->
                <!--                    </span>-->
                <!--                </div>-->
            </q-item-section>
        </template>
        <div class="q-pa-md">
            <p class="text-caption">
                Die Fläche zeigt, wie stark das Wetter schwanken könnte. Auch Werte ausserhalb sind möglich.
            </p>
            <q-banner v-if="uncertaintyLocked" dense class="bg-tint-warn q-mb-md">
                Mögliche Wetterschwankungen siehst du mit Plus. Die Wettervorhersage und das Regenrisiko sind kostenlos.
                <template #action>
                    <q-btn flat dense color="primary" label="Upgrade" to="/account" />
                </template>
            </q-banner>
            <q-banner v-else-if="partial" dense class="bg-tint-warn q-mb-md">
                Für Teile der Strecke fehlen Angaben zu möglichen Wetterschwankungen.
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
                    {{ sample.pop == null ? "Nicht verfügbar" : `${Math.round(sample.pop * 100)}%` }}
                </p>
                <p v-if="!uncertainty && !uncertaintyLocked">Für diesen Punkt ist kein Wetterbereich verfügbar.</p>
                <p v-if="sample.rainRateMmH != null" class="text-caption">
                    Regen: {{ sample.rainRateMmH.toFixed(1) }} mm/h ({{ sample.rainMm.toFixed(1) }} mm in
                    {{ (sample.precipitationIntervalS ?? 3600) / 60 }} min).
                </p>
                <p v-if="uncertainty?.rainIfWet != null" class="text-caption">
                    {{
                        uncertainty.pop === 0
                            ? "Voraussichtlich trocken."
                            : `Falls es regnet: etwa ${uncertainty.rainIfWet.toFixed(1)} mm/h.`
                    }}
                </p>
                <dl v-if="!uncertaintyLocked" class="metric-grid">
                    <template v-for="metric in metricLabels" :key="metric.key">
                        <dt>{{ metric.label }}</dt>
                        <dd>
                            {{ rangeText(uncertainty?.metrics[metric.key], metric.unit) }}
                        </dd>
                    </template>
                </dl>
                <p class="text-caption">Positiver Gegenwind bremst, negative Werte bedeuten Rückenwind.</p>
            </template>
            <p v-else>Keine Wetterdaten für diese Strecke verfügbar.</p>
        </div>
    </q-expansion-item>
</template>

<style scoped>
.sample-slider {
    display: block;
    width: 100%;
    min-height: 44px;
    accent-color: var(--q-primary);
}
.metric-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 8px;
}
dd {
    margin: 0;
}
dt,
dd {
    min-width: 0;
    overflow-wrap: anywhere;
}
</style>
