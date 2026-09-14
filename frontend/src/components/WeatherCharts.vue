<script setup lang="ts">
import { computed, defineAsyncComponent } from "vue";
import { useForecastFigures } from "@/queries/forecastParts";
import type { TimedSample } from "@/utils/forecastSelection";

// Loaded lazily so Plotly ends up in its own chunk, fetched only once a forecast is shown.
defineEmits<{ selectSample: [index: number] }>();

const NiceChart = defineAsyncComponent(() => import("./chart/NiceChart.vue"));

const props = defineProps<{ jobId: string; version: string; selectedSample: number; samples: TimedSample[] }>();

// The chart data is not part of the job result either: only pages that draw charts fetch it.
const { data, isPending, isError } = useForecastFigures(
    () => props.jobId,
    () => props.version,
);

// Opaque JSON dicts in the API client; NiceChart treats both `data` and `layout` as optional.
const figures = computed(() => data.value ?? []);

/** The backend draws temperature, precipitation and wind; hold their places while loading. */
const PLACEHOLDER_TILES = 3;
</script>

<template>
    <q-card class="row q-col-gutter-md transparent" flat>
        <template v-if="isPending">
            <div v-for="i in PLACEHOLDER_TILES" :key="i" class="col-12 col-md-4">
                <q-card flat bordered>
                    <q-skeleton square aria-label="Diagramm wird geladen" />
                </q-card>
            </div>
        </template>
        <div v-else-if="isError" class="col-12">
            <q-banner dense class="bg-tint-error rounded-borders">Diagramme konnten nicht geladen werden.</q-banner>
        </div>
        <template v-else>
            <div v-for="(fig, i) in figures" :key="i" class="col-12 col-md-4">
                <q-card flat bordered class="overflow-hidden">
                    <NiceChart
                        :key="`${jobId}:${version}:${i}`"
                        :figure="fig"
                        :selected-sample="selectedSample"
                        :samples="samples"
                        :directional-wind="i === 2"
                        :temperature="i === 0"
                        @select-sample="$emit('selectSample', $event)"
                    />
                </q-card>
            </div>
        </template>
        <div v-if="!isPending && !isError && !figures.length" class="col-12">
            <q-banner dense>Keine Diagrammdaten verfügbar.</q-banner>
        </div>
    </q-card>
</template>
