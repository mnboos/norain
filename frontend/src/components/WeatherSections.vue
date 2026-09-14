<script setup lang="ts">
import type { RouteSection } from "@norain/api/models";

defineProps<{
    sections: RouteSection[];
}>();

const CONDITION_LABELS: Record<string, string> = {
    dry: "Trocken",
    rain: "Regen",
    heavy_rain: "Starker Regen",
};

const CONDITION_COLORS: Record<string, string> = {
    dry: "secondary",
    rain: "primary",
    heavy_rain: "deep-purple",
};
</script>

<template>
    <div>
        <div class="text-caption text-muted q-mb-xs">Streckenabschnitte · Einzelprognose</div>
        <div class="row items-center q-gutter-xs">
            <q-badge
                v-for="(section, i) in sections"
                :key="i"
                outline
                class="q-pa-xs"
                :color="CONDITION_COLORS[section.condition] || 'grey'"
            >
                <span class="text-weight-medium">{{ CONDITION_LABELS[section.condition] || section.condition }}</span>
                &nbsp;{{ section.startKm }}–{{ section.endKm }} km
                <q-tooltip>
                    {{ section.startTime }}–{{ section.endTime }} · {{ section.tempMin }}–{{ section.tempMax }}°C
                    <template v-if="section.maxRainMm > 0">· Regen: {{ section.maxRainMm }} mm</template>
                    <template v-if="section.maxHeadwind != null">· Gegenwind: {{ section.maxHeadwind }} km/h</template>
                </q-tooltip>
            </q-badge>
            <q-badge v-if="sections.length" color="grey" outline class="q-pa-xs">
                {{ Math.min(...sections.map(s => s.tempMin)) }}–{{ Math.max(...sections.map(s => s.tempMax)) }}°C
            </q-badge>
        </div>
    </div>
</template>
