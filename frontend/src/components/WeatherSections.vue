<script setup lang="ts">
import { symSharpAcUnit } from "@quasar/extras/material-symbols-sharp";
import type { RouteSection } from "@norain/api/models";
import { useI18n } from "vue-i18n";

import { te } from "@/i18n";
import { impactText } from "@/utils/levels";

defineProps<{
    sections: RouteSection[];
}>();

const { t } = useI18n();

function conditionLabel(condition: string): string {
    const key = `sections.${condition}`;
    return te(key) ? t(key) : condition;
}

// Soft tints that follow dark mode (utils/theme.ts), with the text in the matching colour.
const CONDITION_CLASSES: Record<string, string> = {
    dry: "bg-tint-dry text-secondary",
    rain: "bg-tint-wet text-primary",
    heavy_rain: "bg-tint-heavy text-deep-purple",
};
</script>

<template>
    <div class="row items-center">
        <q-chip
            v-for="(section, i) in sections"
            :key="i"
            square
            class="text-weight-medium q-ml-none"
            :class="CONDITION_CLASSES[section.condition] ?? 'bg-tint-neutral'"
        >
            <!-- Sections are cut by rain; frost is a second reading on top of that,
                 so it marks the chip instead of renaming it. The level is the
                 server's word - the tooltip below says it in full. -->
            <q-icon v-if="section.frostLevel" :name="symSharpAcUnit" size="14px" class="q-mr-xs" />
            {{ conditionLabel(section.condition) }}
            <span v-if="sections.length > 1" class="text-caption q-ml-xs">
                {{ section.startKm }}–{{ section.endKm }} km
            </span>
            <q-tooltip>
                {{ section.startTime }}–{{ section.endTime }} · {{ section.tempMin }}–{{ section.tempMax }}°C
                <template v-if="section.maxRainMm > 0">· {{ t("sections.rainMm", { mm: section.maxRainMm }) }}</template>
                <template v-if="section.maxHeadwind != null">
                    · {{ t("sections.headwind", { speed: section.maxHeadwind }) }}
                </template>
                <template v-if="section.frostLevel">· {{ t("map.frost", { level: impactText(section.frostLevel) }) }}</template>
            </q-tooltip>
        </q-chip>
        <q-chip v-if="sections.length" square class="bg-tint-neutral q-ml-none">
            {{ Math.min(...sections.map(s => s.tempMin)) }} – {{ Math.max(...sections.map(s => s.tempMax)) }}°C
        </q-chip>
    </div>
</template>
