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
    dry: "bg-tint-dry",
    rain: "bg-tint-wet",
    heavy_rain: "bg-tint-heavy",
};
</script>

<template>
    <!-- One-line chips instead of tall cards, so the sections cost a single row of height. -->
    <div class="row items-center q-gutter-xs q-mb-xs">
        <span class="text-caption text-muted q-mr-xs">Streckenabschnitte</span>
        <div
            v-for="(section, i) in sections"
            :key="i"
            class="section-chip rounded-borders text-caption"
            :class="CONDITION_COLORS[section.condition] || 'bg-tint-neutral'"
        >
            <span class="text-weight-medium">{{ CONDITION_LABELS[section.condition] || section.condition }}</span>
            · {{ section.startKm }}–{{ section.endKm }} km · {{ section.startTime }}–{{ section.endTime }} ·
            {{ section.tempMin }}–{{ section.tempMax }}°C
            <template v-if="section.maxRainMm > 0"> · 🌧 {{ section.maxRainMm }} mm</template>
            <template v-if="section.maxHeadwind > 0"> · 💨 {{ section.maxHeadwind }} km/h</template>
        </div>
    </div>
</template>

<style scoped>
.section-chip {
    padding: 2px 8px;
}
</style>
