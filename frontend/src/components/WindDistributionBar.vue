<script setup lang="ts">
import { computed } from "vue";
import type { WindDistribution } from "@norain/api/models";
import { windDistributionParts } from "@/utils/wind";

const props = defineProps<{ distribution: WindDistribution }>();
const parts = computed(() => windDistributionParts(props.distribution));
const total = computed(() => parts.value.reduce((sum, p) => sum + p.meters, 0));
const description = computed(() => parts.value.map(p => `${(p.meters / 1000).toFixed(1)} km ${p.label}`).join(" · "));
</script>

<template>
    <div class="q-mt-sm text-caption" data-testid="wind-distribution">
        <div class="text-weight-medium">Wind entlang der Strecke</div>
        <template v-if="total > 0">
            <div class="wind-bar" role="img" :aria-label="description">
                <span
v-for="part in parts" :key="part.label"
                      :style="{ width: `${100 * part.meters / total}%`, background: part.color }" />
            </div>
            <div>{{ description }}</div>
            <div v-if="distribution.meanFeltSpeed != null">
                Gefühlt im Mittel {{ distribution.meanFeltSpeed.toFixed(1) }} km/h (geschätzt)
                <span v-if="distribution.feltCoveredM < total - 0.01">
                    · verfügbar auf {{ (distribution.feltCoveredM / 1000).toFixed(1) }} km
                </span>
                <span v-if="distribution.timingSource === 'sample-interpolation'"> · Fahrtempo näherungsweise</span>
            </div>
            <div v-else>Gefühlter Wind nicht verfügbar.</div>
        </template>
        <div v-else>Keine Strecke</div>
    </div>
</template>

<style scoped>
.wind-bar { display: flex; width: 100%; height: 8px; border-radius: 4px; overflow: hidden; margin: 4px 0; }
</style>
