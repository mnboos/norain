<script setup lang="ts">
import { symSharpAcUnit } from "@quasar/extras/material-symbols-sharp";
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
    <q-card flat class="transparent">
        <q-card-section class="text-caption text-muted q-mb-xs q-pa-none">Wetter entlang der Strecke</q-card-section>
        <q-card-section class="row items-center q-gutter-xs q-pa-none">
            <q-badge
                v-for="(section, i) in sections"
                :key="i"
                outline
                class="q-pa-xs"
                :color="CONDITION_COLORS[section.condition] || 'grey'"
            >
                <q-item-label class="text-weight-medium">
                    <!-- Sections are cut by rain; frost is a second reading on top of that,
                         so it marks the badge instead of renaming it. The level is the
                         server's word - the tooltip below says it in full. -->
                    <q-icon v-if="section.frostLevel" :name="symSharpAcUnit" size="14px" class="q-mr-xs" />
                    {{ CONDITION_LABELS[section.condition] || section.condition }}
                </q-item-label>
                <q-item-label v-if="sections.length > 1" caption>
                    &nbsp;{{ section.startKm }}–{{ section.endKm }} km
                </q-item-label>
                <q-tooltip>
                    {{ section.startTime }}–{{ section.endTime }} · {{ section.tempMin }}–{{ section.tempMax }}°C
                    <template v-if="section.maxRainMm > 0">· Regen: {{ section.maxRainMm }} mm</template>
                    <template v-if="section.maxHeadwind != null">· Gegenwind: {{ section.maxHeadwind }} km/h</template>
                    <template v-if="section.frostLevel">· Frost: {{ section.frostLevel }}</template>
                </q-tooltip>
            </q-badge>
            <q-badge v-if="sections.length" color="primary" outline class="q-pa-xs">
                <q-item-label>
                    {{ Math.min(...sections.map(s => s.tempMin)) }} – {{ Math.max(...sections.map(s => s.tempMax)) }}°C
                </q-item-label>
            </q-badge>
        </q-card-section>
    </q-card>
</template>
