<script setup lang="ts">
import type { RouteSection } from "@norain/api";

defineProps<{
    sections: RouteSection[];
}>();

const CONDITION_LABELS: Record<string, string> = {
    dry: "Trocken",
    rain: "Regen",
    heavy_rain: "Starker Regen",
};

const CONDITION_COLORS: Record<string, string> = {
    dry: "bg-green-2",
    rain: "bg-blue-2",
    heavy_rain: "bg-indigo-3",
};
</script>

<template>
    <div class="q-gutter-sm q-mt-md">
        <div class="text-subtitle2 q-mb-sm">Streckenabschnitte</div>
        <div class="row q-col-gutter-sm">
            <div v-for="(section, i) in sections" :key="i" class="col-auto">
                <q-card :class="CONDITION_COLORS[section.condition] || 'bg-grey-2'" style="min-width: 150px">
                    <q-card-section class="q-pa-sm">
                        <div class="text-weight-medium">
                            {{ CONDITION_LABELS[section.condition] || section.condition }}
                        </div>
                        <div class="text-caption">
                            {{ section.startKm }}–{{ section.endKm }} km
                        </div>
                        <div class="text-caption">
                            {{ section.startTime }}–{{ section.endTime }}
                        </div>
                        <div class="text-caption">
                            {{ section.tempMin }}–{{ section.tempMax }}°C
                        </div>
                        <div v-if="section.maxRainMm > 0" class="text-caption">
                            🌧 {{ section.maxRainMm }} mm
                        </div>
                        <div v-if="section.maxHeadwind > 0" class="text-caption">
                            💨 {{ section.maxHeadwind }} km/h
                        </div>
                    </q-card-section>
                </q-card>
            </div>
        </div>
    </div>
</template>
