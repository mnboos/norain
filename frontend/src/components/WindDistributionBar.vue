<script setup lang="ts">
import { computed } from "vue";
import type { WindDistribution } from "@norain/api/models";
import { windDistributionParts } from "@/utils/wind";

const props = defineProps<{ distribution: WindDistribution }>();
const parts = computed(() => windDistributionParts(props.distribution));
const total = computed(() => parts.value.reduce((sum, p) => sum + p.meters, 0));
const description = computed(() => parts.value.map(p => `${(p.meters / 1000).toFixed(1)} km ${p.label}`).join(" · "));
const colors = ["negative", "primary", "secondary", "blue-grey-3", "grey-7"];
</script>

<template>
    <q-card flat class="text-caption" data-testid="wind-distribution">
        <!-- The card around this names it ("Wind entlang der Strecke"). -->
        <div class="row items-center q-gutter-x-sm">
            <span v-if="total > 0 && distribution.meanFeltSpeed != null" class="text-muted">
                Gefühlt im Mittel {{ distribution.meanFeltSpeed.toFixed(1) }} km/h (geschätzt)
                <span v-if="distribution.feltCoveredM < total - 0.01">
                    · verfügbar auf {{ (distribution.feltCoveredM / 1000).toFixed(1) }} km
                </span>
                <span v-if="distribution.timingSource === 'sample-interpolation'">· Fahrtempo näherungsweise</span>
            </span>
            <span v-else-if="total > 0" class="text-muted">Gefühlter Wind nicht verfügbar.</span>
        </div>
        <template v-if="total > 0">
            <div class="row no-wrap rounded-borders overflow-hidden q-my-xs" role="img" :aria-label="description">
                <q-linear-progress
                    v-for="(part, index) in parts"
                    :key="part.label"
                    :value="1"
                    :color="colors[index]"
                    size="8px"
                    :style="{ width: `${(100 * part.meters) / total}%` }"
                    aria-hidden="true"
                />
            </div>
            <div class="row q-gutter-x-md text-muted">
                <template v-for="(part, index) in parts" :key="part.label">
                    <span v-if="part.meters > 0">
                        <q-badge :color="colors[index]" class="q-mr-xs" />
                        {{ (part.meters / 1000).toFixed(1) }} km {{ part.label }}
                    </span>
                </template>
            </div>
        </template>
        <div v-else>Keine Strecke</div>
    </q-card>
</template>
