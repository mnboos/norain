<script setup lang="ts">
import { computed, ref, toRefs } from "vue";
import {
    symSharpAcUnit,
    symSharpAir,
    symSharpRainy,
    symSharpSchedule,
    symSharpSpeed,
    symSharpStraighten,
    symSharpThermostat,
    symSharpWaterDrop,
} from "@quasar/extras/material-symbols-sharp";
import type { RouteForecastOut } from "@norain/api/models";
import { meanFeltTemp, peakRain } from "@/utils/forecastDetails";
import { headwindColor, temperatureColor } from "@/utils/statColors";

const props = withDefaults(
    defineProps<{
        forecast: RouteForecastOut;
        /**
         * Tiles per row. Quasar's col-* classes follow the screen width, not the card's, so
         * the parent - which knows how wide it made the card - picks this.
         */
        columns?: 2 | 3 | 4;
    }>(),
    { columns: 3 },
);
const { forecast } = toRefs(props);
const showExplanation = ref(false);

/** The colour of a tile with nothing to flag. */
const NEUTRAL = "blue-grey-6";

const peakRate = computed(() => peakRain(forecast.value));
const feltTemp = computed(() => meanFeltTemp(forecast.value.samples));
const stats = computed(() => [
    {
        label: "Gefühlt Ø",
        icon: symSharpThermostat,
        color: temperatureColor(feltTemp.value) ?? NEUTRAL,
        value: feltTemp.value == null ? null : Math.round(feltTemp.value),
        unit: "°C",
    },
    {
        label: "Regenrisiko",
        icon: symSharpRainy,
        color: "blue-grey-6",
        value:
            forecast.value.summary.rainProbability == null
                ? null
                : Math.round(forecast.value.summary.rainProbability * 100),
        unit: "%",
    },
    {
        label: "Wind\u00adaufwand max.",
        icon: symSharpSpeed,
        color: "blue-grey-6",
        value: forecast.value.summary.maxWindEffortLevel ?? null,
        unit: "",
    },
    {
        label: "Distanz",
        icon: symSharpStraighten,
        color: "blue-grey-6",
        value: (forecast.value.totalDistanceM / 1000).toFixed(1),
        unit: "km",
    },

    {
        // "kein" and "Nicht verfügbar" are different answers: the first is the forecast
        // saying the road is fine, the second is having no forecast to read.
        label: "Frost",
        icon: symSharpAcUnit,
        // Blue only when the server found a frost risk somewhere on the ride.
        color: forecast.value.summary.maxFrostLevel != null ? "light-blue-6" : NEUTRAL,
        value: forecast.value.samples.length ? (forecast.value.summary.maxFrostLevel ?? "kein") : null,
        unit: "",
    },

    {
        label: "Regen max.",
        icon: symSharpWaterDrop,
        color: Number(peakRate.value) > 0 ? "light-blue-7" : NEUTRAL,
        value: peakRate.value,
        unit: "mm/h",
    },

    {
        label: "Gegenwind max.",
        icon: symSharpAir,
        color: headwindColor(forecast.value.summary.maxHeadwind) ?? NEUTRAL,
        value: forecast.value.summary.maxHeadwind,
        unit: "km/h",
    },
    {
        label: "Dauer",
        icon: symSharpSchedule,
        color: "blue-grey-6",
        value: Math.round(forecast.value.totalSeconds / 60),
        unit: "min",
    },
]);
/** The stats cut into table rows of `columns` cells. */
const rows = computed(() =>
    Array.from({ length: Math.ceil(stats.value.length / props.columns) }, (_, i) =>
        stats.value.slice(i * props.columns, (i + 1) * props.columns),
    ),
);
const note =
    "Das Regenrisiko zeigt den höchsten Wert an einem Streckenpunkt, nicht für die ganze Fahrt. " +
    "Regen und Gegenwind zeigen die höchsten erwarteten Werte. " +
    "Wird Regen erwartet, zeigt Regen die Menge, die es voraussichtlich regnet, falls es regnet. " +
    "Der Windaufwand zeigt als Stufe (niedrig bis sehr hoch), wie viel zusätzliche Kraft du für dein Tempo brauchst. Er ist geschätzt. " +
    "Gefühlt ist die Temperatur, die du im Fahrtwind spürst (Windchill bei deinem Tempo), " +
    "gemittelt über die Fahrzeit. " +
    "Frost zeigt als Stufe (leicht bis stark), wie glatt die Strasse an der kältesten Stelle werden dürfte — " +
    "aus Temperatur, Nässe und Wettercode zusammen.";
</script>

<template>
    <q-card>
        <!-- The grid runs to the card's edges; separators draw the lines between the cells. -->
        <q-card-section aria-label="Kennzahlen der Fahrt" class="q-pa-none">
            <template v-for="(row, r) in rows" :key="r">
                <div class="row no-wrap">
                    <template v-for="(stat, c) in row" :key="stat.label">
                        <q-separator v-if="c > 0" vertical />
                        <q-item class="col column flex-center text-center q-px-xs">
                            <!-- Quasar's color prop takes palette names only; a hex goes in style. -->
                            <q-icon
                                :name="stat.icon"
                                size="sm"
                                :color="stat.color.startsWith('#') ? undefined : stat.color"
                                :style="stat.color.startsWith('#') ? { color: stat.color } : undefined"
                            />
                            <q-item-label caption>{{ stat.label }}</q-item-label>
                            <q-item-label class="text-weight-bold">
                                <template v-if="stat.value != null">
                                    {{ stat.value }}
                                    <span v-if="stat.unit">{{ stat.unit }}</span>
                                </template>
                                <template v-else>Nicht verfügbar</template>
                            </q-item-label>
                        </q-item>
                    </template>
                    <!-- Keep a short last row's cells as wide as the others. -->
                    <template v-for="c in columns - row.length" :key="`empty-${c}`">
                        <q-separator vertical />
                        <div class="col" />
                    </template>
                </div>
            </template>
        </q-card-section>
        <template v-if="forecast.samples.some(s => s.pop == null)">
            <q-separator />
            <q-card-section class="text-caption text-muted">
                Für Teile der Strecke fehlt das Regenrisiko.
            </q-card-section>
        </template>
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
